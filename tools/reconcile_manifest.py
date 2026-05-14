"""Reconcile rag/corpus/_manifest.json with what's actually on disk.

Some agents wrote PDFs to disk but lost their manifest edit due to concurrent
JSON access. This script:
  1. Walks rag/corpus/<insurer-slug>/ for every PDF
  2. For each PDF NOT already in manifest results[], adds an entry with
     URL recovered from this session's agent transcripts where possible,
     else url='' + note='url_pending_recovery'.
  3. Recomputes the by_insurer + ok + fail counters.
"""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "rag" / "corpus" / "_manifest.json"
TASKS_DIR = Path("/private/tmp/claude-501/-Users-rohitsar-Documents-Personal-AI-Work-Claude-Code/7a136460-0a4d-4ed7-a45e-7af7a8bf0ee2/tasks")

# Insurer-name lookup for the agents that succeeded (transcript-mined)
INSURER_NAMES = {
    "sbi-general": "SBI General Insurance",
    "acko": "Acko General Insurance",
    "iffco-tokio": "IFFCO Tokio General Insurance",
    "cholamandalam": "Cholamandalam MS General Insurance",
    "go-digit": "Go Digit General Insurance",
    "reliance-general": "Reliance General Insurance (now IndusInd)",
    "royal-sundaram": "Royal Sundaram General Insurance",
}


def _index_local_path_to_url() -> dict[str, str]:
    """Build a local_path -> url map by scraping agent transcripts."""
    mapping: dict[str, str] = {}
    if not TASKS_DIR.exists():
        return mapping
    for f in TASKS_DIR.glob("a*.output"):
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        # Look for explicit `local_path` + adjacent `url` in agent's JSON manifest edits
        # OR curl invocations of the form: curl ... -o <local-path> <url>
        for m in re.finditer(r"curl[^\n]+?-o\s+(?P<path>[^\s]+)\s+(?:--?\w+\s+\S+\s+)*(?P<url>https?://\S+)", text):
            mapping.setdefault(m.group("path"), m.group("url"))
        for m in re.finditer(r"curl[^\n]+?(?P<url>https?://\S+\.pdf[^\s]*)[^\n]*?-o\s+(?P<path>[^\s]+)", text):
            mapping.setdefault(m.group("path"), m.group("url"))
        # JSON-embedded "url": "https://..." right before "local_path": "rag/corpus/.../*.pdf"
        for m in re.finditer(
            r'"url"\s*:\s*"(?P<url>https?://[^"]+\.pdf[^"]*)"\s*,\s*"notes"[^,]*,\s*"local_path"\s*:\s*"(?P<path>rag/corpus/[^"]+)"',
            text,
        ):
            mapping.setdefault(m.group("path"), m.group("url"))
    return mapping


def main():
    manifest = json.loads(MANIFEST.read_text())
    results = manifest.setdefault("results", [])
    existing_paths = {r.get("local_path", "") for r in results}

    url_map = _index_local_path_to_url()
    print(f"URL map size from agent transcripts: {len(url_map)}")

    new_entries = 0
    new_with_url = 0
    new_without_url = 0
    by_insurer_extra: dict[str, dict[str, int]] = {}

    for slug in INSURER_NAMES:
        d = ROOT / "rag" / "corpus" / slug
        if not d.exists():
            continue
        for pdf in sorted(d.glob("*.pdf")):
            rel = str(pdf.relative_to(ROOT))
            if rel in existing_paths:
                continue
            stem = pdf.stem  # e.g. arogya-sanjeevani__wordings
            if "__" in stem:
                policy_slug, doc_type = stem.rsplit("__", 1)
            else:
                policy_slug, doc_type = stem, "wordings"
            policy_name = policy_slug.replace("-", " ").title()
            url = url_map.get(rel, "")
            entry = {
                "insurer_slug": slug,
                "insurer_name": INSURER_NAMES[slug],
                "policy_name": policy_name,
                "doc_type": doc_type,
                "url": url,
                "notes": "" if url else "url_pending_recovery — file downloaded by agent, source URL lost in manifest concurrent-write race",
                "local_path": rel,
                "size_bytes": pdf.stat().st_size,
                "ok": True,
                "error": None,
            }
            results.append(entry)
            new_entries += 1
            if url:
                new_with_url += 1
            else:
                new_without_url += 1
            by_insurer_extra.setdefault(slug, {"ok": 0, "fail": 0})["ok"] += 1

    # Recompute by_insurer + counters
    by_insurer = {}
    ok = 0
    fail = 0
    for r in results:
        slug = r.get("insurer_slug", "?")
        b = by_insurer.setdefault(slug, {"ok": 0, "fail": 0})
        if r.get("ok"):
            b["ok"] += 1; ok += 1
        else:
            b["fail"] += 1; fail += 1
    manifest["by_insurer"] = by_insurer
    manifest["total_entries"] = len(results)
    manifest["ok"] = ok
    manifest["fail"] = fail
    manifest["reconciled_at"] = subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True).stdout.strip()

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"Added {new_entries} entries ({new_with_url} with URL from transcripts, {new_without_url} pending URL recovery)")
    print(f"Manifest now: total={len(results)}, ok={ok}, fail={fail}")
    print(f"by_insurer:")
    for k, v in sorted(by_insurer.items()):
        marker = " (NEW)" if k in INSURER_NAMES and any(r.get("insurer_slug") == k and "reconciled" in (r.get("notes") or "") for r in []) else ""
        print(f"  {k}: ok={v['ok']}, fail={v['fail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
