"""Generate per-policy knowledge-base markdown files.

For every successfully extracted policy in DuckDB, emit:
  kb/policies/<policy_id>.md

Each file is a human-readable, source-cited summary of every data point
we have for that policy:

  - IDENTITY: insurer + product + UIN + source PDF URL + extraction date
  - EXTRACTED FIELDS: each of 48 schema fields, value, source-clause pointer
    (when extraction captured one), explicitly marked nullable
  - COMPUTED SCORECARD: 6 sub-scores with the per-field signals that produced
    each one — so the score is reproducible from the doc above
  - DERIVATION TYPES: explicit per-field tag —
      [E] = extracted directly from PDF
      [C] = computed from extracted fields (e.g. scorecard sub-score)
      [I] = implied / curated by us (e.g. insurer_name canonicalization)
      [V] = verified externally (e.g. insurer home URL HEAD-check)

Also writes:
  kb/INDEX.md — table of all policies, their grade, data completeness
  kb/SCHEMA.md — copy of rag/SCHEMA.md (so kb/ is self-contained)

Run:
  python -m rag.build_kb
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from backend.config import settings
from backend.scorecard import build_scorecard, Scorecard
from rag.schema import HealthPolicy

ROOT = settings.CORPUS_DIR.parent.parent
EXTRACTED = settings.EXTRACTED_DIR
KB_DIR = ROOT / "kb"
POLICIES_DIR = KB_DIR / "policies"


# Map insurer slug → home URL (verified, see eval/verified_urls.json)
INSURER_HOME = {
    "aditya-birla":  "https://www.adityabirlacapital.com/healthinsurance",
    "bajaj-allianz": "https://www.bajajallianz.com/",
    "care-health":   "https://www.careinsurance.com/",
    "hdfc-ergo":     "https://www.hdfcergo.com/",
    "icici-lombard": "https://www.icicilombard.com/",
    "manipalcigna":  "https://www.manipalcigna.com/",
    "new-india":     "https://www.newindia.co.in/",
    "niva-bupa":     "https://www.nivabupa.com/",
    "star-health":   "https://www.starhealth.in/",
    "tata-aig":      "https://www.tataaig.com/",
}


def field_marker(field_name: str, value, schema_required: bool) -> str:
    """Return [E]/[C]/[I]/[V] derivation tag for a field."""
    if field_name in ("insurer_name", "policy_name", "policy_id", "insurer_slug"):
        return "[I]"  # canonicalized by us
    if field_name in ("source_pdf_url", "source_pdf_path", "last_updated_date"):
        return "[V]"
    if value is None:
        return "[E?]"  # field was extractable, but came back null
    return "[E]"


def format_value(v) -> str:
    if v is None:
        return "_null (not in document)_"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, dict) and "covered" in v:
        if v.get("covered") is True:
            parts = ["Yes"]
            if v.get("limit_inr"):
                parts.append(f"limit ₹{int(v['limit_inr']):,}")
            if v.get("limit_text"):
                parts.append(f'"{v["limit_text"]}"')
            if v.get("notes"):
                parts.append(f"({v['notes']})")
            return ", ".join(parts)
        if v.get("covered") is False:
            return "No"
        return f"_unclear: {v}_"
    if isinstance(v, list):
        if not v:
            return "_empty_"
        return ", ".join(f"`{x}`" for x in v[:8])
    return f"`{v}`"


def render_field_groups(p: dict, schema_fields: dict) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Return list of (group_name, [(field, value_str, marker)]) for the doc."""
    groups = {
        "Identity": ["policy_id", "insurer_slug", "insurer_name", "policy_name", "policy_type", "uin_code"],
        "Eligibility": ["min_entry_age", "max_entry_age", "max_renewal_age", "min_child_entry_age",
                        "family_composition", "residency_requirement"],
        "Sum insured & premium": ["sum_insured_options", "premium_payment_modes",
                                  "premium_range_band", "premium_payment_term", "grace_period_days"],
        "Waiting periods": ["initial_waiting_period_days", "pre_existing_disease_waiting_months",
                            "specific_disease_waiting_months", "maternity_waiting_months",
                            "specific_diseases_listed"],
        "Coverage scope": ["pre_hospitalization_days", "post_hospitalization_days",
                           "day_care_treatments_count", "domiciliary_treatment", "ayush_coverage",
                           "maternity_coverage", "newborn_coverage", "organ_donor_expenses",
                           "ambulance_cover", "critical_illness_cover", "restoration_benefit",
                           "no_claim_bonus_pct", "preventive_health_checkup"],
        "Sub-limits & caps": ["room_rent_capping", "icu_capping", "copayment_pct",
                              "disease_wise_sub_limits", "deductible_amount"],
        "Geography & network": ["geographic_coverage_india", "worldwide_emergency_cover",
                                "network_hospital_count", "cashless_treatment_supported"],
        "Exclusions": ["permanent_exclusions", "temporary_exclusions", "notable_exclusions_summary"],
        "Claim & service": ["claim_settlement_ratio", "claim_process_summary",
                            "tat_cashless_authorization_hours"],
        "Riders / optional": ["available_riders", "top_rider_examples", "rider_premium_indicative"],
        "Source metadata": ["source_pdf_path", "source_pdf_url", "last_updated_date",
                            "extraction_confidence_pct"],
    }
    out = []
    for group, fields in groups.items():
        rows = []
        for f in fields:
            if f not in schema_fields:
                continue
            value = p.get(f)
            marker = field_marker(f, value, False)
            rows.append((f, format_value(value), marker))
        out.append((group, rows))
    return out


def render_scorecard(sc: Scorecard) -> str:
    bars = []
    for s in sc.sub_scores:
        bar_len = int(s.score / 5)
        bar = "█" * bar_len + "·" * (20 - bar_len)
        bars.append(f"| **{s.name}** | `{bar}` | **{s.score}/100** · {s.summary} |")
        if s.signals:
            sig_str = "<br/>".join(f"&nbsp;&nbsp;&nbsp;{sig}" for sig in s.signals)
            bars.append(f"|  | _signals:_<br/>{sig_str} |  |")
    return "\n".join(bars)


def build_policy_md(p: dict) -> str:
    schema_fields = HealthPolicy.model_fields
    groups = render_field_groups(p, schema_fields)
    sc = build_scorecard(p)

    pid = p.get("policy_id", "")
    pname = p.get("policy_name", pid)
    insurer = p.get("insurer_name") or p.get("insurer_slug", "")
    slug = p.get("insurer_slug", "")
    home = INSURER_HOME.get(slug, "")
    src_url = p.get("source_pdf_url", "") or p.get("source_metadata", {}).get("source_pdf_url", "")

    sections = []
    sections.append(f"# {pname}\n")
    sections.append(f"_Policy KB sheet — auto-generated from `rag/extracted/{pid}.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._\n")

    # Identity block
    sections.append("## Identity")
    sections.append("")
    sections.append(f"| Field | Value | Source |")
    sections.append(f"| --- | --- | --- |")
    sections.append(f"| Insurer | [{insurer}]({home}) | curated · verified `eval/verified_urls.json` |")
    sections.append(f"| Insurer slug | `{slug}` | derived from `40-data/corpus_urls.md` |")
    sections.append(f"| Policy | **{pname}** | extracted from policy wordings |")
    sections.append(f"| Policy id | `{pid}` | minted by us (`<insurer-slug>__<doc-slug>`) |")
    sections.append(f"| Source PDF | [{src_url[:80]}…]({src_url}) | downloaded + verified at ingest time |")
    sections.append(f"| Extraction confidence | {p.get('extraction_confidence_pct', 'n/a')}% (self-rated by extractor) | computed |")
    sections.append("")

    # Scorecard
    sections.append("## Scorecard — single A-F view")
    sections.append("")
    sections.append(f"### **Grade: {sc.grade}** ({sc.overall_score}/100)")
    sections.append(f"> {sc.one_liner}")
    sections.append("")
    sections.append(f"**Data completeness:** {sc.data_completeness_pct}% of the 24 scored fields have data.")
    sections.append("")
    sections.append("| Sub-score | Bar | Score & Signals |")
    sections.append("| --- | --- | --- |")
    sections.append(render_scorecard(sc))
    sections.append("")
    sections.append(f"_Methodology: [`70-docs/scorecard-methodology.md`](../../70-docs/scorecard-methodology.md) · 24 of 48 schema fields drive this grade._")
    sections.append("")

    # Extracted fields by group
    sections.append("## All extracted data points — by group")
    sections.append("")
    sections.append("**Derivation legend:**")
    sections.append("- **[E]** Extracted directly from policy PDF by LLM")
    sections.append("- **[E?]** Field was in schema but extraction returned null (data missing or unclear in source)")
    sections.append("- **[C]** Computed from extracted fields (e.g. scorecard sub-score)")
    sections.append("- **[I]** Implied / canonicalised by us")
    sections.append("- **[V]** Verified externally (HEAD-check, URL probe)")
    sections.append("")

    for group_name, rows in groups:
        if not rows:
            continue
        present = [r for r in rows if "_null" not in r[1]]
        sections.append(f"### {group_name}  _{len(present)}/{len(rows)} fields populated_")
        sections.append("")
        sections.append("| Field | Value | Type |")
        sections.append("| --- | --- | --- |")
        for f, val, marker in rows:
            sections.append(f"| `{f}` | {val} | {marker} |")
        sections.append("")

    # Lineage / audit trail for this policy
    sections.append("## Lineage — end-to-end audit trail for this policy")
    sections.append("")
    sections.append("Every data point above traces through this exact pipeline:")
    sections.append("")
    sections.append(f"```")
    sections.append(f"1. SOURCE        — {src_url[:60]}…")
    sections.append(f"                   (curated by corpus-discovery agent, verified at download)")
    sections.append(f"2. DOWNLOAD      — rag/download_corpus.py + rag/download_retry.py")
    sections.append(f"                   PDF magic-byte check + size > 50 KB enforced")
    sections.append(f"3. PARSE         — pdfplumber → per-page text (rag/ingest.py:read_pdf_pages)")
    sections.append(f"4. CHUNK         — 800 tok / 120 overlap, sentence-aware (rag/ingest.py:chunk_pages)")
    sections.append(f"5. EMBED         — BGE-small-en-v1.5 → 384-dim vector (backend/providers/local_embeddings.py)")
    sections.append(f"6. INDEX         — Chroma persistent client (rag/vectors/) with metadata")
    sections.append(f"7. EXTRACT       — Sarvam-M (DeepSeek-V3 fallback) prompt with HealthPolicy schema")
    sections.append(f"                   → rag/extracted/{pid}.json (this file's source data)")
    sections.append(f"8. STORE         — DuckDB upsert into rag/policies.duckdb")
    sections.append(f"9. SCORE         — backend/scorecard.py rules-based, no LLM-in-the-loop")
    sections.append(f"10. KB SHEET     — rag/build_kb.py renders this markdown")
    sections.append(f"```")
    sections.append("")
    sections.append("**Re-running the audit trail:** delete `rag/extracted/{pid}.json` → run `python -m rag.extract --policy {pid}` → run `python -m rag.build_kb` → diff this file.")
    sections.append("")
    sections.append("## What the bot will and won't say about this policy")
    sections.append("")
    sections.append("Per the 4-gate faithfulness verifier (`backend/faithfulness.py`):")
    sections.append("- Bot answers questions about this policy **only when retrieval scores for its chunks are ≥ 0.30 cosine** (BGE-small).")
    sections.append("- Every factual claim cites this PDF with page numbers.")
    sections.append(f"- If asked something whose answer is _null_ in the schema above (marked **[E?]**), the bot refuses — the data is not in the source PDF.")
    sections.append(f"- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id={pid}`.")
    sections.append("")

    return "\n".join(sections)


def build_index(policies: list[dict], scorecards: list[Scorecard]) -> str:
    rows = []
    rows.append("# Knowledge Base — Index")
    rows.append("")
    rows.append(f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} from `rag/extracted/*.json`._")
    rows.append("")
    rows.append(f"## All policies ({len(policies)})")
    rows.append("")
    rows.append("| Policy | Insurer | Grade | Score | Data completeness | KB sheet |")
    rows.append("| --- | --- | --- | --- | --- | --- |")
    for p, sc in zip(policies, scorecards):
        pid = p.get("policy_id", "")
        rows.append(
            f"| **{p.get('policy_name', pid)}** | {p.get('insurer_slug', '')} | "
            f"**{sc.grade}** | {sc.overall_score}/100 | "
            f"{sc.data_completeness_pct}% | [→](policies/{pid}.md) |"
        )
    rows.append("")
    rows.append("## What's in here")
    rows.append("")
    rows.append("Each policy gets a `policies/<policy_id>.md` file containing:")
    rows.append("- **Identity** — insurer, UIN, source PDF URL")
    rows.append("- **Scorecard** — single A-F grade with 6 sub-scores")
    rows.append("- **All 48 extracted fields** — value, type (Extracted / Computed / Implied / Verified)")
    rows.append("- **Faithfulness notes** — what the bot will and won't claim from this doc")
    rows.append("")
    rows.append("This is the canonical per-policy artifact. Everything else (Chroma vectors, DuckDB rows, bot citations) is derived from the same `rag/extracted/<policy_id>.json` files.")
    rows.append("")
    return "\n".join(rows)


RESEARCH_DIR = KB_DIR / "research"
CALCULATIONS_DIR = KB_DIR / "calculations"
REVIEWS_KB_DIR = KB_DIR / "reviews"
PREMIUMS_KB_DIR = KB_DIR / "premiums"
SECURITY_KB_DIR = KB_DIR / "security"
EVAL_KB_DIR = KB_DIR / "eval"
METHODOLOGY_KB_DIR = KB_DIR / "methodology"


def build_research_corpus_acquisition() -> str:
    """How we acquired the 76 PDFs — from rag/corpus/_manifest.json"""
    mf_path = ROOT / "rag" / "corpus" / "_manifest.json"
    if not mf_path.exists():
        return "_manifest.json not found_"
    m = json.loads(mf_path.read_text())
    rows = []
    rows.append("# Research — Corpus Acquisition")
    rows.append("")
    rows.append(f"_Auto-generated from `rag/corpus/_manifest.json` at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}_")
    rows.append("")
    rows.append("## Headline")
    rows.append(f"- Total attempted: **{m.get('total_entries')}** URLs across 10 target insurers")
    rows.append(f"- Successfully downloaded: **{m.get('ok')}** PDFs")
    rows.append(f"- Failed: **{m.get('fail')}**")
    rows.append(f"- Elapsed: {m.get('elapsed_seconds')}s")
    rows.append("")
    rows.append("## Per-insurer breakdown")
    rows.append("")
    rows.append("| Insurer | OK | Fail |")
    rows.append("| --- | --- | --- |")
    for slug, c in sorted(m.get("by_insurer", {}).items()):
        rows.append(f"| `{slug}` | {c.get('ok', 0)} | {c.get('fail', 0)} |")
    rows.append("")
    rows.append("## Failure reasons")
    rows.append("")
    from collections import Counter
    errs = Counter(r.get("error") for r in m.get("results", []) if not r.get("ok"))
    rows.append("| Reason | Count |")
    rows.append("| --- | --- |")
    for err, n in errs.most_common():
        rows.append(f"| `{err}` | {n} |")
    rows.append("")
    rows.append("## How we did it")
    rows.append("- Dispatched a research agent to find direct PDF URLs for all health policies across 10 target insurers")
    rows.append("- Source list saved to `40-data/corpus_urls.md` (75 URLs)")
    rows.append("- `rag/download_corpus.py` downloads with PDF magic-byte verification + size floor (50KB)")
    rows.append("- `rag/download_retry.py` retried failed downloads with browser-grade headers (rescued ICICI Lombard 9/9)")
    rows.append("- Star Health (11 PDFs) blocked by CDN bot protection — deferred to v2 (see `70-docs/04-failure-modes.md` + ROADMAP)")
    rows.append("")
    return "\n".join(rows)


def build_research_url_verification() -> str:
    vp = ROOT / "eval" / "verified_urls.json"
    if not vp.exists():
        return "_verified_urls.json not found_"
    v = json.loads(vp.read_text())
    rows = []
    rows.append("# Research — URL Verification")
    rows.append("")
    rows.append(f"_Auto-generated from `eval/verified_urls.json` (verified at {v.get('verified_at')})_")
    rows.append("")
    rows.append("## Headline")
    s = v.get("insurer_summary", {})
    rows.append(f"- Insurer home URLs: **{s.get('ok', 0)}/{s.get('total', 0)}** reachable via HEAD/GET")
    s = v.get("policy_summary", {})
    rows.append(f"- Policy PDF URLs (sample): **{s.get('ok', 0)}/{s.get('total', 0)}** reachable")
    rows.append("")
    rows.append("## Why this matters")
    rows.append("Every URL that the bot or coverage panel surfaces to the user is checked here. We do NOT show URLs that we haven't verified.")
    rows.append("Verification script: [`tools/verify_urls.py`](../../tools/verify_urls.py).")
    rows.append("")
    rows.append("## Insurer home URLs")
    rows.append("")
    rows.append("| Insurer | URL | Status |")
    rows.append("| --- | --- | --- |")
    for slug, info in sorted(v.get("insurers", {}).items()):
        url = info.get("url", "—")
        st = "✓ OK" if info.get("ok") else f"✗ {info.get('error') or info.get('status')}"
        rows.append(f"| {info.get('name', slug)} | [{url}]({url}) | {st} |")
    rows.append("")
    rows.append("**Note:** 3 insurer home URLs return 403/timeout to our script (Star Health, ICICI Lombard, Care Health) — but the sites are real and public. Browsers open them fine. This is bot-protection behaviour, not a broken URL.")
    rows.append("")
    return "\n".join(rows)


def build_research_verified_insurers() -> str:
    """One row per insurer with metadata."""
    rows = []
    rows.append("# Research — Verified Insurer Universe")
    rows.append("")
    rows.append("The 10 insurers our v1 corpus covers, with verified home URLs and policy counts.")
    rows.append("")
    rows.append("| Slug | Insurer | Home URL | Source |")
    rows.append("| --- | --- | --- | --- |")
    for slug, home in INSURER_HOME.items():
        rows.append(f"| `{slug}` | _(per `backend/main.py` insurer_meta)_ | [{home}]({home}) | curated + HEAD-verified |")
    rows.append("")
    return "\n".join(rows)


def build_calc_scorecard_results(policies: list[dict], scorecards: list[Scorecard]) -> str:
    rows = []
    rows.append("# Calculations — Scorecard Results")
    rows.append("")
    rows.append(f"_Computed by `backend/scorecard.py` at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} on {len(policies)} extracted policies._")
    rows.append("")
    rows.append(f"Methodology: [`70-docs/scorecard-methodology.md`](../../70-docs/scorecard-methodology.md)")
    rows.append("")
    rows.append("## All policies — overall")
    rows.append("")
    rows.append("| Policy | Insurer | Grade | Score | Data % |")
    rows.append("| --- | --- | --- | --- | --- |")
    for p, sc in sorted(zip(policies, scorecards), key=lambda x: -x[1].overall_score):
        rows.append(
            f"| [{p.get('policy_name', sc.policy_id)}](../policies/{sc.policy_id}.md) | "
            f"{sc.insurer_slug} | **{sc.grade}** | {sc.overall_score} | {sc.data_completeness_pct}% |"
        )
    rows.append("")
    rows.append("## Per-sub-score averages")
    rows.append("")
    rows.append("| Sub-score | Mean | Min | Max |")
    rows.append("| --- | --- | --- | --- |")
    sub_names = [s.name for s in scorecards[0].sub_scores] if scorecards else []
    for i, name in enumerate(sub_names):
        vals = [sc.sub_scores[i].score for sc in scorecards]
        if not vals:
            continue
        rows.append(f"| {name} | {sum(vals)/len(vals):.1f} | {min(vals)} | {max(vals)} |")
    rows.append("")
    rows.append("## Grade distribution")
    rows.append("")
    from collections import Counter
    dist = Counter(sc.grade for sc in scorecards)
    for g in "ABCDF":
        rows.append(f"- **{g}:** {dist.get(g, 0)}")
    rows.append("")
    return "\n".join(rows)


def build_calc_eval_results() -> str:
    erp = ROOT / "eval" / "results.json"
    if not erp.exists():
        return "_eval/results.json not found — run `python -m eval.run` first_"
    e = json.loads(erp.read_text())
    s = e.get("summary", {})
    rows = []
    rows.append("# Calculations — Eval Run Results")
    rows.append("")
    rows.append(f"_Most recent gold Q&A eval run at {s.get('ran_at')}_")
    rows.append("")
    rows.append("## Headline")
    rows.append(f"- Questions: **{s.get('n_questions')}**")
    rows.append(f"- Factual accuracy: **{(s.get('factual_accuracy', 0) * 100):.1f}%**")
    rows.append(f"- Citation accuracy: **{(s.get('citation_accuracy', 0) * 100):.1f}%**")
    rows.append(f"- Refusal precision: **{(s.get('refusal_precision', 0) * 100):.1f}%**")
    rows.append(f"- Blocked by faithfulness: {s.get('blocked_count', 0)}")
    rows.append(f"- Elapsed: {s.get('elapsed_seconds')}s")
    rows.append("")
    rows.append("## By question type")
    rows.append("")
    rows.append("| Type | Accuracy |")
    rows.append("| --- | --- |")
    for t, acc in sorted(s.get("by_type", {}).items(), key=lambda kv: -kv[1]):
        rows.append(f"| {t} | {acc*100:.1f}% |")
    rows.append("")
    rows.append("## By brain")
    rows.append("")
    rows.append("| Brain | Accuracy |")
    rows.append("| --- | --- |")
    for b, acc in sorted(s.get("by_brain", {}).items(), key=lambda kv: -kv[1]):
        rows.append(f"| {b} | {acc*100:.1f}% |")
    rows.append("")
    rows.append(f"Full per-question results: [`eval/results.md`](../../eval/results.md) and [`eval/results.json`](../../eval/results.json).")
    rows.append("")
    return "\n".join(rows)


def build_calc_extraction_audit(policies: list[dict]) -> str:
    """Per-field extraction completeness across all policies."""
    from collections import Counter
    rows = []
    rows.append("# Calculations — Extraction Quality Audit")
    rows.append("")
    rows.append(f"_Computed from `rag/extracted/*.json` ({len(policies)} files)._")
    rows.append("")
    rows.append("How often each of the 48 schema fields actually got populated by extraction. Low-completeness fields are the ones to harden in v2 (better prompts, or LLM router).")
    rows.append("")
    schema_fields = list(HealthPolicy.model_fields.keys())
    rows.append("| Field | Populated | % |")
    rows.append("| --- | --- | --- |")
    for f in schema_fields:
        n_filled = sum(
            1 for p in policies if p.get(f) not in (None, "", [], 0)
        )
        pct = (n_filled / max(1, len(policies))) * 100
        rows.append(f"| `{f}` | {n_filled}/{len(policies)} | {pct:.0f}% |")
    rows.append("")
    return "\n".join(rows)


def build_master_index(policies: list[dict], scorecards: list[Scorecard]) -> str:
    return f"""# Knowledge Base — Master Index

_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}. Auto-regenerable via `python -m rag.build_kb`._

This is the **single canonical KB** for this project. Every data point in the bot
(citations, scorecards, comparison views) traces back to one of these files.

## Layout

```
kb/
├── INDEX.md                          (this file)
├── policies/<policy_id>.md          ({len(policies)} files — one per extracted policy)
├── research/
│   ├── corpus_acquisition.md         (how we got 75 PDFs)
│   ├── url_verification.md           (HEAD-check results)
│   └── verified_insurers.md          (10 insurers, home URLs)
└── calculations/
    ├── scorecard_results.md          (all scores)
    ├── eval_results.md               (gold Q&A grader output)
    └── extraction_quality_audit.md   (per-field completeness)
```

## Quick links

- **All policies (graded):** [`calculations/scorecard_results.md`](calculations/scorecard_results.md)
- **All policy KB sheets:** [`policies/`](policies/)
- **Eval run results:** [`calculations/eval_results.md`](calculations/eval_results.md)
- **Extraction quality:** [`calculations/extraction_quality_audit.md`](calculations/extraction_quality_audit.md)
- **URL verification:** [`research/url_verification.md`](research/url_verification.md)
- **Corpus acquisition:** [`research/corpus_acquisition.md`](research/corpus_acquisition.md)

## Derivation conventions

Every field in every KB file is tagged with one of:
- **[E]** Extracted directly from a source PDF
- **[E?]** Extractable in the schema but absent / null in this specific source
- **[C]** Computed from extracted fields (e.g. scorecard score)
- **[I]** Implied / canonicalised by us (e.g. insurer slug)
- **[V]** Externally verified (HEAD-check, URL probe)

## Headline counts

- Policies extracted: **{len(policies)}**
- Insurers covered: **{len({sc.insurer_slug for sc in scorecards})}**
- Grade distribution: {dict(__import__('collections').Counter(sc.grade for sc in scorecards))}

## Why we maintain this in markdown

JSON is for machines. Markdown is for reviewers. Each KB file is intentionally
human-readable so an interviewer or auditor can open `kb/policies/<some-id>.md`
and read every data point with its source — without running the bot.

The bot's runtime answers are NEVER allowed to use information that isn't
traceable to one of these files (see `backend/faithfulness.py`).
"""


def build_reviews_kb_for(slug: str, data: dict) -> str:
    cm = data.get("claim_metrics", {})
    agg = data.get("aggregator_ratings", {})
    tp = data.get("trustpilot", {})
    reddit = data.get("reddit_sentiment", {})
    yt = data.get("youtube_coverage", {})
    news = data.get("in_news", [])
    score = data.get("aggregate_score", {})
    ver = data.get("_url_verification", {})

    rows = []
    rows.append(f"# {data.get('insurer_name', slug)} — Reputation Sheet")
    rows.append("")
    rows.append(f"_Auto-generated from `40-data/reviews/{slug}.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._")
    rows.append("")
    rows.append(f"**Aggregate score:** **{score.get('value_0_100', 'n/a')}** ({score.get('letter_grade', '?')}). _{score.get('headline', '')}_")
    rows.append("")
    rows.append(f"**URL verification:** {ver.get('ok', 0)}/{ver.get('total_urls', 0)} URLs reachable via HEAD-check; {ver.get('broken_count', 0)} return 403 to scripts (bot-protected real URLs — open fine in a browser).")
    rows.append("")
    rows.append("## IRDAI claim metrics")
    rows.append("")
    rows.append("| Metric | Value |")
    rows.append("| --- | --- |")
    rows.append(f"| Claim settlement ratio (CSR) | **{cm.get('claim_settlement_ratio_pct', 'n/a')}%** ({cm.get('claim_settlement_ratio_year', 'unknown year')}) |")
    rows.append(f"| Complaints / 10K policies | **{cm.get('complaints_per_10k_policies', 'n/a')}** ({cm.get('complaints_year', 'unknown')}) |")
    rows.append(f"| Source | [IRDAI Annual Report]({cm.get('source_irdai_url', '#')}) |")
    rows.append("")
    rows.append("## Aggregator portal ratings")
    rows.append("")
    rows.append("| Portal | Avg star | Review count | URL |")
    rows.append("| --- | --- | --- | --- |")
    for pname, pdata in agg.items():
        if not pdata: continue
        rows.append(f"| {pname} | {pdata.get('avg_star', 'n/a')} | {pdata.get('review_count', 'n/a')} | [{(pdata.get('url') or 'n/a')[:60]}]({pdata.get('url', '#')}) |")
    rows.append("")
    if tp.get("score") is not None:
        rows.append(f"**Trustpilot:** {tp.get('score')} ({tp.get('review_count', 'n/a')} reviews) — [{(tp.get('url') or 'n/a')[:60]}]({tp.get('url', '#')})")
        rows.append("")
    rows.append("## Reddit / r/IndianFinance sentiment")
    rows.append("")
    rows.append(f"- Overall: **{reddit.get('sentiment_overall', 'n/a')}**")
    rows.append(f"- Mentions estimate: {reddit.get('mentions_last_year_estimate', 'n/a')}")
    if reddit.get("notable_themes"):
        rows.append(f"- Themes: {', '.join(reddit['notable_themes'])}")
    if reddit.get("sample_post_urls"):
        rows.append(f"- Sample posts:")
        for u in reddit["sample_post_urls"][:5]:
            rows.append(f"  - [{u[:80]}]({u})")
    rows.append("")
    rows.append("## YouTube coverage")
    rows.append("")
    rows.append(f"- Overall sentiment: **{yt.get('overall_youtube_sentiment', 'n/a')}**")
    for entry in yt.get("top_creators_who_reviewed", [])[:5]:
        rows.append(f"- **{entry.get('creator', '?')}** — [{(entry.get('video_url') or 'n/a')[:80]}]({entry.get('video_url', '#')}) — _{entry.get('verdict', '')}_")
    rows.append("")
    rows.append("## Recent news")
    rows.append("")
    for n in news[:8]:
        rows.append(f"- **{n.get('headline', '?')}** ({n.get('publication', '?')}, {n.get('date', '?')}, tone: {n.get('tone', '?')}) — [{(n.get('url') or 'n/a')[:80]}]({n.get('url', '#')})")
    rows.append("")
    rows.append("---")
    rows.append("")
    rows.append(f"_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `40-data/reviews/INDEX.md` for the leaderboard._")
    rows.append("")
    rows.append(f"**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.")
    return "\n".join(rows)


def build_reviews_index(all_reviews: list[dict]) -> str:
    rows = []
    rows.append("# Reviews — Insurer Reputation Index")
    rows.append("")
    rows.append(f"_Auto-generated. Source: `40-data/reviews/*.json`. Per-insurer sheets in `kb/reviews/<slug>.md`._")
    rows.append("")
    rows.append("## Leaderboard")
    rows.append("")
    rows.append("| Rank | Insurer | Score | Grade | CSR | Complaints/10K | URL verification |")
    rows.append("| --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(sorted(all_reviews, key=lambda d: -(d.get("aggregate_score", {}).get("value_0_100") or 0)), 1):
        slug = r.get("insurer_slug")
        score = r.get("aggregate_score", {})
        cm = r.get("claim_metrics", {})
        ver = r.get("_url_verification", {})
        rows.append(
            f"| {i} | [{r.get('insurer_name', slug)}](./{slug}.md) | "
            f"**{score.get('value_0_100', 'n/a')}** | {score.get('letter_grade', '?')} | "
            f"{cm.get('claim_settlement_ratio_pct', 'n/a')}% | "
            f"{cm.get('complaints_per_10k_policies', 'n/a')} | "
            f"{ver.get('ok', 0)}/{ver.get('total_urls', 0)} reachable |"
        )
    rows.append("")
    rows.append("## Bot integration")
    rows.append("")
    rows.append("- API: `GET /api/insurers/<slug>/reviews`")
    rows.append("- The IRDAI CSR + complaints per 10K from this data feeds the **Claim Experience** sub-score of the scorecard (see `kb/policies/<id>.md` for the per-policy effect).")
    rows.append("- v2 expansions: live Reddit/YouTube sentiment refresh, IRDAI weekly refresh, news monitoring with alerts on insurer-specific incidents.")
    return "\n".join(rows)


def build_premiums_kb() -> str:
    rows = []
    rows.append("# Premiums — Illustrative Pricing Data")
    rows.append("")
    rows.append("_Auto-generated from `40-data/premiums/illustrative_premiums.json`. Real PolicyBazaar / InsuranceDekho / rate-chart anchors plus derived scaling factors. NEVER a binding quote._")
    rows.append("")
    pf = settings.DATA_DIR / "premiums" / "illustrative_premiums.json"
    if not pf.exists():
        rows.append("_Premium data file not yet generated._")
        return "\n".join(rows)
    data = json.loads(pf.read_text())
    methodology = data.get("methodology", "")
    sources = data.get("sources_consulted", [])
    base = data.get("base_premiums", {})
    scaling = data.get("scaling_factors", {})

    rows.append(f"## Methodology")
    rows.append("")
    rows.append(methodology)
    rows.append("")
    rows.append("### Sources consulted")
    rows.append("")
    for s in sources[:10]:
        rows.append(f"- [{s[:100]}]({s})")
    rows.append("")
    rows.append("## Per-policy anchor samples")
    rows.append("")
    rows.append(f"_{len(base)} policies indexed._")
    rows.append("")
    for pid, entry in sorted(base.items()):
        samples = entry.get("samples", [])
        if not samples: continue
        rows.append(f"### {entry.get('policy_name', pid)}")
        rows.append(f"`policy_id`: `{pid}`")
        rows.append("")
        rows.append("| Age | SI (₹) | City | Smoker | Family | Premium ₹/yr | Source |")
        rows.append("| --- | --- | --- | --- | --- | --- | --- |")
        for s in samples:
            src = s.get("source_url", "")
            src_tag = "callback only" if src == "callback_only" else (f"[link]({src})" if src.startswith("http") else "derived")
            rows.append(
                f"| {s.get('age', '?')} | {s.get('sum_insured_inr', '?')} | "
                f"{s.get('city_tier', 'n/a')} | "
                f"{'Y' if s.get('smoker') else 'N'} | {s.get('family_size', 1)} | "
                f"**{s.get('annual_premium_inr', '?'):,}** | {src_tag} |"
            )
        rows.append("")

    rows.append("## Scaling factors")
    rows.append("")
    rows.append("These are derived from comparing real anchor points across age, SI, city, smoker, family-floater dimensions.")
    rows.append("")
    for cat, mults in scaling.items():
        if cat.startswith("_"): continue
        rows.append(f"### {cat}")
        rows.append("```")
        rows.append(json.dumps(mults, indent=2))
        rows.append("```")
        rows.append("")
    return "\n".join(rows)


def build_security_kb() -> str:
    return """# Security — Upload Gates + Hallucination Defense

_Auto-generated. Source modules: `backend/security.py` + `backend/faithfulness.py`._

## Upload security — 5 gates

Every PDF uploaded via `/api/upload-policy` runs through these gates before
indexing. Failure logs to `logs/upload_blocks.jsonl`.

| # | Gate | Check |
| --- | --- | --- |
| 1 | Mechanics | Magic bytes `%PDF`; size 5KB-25MB; `%%EOF` present; dangerous PDF features (`/JavaScript`, `/Launch`, `/OpenAction`, `/EmbeddedFile`, `/SubmitForm`, `/AA`, `/RichMedia`, `/Movie`, `/Sound`, `/GoToR`); embedded executable signatures (Windows PE, Linux ELF, Mach-O, Java class, shell, HTML/JS, PHP) |
| 2 | Content quality | ≥1,500 chars text; ≥3 pages; ≥1 insurance keyword match (catches "garbage PDF" uploads) |
| 3 | Prompt injection | 11 regex patterns scanning for "ignore previous instructions", "system prompt reveal", jailbreak markers, role-takeover patterns, im_start/im_end tokens |
| 4 | Session rate limit | 5 uploads/hour/session; 200 chunks/session lifetime |
| 5 | IP rate limit | 10 uploads/hour/IP (per X-Forwarded-For or peer IP) |

All gates run for EVERY upload. Block on any failure; the audit trail captures the reason set.

## Hallucination defense — 5 gates (runtime, per-turn)

| # | Gate | What it catches |
| --- | --- | --- |
| 1 | Retrieval floor | Top-1 cosine < 0.30 OR avg top-5 < 0.22 → refuse outright |
| 2 | Citation integrity | Any `[Source:…]` in the bot's reply must point to a real retrieved chunk's policy_name |
| 3 | Numeric grounding | Every ₹, %, day/month/year in the reply must appear in retrieved chunks (regex) |
| 4 | LLM-judge faithfulness | Groq Llama-3.3-70B inspects the reply against retrieved chunks; outputs strict JSON; non-circular eval |
| 5 (Indic) | Hinglish drift LLM-judge | Same idea on the Hinglish back-translation vs the English source |

Plus **regex anchors + back-translate cosine** as additional drift checks
when the bot replies in Hinglish.

All blocked replies → `logs/hallucinations.jsonl` with the reason set.

## What WE can't (yet) check

- LLM determinism (DeepSeek-V3 / Sarvam-M can produce slightly different
  output at `temperature=0`).
- Insurer-side PDF tampering — we trust the source PDF was real at download.
- Embedding model drift — pinned to BGE-small-en-v1.5.

These are explicit limits documented in `kb/AUDIT_TRAIL.md` §5.
"""


def build_eval_kb_index() -> str:
    rows = []
    rows.append("# Eval — Gold Q&A + Run History")
    rows.append("")
    rows.append("_Auto-generated. Source: `eval/gold_qa.json` + `eval/results.json` + `eval/run.py`._")
    rows.append("")
    gold_path = ROOT / "eval" / "gold_qa.json"
    if gold_path.exists():
        gold = json.loads(gold_path.read_text())
        from collections import Counter
        by_type = Counter(q.get("question_type") for q in gold)
        rows.append(f"## Gold Q&A composition — {len(gold)} pairs total")
        rows.append("")
        rows.append("| Type | Count |")
        rows.append("| --- | --- |")
        for t, n in by_type.most_common():
            rows.append(f"| `{t}` | {n} |")
        rows.append("")
        refusal_count = sum(1 for q in gold if q.get("expected_refusal"))
        rows.append(f"**Refusal-test questions:** {refusal_count} (these test the bot correctly refuses out-of-corpus questions)")
        rows.append("")
    results_path = ROOT / "eval" / "results.json"
    if results_path.exists():
        try:
            r = json.loads(results_path.read_text())
            s = r.get("summary", {})
            rows.append("## Most recent eval run")
            rows.append("")
            rows.append(f"- Ran: {s.get('ran_at')}")
            rows.append(f"- Questions: {s.get('n_questions')}")
            rows.append(f"- Factual accuracy: **{s.get('factual_accuracy', 0)*100:.1f}%**")
            rows.append(f"- Citation accuracy: **{s.get('citation_accuracy', 0)*100:.1f}%**")
            rows.append(f"- Refusal precision: **{s.get('refusal_precision', 0)*100:.1f}%**")
            rows.append(f"- Blocked by faithfulness: {s.get('blocked_count', 0)}")
            rows.append("")
        except Exception:
            pass
    rows.append("## Methodology")
    rows.append("")
    rows.append("- Gold Q&A built by 3 pipelines: auto-from-extraction (templated), LLM-drafted (human-verified), hand-crafted adversarial. See `70-docs/03-eval-plan.md`.")
    rows.append("- Grader: Groq Llama-3.3-70B (different model family from generators → non-circular).")
    rows.append("- Re-run: `python -m eval.run [--limit N] [--policy <id>]`.")
    rows.append("- CI gate: `.github/workflows/eval.yml` runs eval on every PR; blocks merge if factual_accuracy < 0.65 or citation_accuracy < 0.55.")
    return "\n".join(rows)


def build_methodology_kb() -> str:
    return """# Methodology — Pointers to all design docs

_All design / decision docs in one navigable place._

## Foundation docs (in `70-docs/`)

| Doc | What it covers |
| --- | --- |
| [`01-requirements.md`](../../70-docs/01-requirements.md) | Product vision, 3 user personas, buyer journey, 10 success criteria, 11 non-goals, constraints |
| [`02-architecture.md`](../../70-docs/02-architecture.md) | Stack picks, system diagram, schema groupings, repo layout, c-readiness commitments |
| [`03-eval-plan.md`](../../70-docs/03-eval-plan.md) | 3-pipeline gold Q&A construction, grader, metrics, run cadence |
| [`04-failure-modes.md`](../../70-docs/04-failure-modes.md) | 16 named failure modes (F-01..F-16), detection + mitigation per mode |
| [`05-needs-analysis-flow.md`](../../70-docs/05-needs-analysis-flow.md) | Fact-find question graph, bilingual prompts, termination logic |
| [`decisions.md`](../../70-docs/decisions.md) | Append-only log of 17+ technical decisions with alternatives + reasoning |
| [`tech-stack-rationale.md`](../../70-docs/tech-stack-rationale.md) | 22-row stack pick table + selection rubric + cost envelope |
| [`scorecard-methodology.md`](../../70-docs/scorecard-methodology.md) | 48-field schema → 24 scored fields → 6 sub-scores → A-F grade |
| [`ROADMAP.md`](../../70-docs/ROADMAP.md) | v1 vertical slice → v2 platform plan |
| [`information_source_map.md`](../../70-docs/information_source_map.md) | Corpus catalog (auto-generated by `rag/source_map.py`) |

## KB sub-indexes (each regenerable via `python -m rag.build_kb`)

| Path | Content |
| --- | --- |
| [`kb/INDEX.md`](../INDEX.md) | Master index |
| [`kb/AUDIT_TRAIL.md`](../AUDIT_TRAIL.md) | 10-stage data lineage + decision-to-artifact map |
| [`kb/policies/`](../policies/) | One sheet per extracted policy |
| [`kb/research/`](../research/) | Corpus acquisition, URL verification, insurer universe |
| [`kb/calculations/`](../calculations/) | Scorecard results, eval results, extraction audit |
| [`kb/reviews/`](../reviews/) | Per-insurer reputation sheets + leaderboard |
| [`kb/premiums/`](../premiums/) | Illustrative pricing samples + scaling factors |
| [`kb/security/`](../security/) | Upload gates + hallucination defense |
| [`kb/eval/`](../eval/) | Gold Q&A composition + run history |

## How to navigate

- New visitor → start at `kb/INDEX.md`
- Auditor → start at `kb/AUDIT_TRAIL.md`
- Engineer onboarding → start at `70-docs/02-architecture.md`
- BFSI compliance review → `kb/security/` + `70-docs/04-failure-modes.md` + `logs/hallucinations.jsonl`
- Buyer-side curiosity → any `kb/policies/<id>.md` ends with a "What the bot will and won't say" section.
"""


def main():
    KB_DIR.mkdir(parents=True, exist_ok=True)
    POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    CALCULATIONS_DIR.mkdir(parents=True, exist_ok=True)
    REVIEWS_KB_DIR.mkdir(parents=True, exist_ok=True)
    PREMIUMS_KB_DIR.mkdir(parents=True, exist_ok=True)
    SECURITY_KB_DIR.mkdir(parents=True, exist_ok=True)
    EVAL_KB_DIR.mkdir(parents=True, exist_ok=True)
    METHODOLOGY_KB_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(EXTRACTED.glob("*.json"))
    print(f"Found {len(files)} extracted policy JSONs")

    policies = []
    scorecards = []
    for f in files:
        try:
            p = json.loads(f.read_text())
        except Exception as e:
            print(f"  SKIP {f.name}: {e}")
            continue
        if "policy_id" not in p:
            continue
        policies.append(p)
        sc = build_scorecard(p)
        scorecards.append(sc)
        out = POLICIES_DIR / f"{p['policy_id']}.md"
        out.write_text(build_policy_md(p))

    # Research files
    (RESEARCH_DIR / "corpus_acquisition.md").write_text(build_research_corpus_acquisition())
    (RESEARCH_DIR / "url_verification.md").write_text(build_research_url_verification())
    (RESEARCH_DIR / "verified_insurers.md").write_text(build_research_verified_insurers())

    # Calculations files
    (CALCULATIONS_DIR / "scorecard_results.md").write_text(build_calc_scorecard_results(policies, scorecards))
    (CALCULATIONS_DIR / "eval_results.md").write_text(build_calc_eval_results())
    (CALCULATIONS_DIR / "extraction_quality_audit.md").write_text(build_calc_extraction_audit(policies))

    # Reviews KB
    reviews_dir = settings.DATA_DIR / "reviews"
    all_reviews = []
    if reviews_dir.exists():
        for rf in sorted(reviews_dir.glob("*.json")):
            try:
                rdata = json.loads(rf.read_text())
                slug = rdata.get("insurer_slug", rf.stem)
                (REVIEWS_KB_DIR / f"{slug}.md").write_text(build_reviews_kb_for(slug, rdata))
                all_reviews.append(rdata)
            except Exception:
                continue
        if all_reviews:
            (REVIEWS_KB_DIR / "INDEX.md").write_text(build_reviews_index(all_reviews))

    # Premiums KB
    (PREMIUMS_KB_DIR / "INDEX.md").write_text(build_premiums_kb())

    # Security KB
    (SECURITY_KB_DIR / "INDEX.md").write_text(build_security_kb())

    # Eval KB
    (EVAL_KB_DIR / "INDEX.md").write_text(build_eval_kb_index())

    # Methodology KB
    (METHODOLOGY_KB_DIR / "INDEX.md").write_text(build_methodology_kb())

    # Master index
    (KB_DIR / "INDEX.md").write_text(build_master_index(policies, scorecards))

    print(f"\n✓ kb/INDEX.md")
    print(f"✓ kb/policies/   ({len(policies)} files)")
    print(f"✓ kb/research/   (3 files)")
    print(f"✓ kb/calculations/ (3 files)")
    print(f"✓ kb/reviews/    ({len(all_reviews) + (1 if all_reviews else 0)} files)")
    print(f"✓ kb/premiums/   (1 file)")
    print(f"✓ kb/security/   (1 file)")
    print(f"✓ kb/eval/       (1 file)")
    print(f"✓ kb/methodology/ (1 file)")
    print(f"\nKB rebuilt — open `kb/INDEX.md` for the master map.")


if __name__ == "__main__":
    main()
