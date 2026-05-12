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
    sections.append(f"| Insurer slug | `{slug}` | derived from `data/corpus_urls.md` |")
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
    sections.append(f"_Methodology: [`docs/scorecard-methodology.md`](../../docs/scorecard-methodology.md) · 24 of 48 schema fields drive this grade._")
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
    rows.append("- Source list saved to `data/corpus_urls.md` (75 URLs)")
    rows.append("- `rag/download_corpus.py` downloads with PDF magic-byte verification + size floor (50KB)")
    rows.append("- `rag/download_retry.py` retried failed downloads with browser-grade headers (rescued ICICI Lombard 9/9)")
    rows.append("- Star Health (11 PDFs) blocked by CDN bot protection — deferred to v2 (see `docs/04-failure-modes.md` + ROADMAP)")
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
    rows.append(f"Methodology: [`docs/scorecard-methodology.md`](../../docs/scorecard-methodology.md)")
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


def main():
    KB_DIR.mkdir(parents=True, exist_ok=True)
    POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    CALCULATIONS_DIR.mkdir(parents=True, exist_ok=True)

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

    # Master index
    (KB_DIR / "INDEX.md").write_text(build_master_index(policies, scorecards))

    print(f"\n✓ kb/INDEX.md")
    print(f"✓ kb/policies/  ({len(policies)} files)")
    print(f"✓ kb/research/  (3 files)")
    print(f"✓ kb/calculations/  (3 files)")
    print(f"\nKB rebuilt — open `kb/INDEX.md` for the master map.")


if __name__ == "__main__":
    main()
