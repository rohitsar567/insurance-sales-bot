"""Regression tests for KI-102 / KI-112 / KI-117 / KI-118 — profile-RAG safety.

KI-118 (2026-05-15) rewrites the threat model. Profile chunks are NO LONGER
keyed by session_id; they are keyed by `name_slug` (canonical user name)
and only NAMED users ever get embedded. Anonymous chats never write to
Chroma — the corruption surface that the session_id keying introduced
(the legacy `profile_anonymous` dangling row that poisoned every query
with a `where` clause referencing session_id) is now structurally
unreachable.

The remaining safety guarantees these tests pin:

  KI-102 / KI-118 — main retrieval cosine pass MUST NOT return profile
                    chunks (`where={'doc_type': {'$ne': 'profile'}}`).
                    Profile chunks are exclusively surfaced via the
                    explicit per-name lookup
                    `collection.get(ids=[f'profile_{name_slug}'])`.

  KI-118.a — upsert_profile_chunk stamps `name_slug` into Chroma metadata.
  KI-118.b — retrieve()'s per-name lookup gates on metadata.name_slug ==
             caller's slug (triple-check), so cross-name leakage is blocked.

  KI-112 — input guards: empty/None name_slug refused; mis-shaped embeddings
           refused. These remain in place.

  KI-107 — retrieve() with a missing/non-existent profile must NEVER raise.

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
    semantically-similar vectors. Two profile chunks (one per named user)
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
# Test cases — KI-118 threat model. Profile chunks keyed by name_slug.
# ---------------------------------------------------------------------------


class TestProfileIsolation(unittest.TestCase):
    """KI-118 — anonymous sessions never write to Chroma; named user A's
    profile must NEVER surface in named user B's retrieve."""

    def setUp(self):
        self.coll = _make_ephemeral_collection()
        self.name_a = f"alice_{uuid.uuid4().hex[:4]}"
        self.name_b = f"bob_{uuid.uuid4().hex[:4]}"

    def _seed_profile(self, name_slug: str, text: str) -> None:
        """Write a profile chunk for `name_slug` directly to the test
        collection, mirroring what upsert_profile_chunk does in prod."""
        vec = asyncio.run(_StubEmbedder().embed([text]))[0]
        chunk_id = f"profile_{name_slug}"
        self.coll.add(
            ids=[chunk_id],
            documents=[text],
            embeddings=[vec],
            metadatas=[{
                "policy_id": chunk_id,
                "insurer_slug": "profile",
                "policy_name": f"User profile ({name_slug[:16]})",
                "doc_type": "profile",
                "name_slug": name_slug,  # KI-118.a — stamped at write time
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

    def _run_retrieve(self, query: str, profile_name_slug: str, top_k: int = 5):
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
                profile_name_slug=profile_name_slug,
            ))

    # -----------------------------------------------------------------
    # CASE 1 — pre-fix leak repro: named user A's profile must NOT show
    # up in named user B's retrieved context.
    # -----------------------------------------------------------------
    def test_name_a_profile_never_leaks_into_name_b(self):
        self._seed_profile(
            self.name_a,
            "USER CONTEXT — facts about the person asking this question:\n"
            "- Age: 45 years.\n- User's own pre-existing conditions: diabetes, hypertension.",
        )
        self._seed_profile(
            self.name_b,
            "USER CONTEXT — facts about the person asking this question:\n"
            "- Age: 28 years.\n- First-time buyer; no existing health insurance.",
        )
        # Add a generic policy chunk so there's something to retrieve.
        self._seed_policy("hdfc_ergo_optima_secure_v1", "Standard health policy text about waiting periods.")

        # User B asks a profile-flavoured query
        chunks = self._run_retrieve(
            query="what plan suits my age and dependents",
            profile_name_slug=self.name_b,
        )

        leaked = [c for c in chunks if c.policy_id == f"profile_{self.name_a}"]
        self.assertEqual(
            leaked, [],
            f"PRIVACY LEAK: user A's profile chunk surfaced in user B's "
            f"retrieval. Found: {[c.policy_id for c in chunks]}",
        )

    # -----------------------------------------------------------------
    # CASE 2 — named user B's OWN profile must still surface (positive path).
    # -----------------------------------------------------------------
    def test_name_b_own_profile_is_surfaced(self):
        self._seed_profile(
            self.name_b,
            "USER CONTEXT — facts about the person asking this question:\n"
            "- Age: 28 years.",
        )
        self._seed_policy("test_policy_1", "Generic policy text.")

        chunks = self._run_retrieve(
            query="recommend a plan for me",
            profile_name_slug=self.name_b,
        )
        own = [c for c in chunks if c.policy_id == f"profile_{self.name_b}"]
        self.assertEqual(
            len(own), 1,
            f"User B should see its OWN profile chunk. Got: {[c.policy_id for c in chunks]}",
        )

    # -----------------------------------------------------------------
    # CASE 3 — multiple foreign profiles + one own profile. Only the
    # current user's chunk may be present.
    # -----------------------------------------------------------------
    def test_three_foreign_profiles_none_leak(self):
        for name in ["alice_1", "carol_2", "dave_3"]:
            self._seed_profile(
                name,
                f"USER CONTEXT — facts about the person asking this question:\n"
                f"- Age: {30 + len(name)} years.\n- Health conditions: PII for {name}.",
            )
        self._seed_profile(
            self.name_b,
            "USER CONTEXT — facts about the person asking this question:\n- Age: 28 years.",
        )
        self._seed_policy("test_policy_2", "Generic policy text.")

        chunks = self._run_retrieve(
            query="my age health conditions dependents",
            profile_name_slug=self.name_b,
            top_k=10,
        )
        profile_pids = [c.policy_id for c in chunks if c.doc_type == "profile"]
        # Only ONE profile chunk may appear, and it must be name_b's
        self.assertEqual(
            profile_pids, [f"profile_{self.name_b}"],
            f"Foreign profile leaked. profile chunks in result: {profile_pids}",
        )

    # -----------------------------------------------------------------
    # CASE 4 — legacy chunk without name_slug metadata is refused even
    # if its id happens to match (defence-in-depth from KI-118.b).
    # -----------------------------------------------------------------
    def test_legacy_chunk_without_name_slug_metadata_is_refused(self):
        # Write a chunk under id 'profile_<name_b>' but with NO
        # name_slug field (simulating a pre-KI-118 legacy row).
        vec = asyncio.run(_StubEmbedder().embed(["USER CONTEXT — legacy"]))[0]
        chunk_id = f"profile_{self.name_b}"
        self.coll.add(
            ids=[chunk_id],
            documents=["USER CONTEXT — legacy row from before KI-118 deploy"],
            embeddings=[vec],
            metadatas=[{
                "policy_id": chunk_id,
                "insurer_slug": "profile",
                "policy_name": "legacy profile",
                "doc_type": "profile",
                # No 'name_slug' — simulating pre-fix state
                "source_url": "",
                "page_start": 0,
                "page_end": 0,
                "chunk_idx": 0,
            }],
        )
        self._seed_policy("test_policy_3", "Generic policy text.")

        chunks = self._run_retrieve(
            query="anything",
            profile_name_slug=self.name_b,
        )
        # Legacy chunk must be refused — the triple-check at retrieve's
        # per-name lookup gates on metadata.name_slug match.
        legacy_hits = [c for c in chunks if c.policy_id == chunk_id]
        self.assertEqual(
            legacy_hits, [],
            "Legacy profile chunk without name_slug metadata must be refused. "
            f"Got: {[c.policy_id for c in chunks]}",
        )

    # -----------------------------------------------------------------
    # CASE 5 — KI-118 core invariant: anonymous calls (no profile_name_slug)
    # produce NO profile chunks at all, even if the collection contains
    # foreign profile rows that match the query.
    # -----------------------------------------------------------------
    def test_anonymous_retrieve_never_surfaces_any_profile_chunk(self):
        # Seed two named-user profiles
        self._seed_profile(self.name_a, "USER CONTEXT — Age: 45 years.")
        self._seed_profile(self.name_b, "USER CONTEXT — Age: 28 years.")
        self._seed_policy("test_policy_anon", "Generic policy text.")

        # Anonymous call — no profile_name_slug
        from rag import retrieve as retrieve_mod
        retrieve_mod._RETRIEVAL_CACHE.clear()
        with mock.patch.object(retrieve_mod, "get_collection", return_value=self.coll):
            chunks = asyncio.run(retrieve_mod.retrieve(
                query="my age health conditions",
                top_k=5,
                embedder=_StubEmbedder(),
                profile_name_slug=None,
            ))
        profile_chunks = [c for c in chunks if c.doc_type == "profile"]
        self.assertEqual(
            profile_chunks, [],
            "PRIVACY LEAK: anonymous retrieve surfaced a profile chunk. "
            f"Got: {[c.policy_id for c in chunks]}",
        )


# ---------------------------------------------------------------------------
# KI-107 (2026-05-15) — graceful handling of Chroma get(ids=[missing]).
# C5 port-in persona saw 3× HTTP 500 "Error executing plan: Internal error:
# Error finding id". After KI-102 added the per-session profile-chunk
# lookup, retrieve() runs collection.get(ids=[f"profile_{slug}"]) on EVERY
# named query — and for new users (no profile saved yet) and certain
# Chroma sqlite states, that call can raise. These tests pin the contract:
# retrieve() with a never-existed name_slug must NEVER raise and must
# NEVER return a profile chunk.
# ---------------------------------------------------------------------------


class TestRetrieveSurvivesMissingProfileId(unittest.TestCase):
    """KI-107 — retrieve(profile_name_slug=...) must be exception-safe across:
    (1) first-time named users with no profile chunk yet,
    (2) Chroma.get raising on the per-name lookup,
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

    def _run_retrieve(self, query: str, profile_name_slug: str, top_k: int = 5):
        from rag import retrieve as retrieve_mod
        retrieve_mod._RETRIEVAL_CACHE.clear()
        with mock.patch.object(retrieve_mod, "get_collection", return_value=self.coll):
            return asyncio.run(retrieve_mod.retrieve(
                query=query,
                top_k=top_k,
                embedder=_StubEmbedder(),
                profile_name_slug=profile_name_slug,
            ))

    def test_retrieve_with_never_existed_name_does_not_raise(self):
        """First-time named user, no profile saved yet — must return policy
        chunks without raising. Pre-KI-107 this surfaced as HTTP 500 "Error
        finding id" because get(ids=[missing]) raised + bare except: pass
        let downstream code index into a None result."""
        self._seed_one_policy()

        # Should not raise
        chunks = self._run_retrieve(
            query="what is the waiting period for cataract",
            profile_name_slug="never_existed_name_xyz",
        )

        # No profile chunk should appear (none was ever written)
        profile_chunks = [c for c in chunks if c.doc_type == "profile"]
        self.assertEqual(
            profile_chunks, [],
            f"never-existed name should not produce profile chunks. "
            f"Got: {[(c.chunk_id, c.doc_type) for c in chunks]}",
        )
        # But main cosine retrieval must still work
        self.assertGreater(
            len(chunks), 0,
            "main cosine pass should still return the seeded policy chunk.",
        )

    def test_retrieve_handles_chroma_get_raising_on_per_name_lookup(self):
        """Simulate the worst case: Chroma raises on the per-name profile
        lookup (e.g. transient sqlite lock during compaction). retrieve()
        must still return main cosine results, not 500."""
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
                profile_name_slug="some_name_slug",
            ))

        # Main cosine still works → at least the seeded policy chunk returns
        self.assertGreater(
            len(chunks), 0,
            "retrieve() should fall back to main cosine results when "
            "the per-name profile lookup raises.",
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
# upsert builds metadata containing name_slug.
# ---------------------------------------------------------------------------


class TestUpsertStampsNameSlug(unittest.TestCase):
    """KI-118.a — upsert_profile_chunk MUST write name_slug into the
    chunk's Chroma metadata. Without it, the retrieve filter can't
    distinguish user A's profile from user B's."""

    def test_upsert_writes_name_slug_to_metadata(self):
        from backend import profile_rag

        captured: dict = {}

        class _FakeColl:
            def add(self, ids, documents, embeddings, metadatas):
                captured["ids"] = ids
                captured["metadatas"] = metadatas

            def delete(self, where=None):
                captured["deleted_where"] = where

        class _FakeEmbedder:
            # KI-112 (2026-05-15) — the upsert path now validates that the
            # embedding length matches embedder.dimension. Set both to 384 so
            # the realistic-shape vector passes the shape check; the test's
            # subject under scrutiny is the metadata stamping, not the shape
            # guard (those have dedicated cases below).
            dimension = 384

            async def embed(self, texts, input_type="document"):
                return [[0.1] * 384 for _ in texts]

        fake_coll = _FakeColl()
        slug = f"alice_{uuid.uuid4().hex[:6]}"
        profile = {
            "age": 32,
            "dependents": "self_spouse",
            "health_conditions": [],
            "existing_cover_inr": 500000,
        }

        with mock.patch.object(profile_rag, "_get_collection", return_value=fake_coll), \
             mock.patch("backend.providers.local_embeddings.LocalEmbeddings", _FakeEmbedder):
            asyncio.run(profile_rag.upsert_profile_chunk(slug, profile))

        self.assertIn("metadatas", captured, "upsert never called coll.add")
        meta = captured["metadatas"][0]
        self.assertEqual(
            meta.get("name_slug"), slug,
            f"profile chunk metadata missing name_slug. Got: {meta}",
        )
        self.assertEqual(meta.get("doc_type"), "profile")
        self.assertEqual(captured["ids"], [f"profile_{slug}"])


# ---------------------------------------------------------------------------
# KI-112 (2026-05-15) — input validation hardening (still applies post-KI-118).
#
# Root cause of the historical HNSW corruption: KI-102's initial deploy wrote
# a profile chunk under id "profile_anonymous" with NO session_id metadata.
# That legacy chunk poisoned every subsequent collection.query() that
# referenced session_id or doc_type$ne in the where clause — Chroma's plan
# executor raised "Error finding id" against the dangling row's HNSW pointer.
#
# KI-118 moved the key from session_id to name_slug; the guards are the same.
# These tests pin the contract: bad inputs MUST be rejected at write time,
# not silently corrupt the index for future users.
# ---------------------------------------------------------------------------


class TestUpsertRejectsBadInputs(unittest.TestCase):
    """KI-112 / KI-118 — bad name_slug or embedding shape must NOT reach Chroma."""

    def test_upsert_rejects_empty_name_slug(self):
        from backend import profile_rag

        captured: dict = {"add_called": False}

        class _FakeColl:
            def add(self, ids, documents, embeddings, metadatas):
                captured["add_called"] = True

            def delete(self, where=None):
                captured["delete_called"] = True

        class _FakeEmbedder:
            dimension = 384

            async def embed(self, texts, input_type="document"):
                return [[0.1] * 384 for _ in texts]

        for bad_slug in ["", "   ", None]:
            with mock.patch.object(profile_rag, "_get_collection", return_value=_FakeColl()), \
                 mock.patch("backend.providers.local_embeddings.LocalEmbeddings", _FakeEmbedder):
                captured["add_called"] = False
                asyncio.run(profile_rag.upsert_profile_chunk(bad_slug, {"age": 30}))
            self.assertFalse(
                captured["add_called"],
                f"upsert MUST refuse name_slug={bad_slug!r} — bad write would "
                "corrupt the policies collection.",
            )

    def test_upsert_rejects_mismatched_embedding_dim(self):
        """If the embedder somehow returns the wrong dim (model drift,
        misconfig), upsert must NOT write it to Chroma."""
        from backend import profile_rag

        captured: dict = {"add_called": False}

        class _FakeColl:
            def add(self, ids, documents, embeddings, metadatas):
                captured["add_called"] = True

            def delete(self, where=None):
                pass

        class _BadDimEmbedder:
            dimension = 384

            async def embed(self, texts, input_type="document"):
                # Wrong dim — 8-dim stub like other tests, but profile_rag
                # expects 384.
                return [[0.1] * 8 for _ in texts]

        with mock.patch.object(profile_rag, "_get_collection", return_value=_FakeColl()), \
             mock.patch("backend.providers.local_embeddings.LocalEmbeddings", _BadDimEmbedder):
            asyncio.run(profile_rag.upsert_profile_chunk(
                "valid_slug_xyz", {"age": 30, "dependents": "self"},
            ))

        self.assertFalse(
            captured["add_called"],
            "upsert MUST refuse a mis-shaped embedding to prevent HNSW "
            "corruption from a model drift event.",
        )

    def test_upsert_rejects_none_in_embedding(self):
        from backend import profile_rag

        captured: dict = {"add_called": False}

        class _FakeColl:
            def add(self, ids, documents, embeddings, metadatas):
                captured["add_called"] = True

            def delete(self, where=None):
                pass

        class _NoneVecEmbedder:
            dimension = 384

            async def embed(self, texts, input_type="document"):
                vec = [0.1] * 384
                vec[42] = None  # one None value
                return [vec for _ in texts]

        with mock.patch.object(profile_rag, "_get_collection", return_value=_FakeColl()), \
             mock.patch("backend.providers.local_embeddings.LocalEmbeddings", _NoneVecEmbedder):
            asyncio.run(profile_rag.upsert_profile_chunk(
                "valid_slug_xyz", {"age": 30, "dependents": "self"},
            ))

        self.assertFalse(
            captured["add_called"],
            "upsert MUST refuse a vector containing None values.",
        )

    def test_upsert_accepts_correct_shape(self):
        """Positive path — a well-formed 384-dim list must be persisted."""
        from backend import profile_rag

        captured: dict = {}

        class _FakeColl:
            def add(self, ids, documents, embeddings, metadatas):
                captured["ids"] = ids
                captured["embeddings"] = embeddings

            def delete(self, where=None):
                pass

        class _GoodEmbedder:
            dimension = 384

            async def embed(self, texts, input_type="document"):
                return [[0.1] * 384 for _ in texts]

        with mock.patch.object(profile_rag, "_get_collection", return_value=_FakeColl()), \
             mock.patch("backend.providers.local_embeddings.LocalEmbeddings", _GoodEmbedder):
            asyncio.run(profile_rag.upsert_profile_chunk(
                "good_slug", {"age": 30, "dependents": "self"},
            ))

        self.assertEqual(captured.get("ids"), ["profile_good_slug"])
        self.assertEqual(len(captured["embeddings"][0]), 384)


if __name__ == "__main__":
    unittest.main()
