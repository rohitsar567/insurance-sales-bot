# ADR-029 — ChromaDB HNSW bloat tripwire + LaunchAgent purge

**Status:** Accepted — 2026-05-14
**Owner:** Rohit Saraf
**Trigger incident:** D-001 in [`80-audit/ENTERPRISE_AUDIT.md`](../../80-audit/ENTERPRISE_AUDIT.md)

## Context

On 2026-05-14, a single fact-find / ingest session produced **`link_lists.bin` of 277 GB logical / 136 GB on-disk** inside the ChromaDB HNSW persistence directory (`rag/_hf_dataset_backup/rag/vectors/<collection-id>/`). The actual vector data file (`data_level0.bin`) was only 12 MB. The bloat ratio was ~22,000×. Free disk on the workstation went from ~137 GB to 50 MiB in about 45 minutes — the entire OS became unusable until the file was deleted.

The pathology is a known ChromaDB issue: under certain add/delete cycles, the HNSW link-graph file can grow with sparse holes (`lseek + write` past EOF) producing massive logical sizes. Even when on-disk size is smaller, the file still exhausts free space.

The pre-existing safeguards missed this:

- `cache-sweeper.sh` only swept `~/Library/Caches/com.apple.python/.../pip-target-*` (pip target dirs).
- `cache-tripwire.sh` only watched `~/Library/Caches` total size.
- Neither covered `~/Developer/` or any project-side artifacts.

Compounding this: the script that *should* have done corpus link-rot checks (and would have surfaced the corruption earlier) was a LaunchAgent pointing to `~/Documents/Personal/AI Work/Insurance Sales Bot`, a path that doesn't exist. The script had been silently failing every scheduled run for 18+ days.

## Decision

**Three independent safety layers.** Each layer alone could prevent the next incident; all three together provide defense in depth.

### Layer 1 — In-process tripwire (smallest blast radius)

`rag/ingest.py` defines `HNSW_BLOAT_THRESHOLD_BYTES = 500 MB` (500× the expected `link_lists.bin` size for the corpus). After every `collection.add(...)` call, `_abort_if_hnsw_bloated()` walks `settings.VECTORS_DIR.rglob("link_lists.bin")` and raises `RuntimeError` if any file exceeds the threshold. The same guard is imported and called from `tools/ingest_kb_summaries.py` and `tools/ingest_reviews.py` — every writer is protected.

Effect: a runaway HNSW growth is bounded to a single ingest batch — typically dozens of MB, not hundreds of GB.

### Layer 2 — Out-of-process auto-purge (catches what slips past Layer 1)

LaunchAgent `com.rohit.insurancebot.vectorbloat` runs hourly:

- Warns at `_hf_dataset_backup/ > 5 GB` (notification + log).
- **Auto-deletes the whole `_hf_dataset_backup/` directory at > 20 GB.** The canonical dataset lives on HuggingFace Hub at `rohitsar567/insurance-bot-data`; the local copy is re-cloneable in seconds.
- Independently warns on any `link_lists.bin > 1 GB` anywhere under the project — catches the same pathology even if `_hf_dataset_backup/` itself stays small.

The script writes heartbeats (`scan start (backup=yes|no)` / `scan done`) every run, so a silent TCC-blocked failure (the failure mode of the broken `thesis-link-rot` LaunchAgent) would be visible in the log immediately.

### Layer 3 — System-wide disk-free tripwire (catches everything)

LaunchAgent `com.rohit.disk-free-tripwire` runs every 15 minutes. Warns at `<20 GB` free on the data volume; **critical alert at `<8 GB`**, which also dumps every subdirectory of `~/Developer` larger than 1 GB to the log. So if a *different* runaway eats the disk (not just ChromaDB), the user sees both the alert and the offender's path within 15 minutes.

### Where LaunchAgents live

All three layer-2 + layer-3 scripts live under `~/Library/Scripts/` (NOT `~/Documents/`), per the TCC-blocks-launchd-in-Documents incident memory. macOS Privacy/Security TCC blocks `launchd` from executing scripts inside iCloud-synced `~/Documents/` paths, silently exit-126. This had bitten us on a separate `cache-sweeper.sh` placement 18 days prior.

## Consequences

| Win | Cost |
|---|---|
| Bloat is contained at three different scales (in-process / hourly / 15-min) — at least one layer fires before disk fills | Three LaunchAgents to keep loaded + heartbeated |
| Production HF Space is unaffected — the bloat was always local-dev only, and the deployed Docker image rebuilds vectors from HF Hub at build time anyway | The 500 MB in-process threshold is arbitrary; a corpus 100× the current one would need tuning |
| `~/Library/Scripts/` placement prevents the silent-TCC-failure regression | LaunchAgent plists must be re-loaded after macOS upgrades; `launchctl list \| grep com.rohit` is the heartbeat check |
| The audit doc + this ADR + the in-line comments in `rag/ingest.py` document the actual ChromaDB bug so a future engineer can fix it upstream rather than just containing it | None |

## Related

- D-001 in [`80-audit/ENTERPRISE_AUDIT.md`](../../80-audit/ENTERPRISE_AUDIT.md)
- [`rag/ingest.py::_abort_if_hnsw_bloated`](../../rag/ingest.py)
- `~/Library/Scripts/insurance-bot/check-vector-bloat.sh`
- `~/Library/Scripts/cache-prevention/disk-free-tripwire.sh`
- ChromaDB upstream issue tracker (see HNSW persistence stability discussions)
