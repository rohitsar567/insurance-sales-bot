"""Regression tests for KI-102 — profile-RAG cross-session privacy leak.

Pre-fix bug (caught by live 15-persona smoke test 2026-05-15):
    1. User A saves profile → upsert_profile_chunk(session_id=A) writes a
       chunk to the shared 'policies' Chroma collection with metadata
       {policy_id: 'profile_A', doc_type: 'profile'} — NO session_id field.
    2. User B sends a chat → retrieve(query, session_id=B) runs the main
       cosine pass with no doc_type filter, so user A's profile chunk is
       a candidate for top-k by raw cosine.
    3. If user B's query is profile-shaped (age / dependents / health),
       user A's profile chunk surfaces in B's context and the LLM cites
       it as B's "User profile" — leaking A's facts into B's reply.

Three fixes ship together:
    KI-102.a — upsert_profile_chunk stamps session_id into Chroma metadata,
               so the retrieve path can filter by it.
    KI-102.b — retrieve()'s main cosine pass now passes
               where={'doc_type': {'$ne': 'profile'}} so NO profile chunk
               can ever surface via the cosine path. Profile chunks are
               exclusively surfaced via the explicit per-session
               collection.get(ids=[f'profile_{session_id}']) lookup.
    KI-102.c — that per-session lookup gates on metadata.session_id ==
               current session_id (triple-check) so even an ID collision
               or legacy chunk without session_id metadata cannot leak.

These tests run WITHOUT touching a real LLM / network. We stub the
embedder and use chromadb's in-memory ephemeral client to verify the
retrieve path's filter behaviour end-to-end.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_profile_rag_isolation.py -v
"""

from __future__ import annotations

import asyncio
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# In-memory Chroma + stub embedder. We avoid touching the real
# settings.VECTORS_DIR so tests don't pollute the prod collection.
# ---------------------------------------------------------------------------


def _make_ephemeral_collection():
    """Return a fresh in-memory Chroma collection named 'policies'."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    client = chromadb.EphemeralClient(
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    # Use a unique name per call so parallel tests don't share state.
    return client.get_or_create_collection(
        name=f"policies_{uuid.uuid4().hex[:8]}",
        metadata={"hnsw:space": "cosine"},
    )


class _StubEmbedder:
    """Deterministic 8-dim embedder so semantically-similar text gets
    semantically-similar vectors. Two profile chunks (one for each session)
    will end up with near-identical embeddings, which is exactly what
    triggers the pre-fix leak in the wild."""

    async def embed(self, texts, input_type="document"):
        # Hash-based but stable: every "USER CONTEXT" doc maps near the same
        # region of the unit sphere; that's the realistic case where two
        # users' profile chunks both look like profile chunks to cosine.
        vecs = []
        for t in texts:
            base = [0.0] * 8
            if "USER CONTEXT" in t or "profile" in t.lower():
                # All profile-flavoured text lands near vector [1, 0, ...]
                base = [1.0, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            elif "age" in t.lower() or "dependents" in t.lower():
                base = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            else:
                # Generic policy text is far from the profile cluster
                base = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            vecs.append(base)
        return vecs


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestProfileIsolation(unittest.TestCase):
    """KI-102 — session A's profile must NEVER surface in session B's retrieve."""

    def setUp(self):
        self.coll = _make_ephemeral_collection()
        self.session_a = f"sessA_{uuid.uuid4().hex[:6]}"
        self.session_b = f"sessB_{uuid.uuid4().hex[:6]}"

    def _seed_profile(self, session_id: str, text: str) -> None:
        """Write a profile chunk for `session_id` directly to the test
        collection, mirroring what upsert_profile_chunk does in prod."""
        vec = asyncio.run(_StubEmbedder().embed([text]))[0]
        chunk_id = f"profile_{session_id}"
        self.coll.add(
            ids=[chunk_id],
            documents=[text],
            embeddings=[vec],
            metadatas=[{
                "policy_id": chunk_id,
                "insurer_slug": "profile",
                "policy_name": f"User profile (session {session_id[:8]})",
                "doc_type": "profile",
                "session_id": session_id,  # KI-102.a — stamped at write time
                "source_url": "",
                "page_start": 0,
                "page_end": 0,
                "chunk_idx": 0,
                "local_path": "in-memory test profile",
            }],
        )

    def _seed_policy(self, policy_id: str, text: str) -> None:
        """Seed a generic non-profile chunk so the main retrieval pass
        isn't empty (otherwise we're not actually testing the filter)."""
        vec = asyncio.run(_StubEmbedder().embed([text]))[0]
        self.coll.add(
            ids=[policy_id],
            documents=[text],
            embeddings=[vec],
            metadatas=[{
                "policy_id": policy_id,
                "insurer_slug": "test-insurer",
                "policy_name": "Test Policy",
                "doc_type": "policy",
                "source_url": "",
                "page_start": 1,
                "page_end": 1,
                "chunk_idx": 0,
            }],
        )

    def _run_retrieve(self, query: str, session_id: str, top_k: int = 5):
        """Invoke rag.retrieve.retrieve() with the test collection +
        stub embedder patched in."""
        from rag import retrieve as retrieve_mod
        # Clear the in-process cache so each test sees a fresh execution
        retrieve_mod._RETRIEVAL_CACHE.clear()
        with mock.patch.object(retrieve_mod, "get_collection", return_value=self.coll):
            return asyncio.run(retrieve_mod.retrieve(
                query=query,
                top_k=top_k,
                embedder=_StubEmbedder(),
                session_id=session_id,
            ))

    # -----------------------------------------------------------------
    # CASE 1 — pre-fix leak repro: session A's profile must NOT show up
    # in session B's retrieved context.
    # -----------------------------------------------------------------
    def test_session_a_profile_never_leaks_into_session_b(self):
        self._seed_profile(
            self.session_a,
            "USER CONTEXT — facts about the person asking this question:\n"
            "- Age: 45 years.\n- User's own pre-existing conditions: diabetes, hypertension.",
        )
        self._seed_profile(
            self.session_b,
            "USER CONTEXT — facts about the person asking this question:\n"
            "- Age: 28 years.\n- First-time buyer; no existing health insurance.",
        )
        # Add a generic policy chunk so there's something to retrieve.
        self._seed_policy("hdfc_ergo_optima_secure_v1", "Standard health policy text about waiting periods.")

        # Session B asks a profile-flavoured query
        chunks = self._run_retrieve(
            query="what plan suits my age and dependents",
            session_id=self.session_b,
        )

        leaked = [c for c in chunks if c.policy_id == f"profile_{self.session_a}"]
        self.assertEqual(
            leaked, [],
            f"PRIVACY LEAK: session A's profile chunk surfaced in session B's "
            f"retrieval. Found: {[c.policy_id for c in chunks]}",
        )

    # -----------------------------------------------------------------
    # CASE 2 — session B's OWN profile must still surface (positive path).
    # -----------------------------------------------------------------
    def test_session_b_own_profile_is_surfaced(self):
        self._seed_profile(
            self.session_b,
            "USER CONTEXT — facts about the person asking this question:\n"
            "- Age: 28 years.",
        )
        self._seed_policy("test_policy_1", "Generic policy text.")

        chunks = self._run_retrieve(
            query="recommend a plan for me",
            session_id=self.session_b,
        )
        own = [c for c in chunks if c.policy_id == f"profile_{self.session_b}"]
        self.assertEqual(
            len(own), 1,
            f"Session B should see its OWN profile chunk. Got: {[c.policy_id for c in chunks]}",
        )

    # -----------------------------------------------------------------
    # CASE 3 — multiple foreign profiles + one own profile. Only the
    # current session's chunk may be present.
    # -----------------------------------------------------------------
    def test_three_foreign_profiles_none_leak(self):
        for sid in ["smokeA_1", "ki100_ve", "smokeB_B2"]:
            self._seed_profile(
                sid,
                f"USER CONTEXT — facts about the person asking this question:\n"
                f"- Age: {30 + len(sid)} years.\n- Health conditions: PII for {sid}.",
            )
        self._seed_profile(
            self.session_b,
            "USER CONTEXT — facts about the person asking this question:\n- Age: 28 years.",
        )
        self._seed_policy("test_policy_2", "Generic policy text.")

        chunks = self._run_retrieve(
            query="my age health conditions dependents",
            session_id=self.session_b,
            top_k=10,
        )
        profile_pids = [c.policy_id for c in chunks if c.doc_type == "profile"]
        # Only ONE profile chunk may appear, and it must be session_b's
        self.assertEqual(
            profile_pids, [f"profile_{self.session_b}"],
            f"Foreign profile leaked. profile chunks in result: {profile_pids}",
        )

    # -----------------------------------------------------------------
    # CASE 4 — legacy chunk without session_id metadata is refused even
    # if its id happens to match (defence-in-depth from KI-102.c).
    # -----------------------------------------------------------------
    def test_legacy_chunk_without_session_id_metadata_is_refused(self):
        # Write a chunk under id 'profile_<session_b>' but with NO
        # session_id field (simulating a pre-fix legacy row).
        vec = asyncio.run(_StubEmbedder().embed(["USER CONTEXT — legacy"]))[0]
        chunk_id = f"profile_{self.session_b}"
        self.coll.add(
            ids=[chunk_id],
            documents=["USER CONTEXT — legacy row from before KI-102 deploy"],
            embeddings=[vec],
            metadatas=[{
                "policy_id": chunk_id,
                "insurer_slug": "profile",
                "policy_name": "legacy profile",
                "doc_type": "profile",
                # No 'session_id' — simulating pre-fix state
                "source_url": "",
                "page_start": 0,
                "page_end": 0,
                "chunk_idx": 0,
            }],
        )
        self._seed_policy("test_policy_3", "Generic policy text.")

        chunks = self._run_retrieve(
            query="anything",
            session_id=self.session_b,
        )
        # Legacy chunk must be refused — the triple-check at retrieve's
        # per-session lookup gates on metadata.session_id match.
        legacy_hits = [c for c in chunks if c.policy_id == chunk_id]
        self.assertEqual(
            legacy_hits, [],
            "Legacy profile chunk without session_id metadata must be refused. "
            f"Got: {[c.policy_id for c in chunks]}",
        )


# ---------------------------------------------------------------------------
# KI-107 (2026-05-15) — graceful handling of Chroma get(ids=[missing]).
# C5 port-in persona saw 3× HTTP 500 "Error executing plan: Internal error:
# Error finding id". After KI-102 added the per-session profile-chunk
# lookup, retrieve() runs collection.get(ids=[f"profile_{sid}"]) on EVERY
# query — and for new sessions (no profile saved yet) and certain Chroma
# sqlite states, that call can raise. The bare `except: pass` in the
# pre-KI-107 code masked the failure into the orchestrator's plan executor.
# These tests pin the contract: retrieve() with a never-existed session_id
# must NEVER raise and must NEVER return a profile chunk.
# ---------------------------------------------------------------------------


class TestRetrieveSurvivesMissingProfileId(unittest.TestCase):
    """KI-107 — retrieve(session_id=...) must be exception-safe across:
    (1) brand-new sessions with no profile chunk yet,
    (2) Chroma.get raising on the per-session lookup,
    (3) Chroma.get returning empty lists for missing ids."""

    def setUp(self):
        self.coll = _make_ephemeral_collection()

    def _seed_one_policy(self):
        """Seed a single policy chunk so the main cosine pass returns something."""
        vec = asyncio.run(_StubEmbedder().embed(["Standard policy text."]))[0]
        self.coll.add(
            ids=["policy_seed_1"],
            documents=["Standard policy text about waiting periods."],
            embeddings=[vec],
            metadatas=[{
                "policy_id": "policy_seed_1",
                "insurer_slug": "test-insurer",
                "policy_name": "Test Policy",
                "doc_type": "policy",
                "source_url": "",
                "page_start": 1,
                "page_end": 1,
                "chunk_idx": 0,
            }],
        )

    def _run_retrieve(self, query: str, session_id: str, top_k: int = 5):
        from rag import retrieve as retrieve_mod
        retrieve_mod._RETRIEVAL_CACHE.clear()
        with mock.patch.object(retrieve_mod, "get_collection", return_value=self.coll):
            return asyncio.run(retrieve_mod.retrieve(
                query=query,
                top_k=top_k,
                embedder=_StubEmbedder(),
                session_id=session_id,
            ))

    def test_retrieve_with_never_existed_session_does_not_raise(self):
        """New session, no profile saved yet — must return policy chunks
        without raising. Pre-KI-107 this surfaced as HTTP 500 "Error
        finding id" because get(ids=[missing]) raised + bare except: pass
        let downstream code index into a None result."""
        self._seed_one_policy()

        # Should not raise
        chunks = self._run_retrieve(
            query="what is the waiting period for cataract",
            session_id="never_existed_session_xyz",
        )

        # No profile chunk should appear (none was ever written)
        profile_chunks = [c for c in chunks if c.doc_type == "profile"]
        self.assertEqual(
            profile_chunks, [],
            f"never-existed session should not produce profile chunks. "
            f"Got: {[(c.chunk_id, c.doc_type) for c in chunks]}",
        )
        # But main cosine retrieval must still work
        self.assertGreater(
            len(chunks), 0,
            "main cosine pass should still return the seeded policy chunk.",
        )

    def test_retrieve_handles_chroma_get_raising_on_per_session_lookup(self):
        """Simulate the worst case: Chroma raises on the per-session
        profile lookup (e.g. transient sqlite lock during compaction).
        retrieve() must still return main cosine results, not 500."""
        self._seed_one_policy()

        # Wrap the real collection so .get() raises but .query() works
        real_coll = self.coll

        class _RaisingGetWrapper:
            def __init__(self, inner):
                self._inner = inner

            def query(self, *args, **kwargs):
                return self._inner.query(*args, **kwargs)

            def get(self, *args, **kwargs):
                raise RuntimeError("Error finding id: simulated chroma failure")

        wrapped = _RaisingGetWrapper(real_coll)

        from rag import retrieve as retrieve_mod
        retrieve_mod._RETRIEVAL_CACHE.clear()
        with mock.patch.object(retrieve_mod, "get_collection", return_value=wrapped):
            # Must NOT raise — _safe_collection_get swallows + logs
            chunks = asyncio.run(retrieve_mod.retrieve(
                query="what is the waiting period",
                top_k=5,
                embedder=_StubEmbedder(),
                session_id="some_session_id",
            ))

        # Main cosine still works → at least the seeded policy chunk returns
        self.assertGreater(
            len(chunks), 0,
            "retrieve() should fall back to main cosine results when "
            "the per-session profile lookup raises.",
        )
        # No profile chunk surfaced
        self.assertEqual(
            [c for c in chunks if c.doc_type == "profile"], [],
            "raising get() must NOT produce a profile chunk in the result.",
        )

    def test_safe_collection_get_returns_none_on_exception(self):
        """Unit-test the _safe_collection_get helper directly."""
        from rag.retrieve import _safe_collection_get

        class _Raiser:
            def get(self, **kw):
                raise RuntimeError("Error finding id")

        result = _safe_collection_get(_Raiser(), ids=["x"], include=["documents"])
        self.assertIsNone(
            result,
            "_safe_collection_get must return None on exception, not re-raise.",
        )

    def test_safe_collection_get_returns_empty_dict_on_miss(self):
        """When ids miss but Chroma returns empty lists (the normal case),
        _safe_collection_get returns the raw dict — caller decides what to
        do with empty lists (the truthiness check filters them out)."""
        from rag.retrieve import _safe_collection_get

        result = _safe_collection_get(
            self.coll,
            ids=["definitely_does_not_exist"],
            include=["documents", "metadatas"],
        )
        # Should return a dict (not None), with empty ids list
        self.assertIsNotNone(result, "missing-id get must not return None")
        self.assertEqual(result.get("ids"), [], "missing id should yield empty ids list")


# ---------------------------------------------------------------------------
# Standalone upsert metadata test — no Chroma client; just verify the
# upsert builds metadata containing session_id.
# ---------------------------------------------------------------------------


class TestUpsertStampsSessionId(unittest.TestCase):
    """KI-102.a — upsert_profile_chunk MUST write session_id into the
    chunk's Chroma metadata. Without it, the retrieve filter can't
    distinguish session A's profile from session B's."""

    def test_upsert_writes_session_id_to_metadata(self):
        from backend import profile_rag

        captured: dict = {}

        class _FakeColl:
            def add(self, ids, documents, embeddings, metadatas):
                captured["ids"] = ids
                captured["metadatas"] = metadatas

            def delete(self, where=None):
                captured["deleted_where"] = where

        class _FakeEmbedder:
            async def embed(self, texts, input_type="document"):
                return [[0.1] * 8 for _ in texts]

        fake_coll = _FakeColl()
        sid = f"test_{uuid.uuid4().hex[:6]}"
        profile = {
            "age": 32,
            "dependents": "self_spouse",
            "health_conditions": [],
            "existing_cover_inr": 500000,
        }

        with mock.patch.object(profile_rag, "_get_collection", return_value=fake_coll), \
             mock.patch("backend.providers.local_embeddings.LocalEmbeddings", _FakeEmbedder):
            asyncio.run(profile_rag.upsert_profile_chunk(sid, profile))

        self.assertIn("metadatas", captured, "upsert never called coll.add")
        meta = captured["metadatas"][0]
        self.assertEqual(
            meta.get("session_id"), sid,
            f"profile chunk metadata missing session_id. Got: {meta}",
        )
        self.assertEqual(meta.get("doc_type"), "profile")
        self.assertEqual(captured["ids"], [f"profile_{sid}"])


if __name__ == "__main__":
    unittest.main()
