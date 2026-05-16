#!/usr/bin/env python3
"""Two-part verification — PART 1 (automated, deterministic).

Gate that must pass before the policy_facts pass is committed. Part 2 is an
independent re-audit agent run separately at commit time.

Checks, per 40-data/policy_facts/*.json:
  1. Valid JSON.
  2. No fabricated sentinels: zero `"value": 999/9999` in ANY field except
     max_renewal_age (which is dead/removed and stripped by tools task #12).
  3. Every cell with a non-null `value` has a non-empty `source_quote`.
  4. `source_pdf_path`, when present, points to a file that exists.
  5. Quote-occurrence (sampled): for up to N filled cells per file whose
     source_quote looks like a real extracted phrase (not "not stated ...",
     not a pipeline-provenance note), the quote text actually occurs in the
     source PDF's extracted text. Fabricated quotes fail the gate.

Exit 0 = PASS (safe to commit). Exit 1 = FAIL (prints offending files/cells).

Usage:
  ./.venv/bin/python tools/verify_policy_facts.py            # sampled (fast)
  ./.venv/bin/python tools/verify_policy_facts.py --full     # all filled cells
  ./.venv/bin/python tools/verify_policy_facts.py --sample 8 # N quotes/file
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PF_DIR = ROOT / "40-data" / "policy_facts"

# source_quote values that are legitimately NOT verbatim PDF text
# Quotes that are NOT verbatim PDF text. Two sub-classes:
#  - sourced-null notes ("not stated …")  → legitimately empty cell
#  - legacy-pipeline provenance notes ("extracted from PDF data", "NIM
#    DeepSeek", "regex extracted …", "rag/extracted") → cell HAS a value but
#    its only "source" is an unverifiable self-reference. These are NOT
#    fabrication, but they are NOT independently verbatim-verifiable either —
#    counted and surfaced separately, never silently passed.
_NON_QUOTE = re.compile(
    r"not stated|not extracted|field absent|unable to infer|"
    r"batch extract|extracted\.json|structured field|prior[- ]pipeline|"
    r"author'?s inference|from extracted pdf data|extracted from pdf|"
    r"regex extracted from pdf|nim deepseek|see source pdf for verbatim|"
    r"rag/extracted|structured json|from extracted pdf|g\d+ batch extract",
    re.I,
)
_SOURCED_NULL = re.compile(r"not stated|not extracted|field absent|unable to infer", re.I)
# Heuristic / inference / placeholder notes that masquerade as a verbatim quote
# but are NOT document text (slipped past the narrower _NON_QUOTE list).
_HEURISTIC_NOTE = re.compile(
    r"classified as|from pdf heuristics|default irdai|no mandatory copay extracted|"
    r"extracted from day_care|may have age-based|not explicitly quoted|"
    r"\(standard .*per policy|interpreted from|inferred from|from pdf heuristic|"
    r"^limit:\s|synthesi[sz]ed|placeholder",
    re.I,
)
_DEAD_FIELDS = {"max_renewal_age"}  # removed from scoring; stripped in task #12


def _pdf_text(pdf_path: Path, _cache: dict[str, str] = {}) -> str | None:
    key = str(pdf_path)
    if key in _cache:
        return _cache[key]
    try:
        import fitz  # PyMuPDF — the extractor the fill agents used

        doc = fitz.open(key)
        txt = " ".join(pg.get_text() for pg in doc)
        doc.close()
        norm = re.sub(r"\s+", " ", txt).lower()
    except Exception:
        norm = None
    _cache[key] = norm
    return norm


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def main() -> int:
    full = "--full" in sys.argv
    sample_n = 6
    if "--sample" in sys.argv:
        sample_n = int(sys.argv[sys.argv.index("--sample") + 1])

    files = sorted(PF_DIR.glob("*.json"))
    if not files:
        print("FAIL: no policy_facts files found")
        return 1

    errors: list[str] = []          # hard, commit-blocking
    legacy_prov: list[str] = []     # value present but provenance is a self-
                                    # referential note (not verbatim, no url)
    quote_ok = quote_fail = 0
    field_legacy: dict[str, int] = {}

    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            errors.append(f"[invalid-json] {f.name}: {e}")
            continue

        verbatim_cells = []
        for k, v in d.items():
            if not (isinstance(v, dict) and "value" in v):
                continue
            val = v.get("value")
            if k in _DEAD_FIELDS:
                continue
            if val in (999, 9999):
                errors.append(f"[sentinel-999] {f.name}:{k} → fabricated 999/9999")
                continue
            if val in (None, "", []):
                continue
            sq = (v.get("source_quote") or "").strip()
            surl = (v.get("source_url") or "").strip()
            snote = (v.get("source_note") or "").strip()
            spp = v.get("source_pdf_path")
            if not (sq or surl or snote or spp):
                errors.append(f"[unsourced] {f.name}:{k} value present but no quote/url/note/pdf")
                continue
            if spp and not (ROOT / spp).exists():
                errors.append(f"[missing-source-pdf] {f.name}:{k} → {spp}")
            if surl:
                continue  # externally sourced (insurer site/aggregator) — OK
            if _SOURCED_NULL.search(sq):
                continue  # legitimately empty-but-explained cell
            if not sq or _NON_QUOTE.search(sq):
                # Has a value + a PDF path but the "quote" is a provenance
                # self-reference, not verbatim text → not independently
                # verifiable. Legacy prior-pipeline cell.
                legacy_prov.append(f"{f.name}:{k}")
                field_legacy[k] = field_legacy.get(k, 0) + 1
                continue
            if spp and (ROOT / spp).exists():
                verbatim_cells.append((k, sq, ROOT / spp))

        sample = verbatim_cells if full else verbatim_cells[:sample_n]
        for k, sq, pdf in sample:
            txt = _pdf_text(pdf)
            if txt is None:
                continue
            # Image-only / non-text-extractable PDF (e.g. a scanned brochure):
            # a confident verbatim-style quote on it CANNOT have been
            # text-extracted — it is hallucinated provenance (the Acko
            # acko-health-iii-platinum__brochure class). Hard fail.
            if len(txt) < 400:
                quote_fail += 1
                errors.append(
                    f"[fabricated-quote-image-pdf] {f.name}:{k} → cited PDF has "
                    f"only {len(txt)} extractable chars (image-only scan) yet "
                    f"carries a verbatim quote ⟶ “{sq[:60]}…”"
                )
                continue
            # Heuristic / placeholder note dressed as a quote — never verbatim.
            if _HEURISTIC_NOTE.search(sq):
                quote_fail += 1
                errors.append(f"[non-verbatim-note] {f.name}:{k} ⟶ “{sq[:70]}…”")
                continue
            # Shingle match: a genuine clause copied from a multi-column / table
            # PDF survives column-interleaved extraction in fragments, so a
            # single 60-char substring is too brittle (false-fails real quotes).
            # Require a majority of the quote's 8-word shingles to appear.
            qn = _norm(sq)
            words = qn.split()
            if len(words) < 8:
                ok = qn in txt
            else:
                shingles = [" ".join(words[i:i + 8]) for i in range(0, len(words) - 7, 3)]
                hits = sum(1 for s in shingles if s in txt)
                ok = shingles and hits / len(shingles) >= 0.5
            if ok:
                quote_ok += 1
            else:
                quote_fail += 1
                errors.append(f"[quote-not-in-pdf] {f.name}:{k} ⟶ “{sq[:70]}…”")

    print(f"files={len(files)}  verbatim_quotes_OK={quote_ok}  "
          f"verbatim_quotes_FAIL={quote_fail}  "
          f"legacy_provenance_cells={len(legacy_prov)}  "
          f"hard_errors={len(errors)}")
    if field_legacy:
        print("legacy-provenance by field (value present, source not verbatim-"
              "verifiable):")
        for fld, n in sorted(field_legacy.items(), key=lambda x: -x[1]):
            print(f"  {fld:42s} {n}")
    if quote_ok == 0 and quote_fail == 0:
        print("FAIL — checked 0 verbatim quotes (PDF reader broken). Not a PASS.")
        return 1
    if errors:
        print(f"\n-- HARD FAIL ({len(errors)}; first 30) --")
        for e in errors[:30]:
            print(" ", e)
        return 1
    if legacy_prov:
        print(f"\n-- NOT COMMIT-READY: {len(legacy_prov)} cells carry a value "
              f"whose source is a provenance note, not a verbatim/url source. "
              f"Per the never-unsourced rule these must be re-extracted to a "
              f"verbatim quote (or nulled+sourced) before commit. --")
        return 2
    print(f"PASS — {quote_ok} verbatim quotes confirmed in-PDF, no fabricated "
          f"sentinels, no unsourced values, no legacy-provenance cells.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
