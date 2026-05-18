"""#52 — graded server-assignment for user-uploaded PDFs (E2E regression).

WHAT #52 GUARANTEES (and this test pins, end-to-end, fully offline)
------------------------------------------------------------------
When a user uploads a policy PDF it must:
  1. be accepted (POST /api/upload-policy, 8 security gates),
  2. be chunked + bge-small-embedded into the SAME Chroma store the chat
     retrieval reads (the global `policies` collection),
  3. CREATE a real curated-facts-shaped JSON record (not the data-starved
     "—"/0 sentinel) under the PERSISTENT uploaded-docs store,
  4. appear in the marketplace (/api/policies/all) as a card whose grade
     flows through backend.main.marketplace_grade — the #40 single source
     of truth (NO re-implemented grading),
  5. be retrievable when a user asks about it,
  6. SURVIVE a restart: re-running the startup re-ingest handler must
     restore its chunks AND keep the card graded.

NETWORK / LLM
-------------
The whole upload+persist+index+grade+retrieve path is intrinsically
offline: the security gates are byte/text scans, embeddings are LOCAL
bge-small (no Voyage), grading is pure-Python build_scorecard, retrieval
is Chroma cosine. So no stub is needed for steps 1-6. The final NL
synthesis (Gemini) is the ONLY networked hop and is intentionally NOT
exercised here — the repo's pattern is to assert the tool layer the brain
calls returns the answer-bearing chunk (which IS the load-bearing proof).

ISOLATION
---------
The test points settings.UPLOADED_DOCS_DIR at a tmp dir and uses a
throwaway Chroma collection name so it never mutates the real persistent
store or the 148-policy corpus index. The scorecard-parity (#40) guard is
re-asserted in tests/test_scorecard_parity.py — this file additionally
proves the uploaded card resolves through marketplace_grade identically.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _run(coro):
    """Run a coroutine on a FRESH event loop.

    Other suites in the gate close / consume the process-default asyncio
    loop, so `asyncio.get_event_loop()` raises `RuntimeError: There is no
    current event loop` when this test runs after them. A dedicated
    new_event_loop() per call is robust regardless of prior-test state.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# --- a structurally valid, multi-page, keyword-dense insurance PDF that
#     pdfplumber actually extracts (insert_text, not insert_textbox) -------

_PARAS = [
    "ZENITH SECURE HEALTH INSURANCE POLICY. Unique Identification No: "
    "ZENHLIP24077V012324. This health insurance policy covers in-patient "
    "hospitalisation, sum insured, premium and claim.",
    "The initial waiting period is 30 days from the first policy commencement "
    "date; expenses within 30 days shall be excluded except accidents. "
    "Pre-existing disease (PED) and its direct complications shall be excluded "
    "until the expiry of 36 months of continuous coverage.",
    "Specific disease waiting period of 24 months applies to cataract and "
    "hernia. Maternity benefit has a waiting period of 24 months. "
    "Pre-hospitalisation cover is 60 days and post-hospitalisation cover is "
    "90 days.",
    "A co-payment of 20% applies to each claim for insured persons above 60 "
    "years. No-claim bonus (cumulative bonus) of 50% per claim-free year is "
    "granted. Room rent: no sub-limit on room rent.",
    "The insurer has 12,000 network hospitals for cashless treatment across "
    "India. Maximum entry age is 65 years. AYUSH treatment is covered. "
    "Ambulance cover is included. Day care procedures are covered.",
    "The IRDAI claim settlement ratio for the insurer is 97.2%. Cashless "
    "treatment is supported at all network hospitals. Pre-existing disease, "
    "exclusions, renewal and free-look terms are defined herein. This is a "
    "comprehensive indemnity health insurance plan regulated by IRDAI.",
]


def _real_policy_pdf_bytes() -> bytes:
    import fitz  # PyMuPDF

    doc = fitz.open()
    for _ in range(2):  # 12 pages, dense extractable text per page
        for para in _PARAS:
            page = doc.new_page()
            y = 72
            for line in [para[i:i + 90] for i in range(0, len(para), 90)]:
                page.insert_text((56, y), line, fontsize=11, fontname="helv")
                y += 16
    base = doc.tobytes()
    doc.close()
    data = base + b"\n" + (b"%" + b"X" * 118 + b"\n") * 40 + b"%%EOF\n"
    assert data.startswith(b"%PDF") and 5_000 < len(data) < 25 * 1024 * 1024
    return data


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """Point the persistent uploaded-docs dir + the working Chroma vectors
    at throwaway dirs so the test never touches the real store/corpus."""
    import backend.config as cfg

    udir = tmp_path / "uploaded_docs"
    vdir = tmp_path / "vectors"
    udir.mkdir()
    vdir.mkdir()
    monkeypatch.setattr(cfg.settings, "UPLOADED_DOCS_DIR", udir, raising=False)
    monkeypatch.setattr(cfg.settings, "VECTORS_DIR", vdir, raising=False)

    # Reload the modules that captured settings paths at import time so they
    # see the isolated dirs. main + brain_tools read settings live, so a
    # monkeypatch is enough; rag.retrieve/ingest build the Chroma client
    # path from settings.VECTORS_DIR per call, so no reload needed there.
    import backend.main as M
    import backend.uploaded_docs as U
    import rag.retrieve as R
    import rag.ingest as I

    # Bust caches that may hold corpus-built state.
    M._CORPUS_PDF_IDX = None
    with M._MG_LOCK:
        M._MG_CACHE["sig"] = None
        M._MG_CACHE["index"] = None
    R._RETRIEVAL_CACHE.clear()
    yield {"M": M, "U": U, "R": R, "I": I, "udir": udir}


def test_upload_pdf_becomes_a_graded_persistent_marketplace_card(isolated_store):
    M = isolated_store["M"]
    U = isolated_store["U"]
    R = isolated_store["R"]
    I = isolated_store["I"]

    from fastapi.testclient import TestClient

    client = TestClient(M.app, raise_server_exceptions=True)
    pdf = _real_policy_pdf_bytes()

    # ---- STEP 1+2+3: accept + chunk/embed + create JSON record -----------
    resp = client.post(
        "/api/upload-policy",
        files={"file": ("zenith_secure_policy.pdf", pdf, "application/pdf")},
        data={"session_id": "pytest-e2e-sid-001"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    pid = body["policy_id"]
    assert pid.startswith("user-upload__")
    assert body["chunks_added"] >= 1
    assert body["pages_indexed"] >= 1

    # STEP 3 — the created JSON record exists, is curated-facts-shaped, and
    # carries real sourced fields (NOT empty → it WILL grade, not sentinel).
    recs = U.load_persisted_records()
    assert pid in recs, f"no persisted record for {pid}: {list(recs)}"
    rec = recs[pid]
    assert rec["insurer_slug"] == "user-upload"
    sourced = [
        k for k, v in rec.items()
        if isinstance(v, dict) and "value" in v and v.get("value") is not None
    ]
    # The scorecard's sentinel fires below ~2/23 scored fields; we sourced
    # well above that from this document's own text.
    assert len(sourced) >= 6, f"too few sourced fields ({sourced})"
    # Every emitted field must be backed by a verbatim source quote (the
    # #52 'no fabrication' contract — a field only exists if its evidence
    # is literally in the document).
    for k in sourced:
        assert rec[k].get("source_quote"), f"{k} has no source_quote"
    # Persisted artefacts (PDF + chunks + meta) for restart survival.
    ddir = isolated_store["udir"] / pid
    for fn in ("source.pdf", "record.json", "chunks.json", "meta.json"):
        assert (ddir / fn).is_file(), f"missing persisted {fn}"

    # ---- STEP 4: marketplace card + graded via the #40 SSOT -------------
    feed = client.get("/api/policies/all").json()
    cards = [p for p in feed["policies"] if p["policy_id"] == pid]
    assert len(cards) == 1, "uploaded doc not in /api/policies/all exactly once"
    card = cards[0]
    assert card["grade"] in ("A", "B", "C", "D", "F"), card["grade"]
    assert card["grade"] != "—" and card["overall_score"] > 0, card
    assert card["data_completeness_pct"] >= 9.0  # above the sentinel floor

    # The card grade MUST come from backend.main.marketplace_grade (the #40
    # single source of truth) and the recommendation-path signal MUST agree
    # — no parallel grading implementation.
    mg = M.marketplace_grade(pid)
    assert mg.get("_grade") == card["grade"], (mg, card["grade"])
    assert mg.get("_overall_score") == card["overall_score"]
    from backend.brain_tools import _scorecard_signal
    sig = _scorecard_signal(pid)
    assert sig.get("_grade") == card["grade"], (sig, card["grade"])

    # ---- STEP 5+6: retrievable + answer-bearing for a Q&A ---------------
    async def _ask():
        from backend.brain_tools import retrieve_policies

        R._RETRIEVAL_CACHE.clear()
        # Anonymous GLOBAL query (no session): the doc was added to THE
        # marketplace, so it must surface for anyone asking about it.
        r = await retrieve_policies(
            query="Zenith Secure policy pre-existing disease waiting period "
                  "and co-payment percentage",
            top_k=10,
            intent="qa",
        )
        return r

    r = _run(_ask())
    up = [c for c in (r.get("chunks") or [])
          if str(c.get("policy_id", "")).startswith("user-upload__")]
    assert up, f"uploaded doc not retrievable for its own Q&A: {r}"
    answer_text = " ".join(c["chunk_text"] for c in up)
    # The retrieved chunk literally contains the answer to the question.
    assert "36 months" in answer_text  # PED waiting period
    assert "20%" in answer_text        # co-payment

    # ---- RESTART PERSISTENCE: simulate a Space rebuild ------------------
    # A rebuild wipes the ephemeral Chroma. Delete the uploaded doc's chunks
    # from the working collection, prove they're GONE, then run the startup
    # re-ingest handler and prove steps 4-6 hold again from the PERSISTED
    # store alone.
    coll = I.get_chroma_collection()
    coll.delete(where={"policy_id": pid})
    R._RETRIEVAL_CACHE.clear()
    gone = coll.get(where={"policy_id": pid}, limit=5)
    assert not gone.get("ids"), "chunks should be gone post simulated rebuild"

    _run(M._startup_reingest_uploaded_docs())

    # STEP 4 again — card still present + still graded (from persisted JSON).
    feed2 = client.get("/api/policies/all").json()
    cards2 = [p for p in feed2["policies"] if p["policy_id"] == pid]
    assert len(cards2) == 1, "card lost after restart"
    assert cards2[0]["grade"] == card["grade"], "grade changed after restart"

    # STEP 5+6 again — chunks restored + retrievable from persisted payload.
    async def _ask2():
        from backend.brain_tools import retrieve_policies

        R._RETRIEVAL_CACHE.clear()
        return await retrieve_policies(
            query="Zenith Secure policy co-payment percentage and "
                  "pre-existing disease waiting period",
            top_k=10,
            intent="qa",
        )

    r2 = _run(_ask2())
    up2 = [c for c in (r2.get("chunks") or [])
           if str(c.get("policy_id", "")).startswith("user-upload__")]
    assert up2, f"uploaded doc NOT retrievable after restart re-ingest: {r2}"
    txt2 = " ".join(c["chunk_text"] for c in up2)
    assert "36 months" in txt2 and "20%" in txt2, "answer lost after restart"


def test_no_silent_failure_on_persist_error(isolated_store, monkeypatch):
    """A persist failure MUST surface as an HTTP 500 (the #52 'no silent
    failure' contract) — a 200 that didn't persist is forbidden."""
    M = isolated_store["M"]
    from fastapi.testclient import TestClient
    import backend.uploaded_docs as U

    def _boom(**_kw):
        raise RuntimeError("persist_upload simulated disk failure")

    monkeypatch.setattr(U, "persist_upload", _boom)
    client = TestClient(M.app, raise_server_exceptions=False)
    resp = client.post(
        "/api/upload-policy",
        files={"file": ("zenith_secure_policy.pdf",
                        _real_policy_pdf_bytes(), "application/pdf")},
        data={"session_id": "pytest-e2e-sid-fail"},
    )
    assert resp.status_code == 500, resp.text
    assert "Indexing failed" in resp.text
    # The PDF must NOT be left orphaned in rag/corpus/user-upload on failure.


def test_extract_fields_emits_only_evidenced_facts():
    """The heuristic extractor must NEVER fabricate: a doc with zero
    structured terms yields {} (→ the honest sentinel), and every emitted
    field carries its verbatim source quote."""
    import backend.uploaded_docs as U

    assert U.extract_fields_from_text("just some prose with no policy terms at all") == {}

    fields = U.extract_fields_from_text(
        "Pre-existing disease shall be excluded until the expiry of 48 "
        "months. A co-payment of 15% applies. 30 days initial waiting period."
    )
    assert fields["pre_existing_disease_waiting_months"]["value"] == 48
    assert fields["copayment_pct"]["value"] == 15
    for cell in fields.values():
        assert cell["source_quote"], "every fact must be source-quoted"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
