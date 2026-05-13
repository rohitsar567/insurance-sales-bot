# Knowledge-base refresh schedule

This project's knowledge base — policy PDFs, premium anchors, insurer reviews,
all source URLs — would slowly rot without automation. Insurer sites change
filenames, IRDAI replaces annual reports, aggregator ratings move. The schedule
below keeps everything fresh without us touching it.

## What "cron" means

"Cron" is a unix term for a job that runs on a fixed schedule, unattended.
The name comes from `chronos` (Greek for "time"). The original `cron` daemon
on Linux servers reads a table of `min hour day month weekday command` rows
and fires each command at its scheduled time.

On macOS the modern equivalent is `launchd`, configured by XML plist files
under `~/Library/LaunchAgents/`. A LaunchAgent is essentially a cron entry —
it tells the OS "wake up at X, run this command, log the output." The macOS
scheduler honors these even when the laptop is asleep (it queues them and
runs at the next wake), and respects per-user permissions.

This project uses three LaunchAgents + one manual script. They run themselves
in the background. You don't have to remember anything.

## The four jobs

| Frequency | Job | Plist label | What it does | Auto-fix |
|---|---|---|---|---|
| Daily 03:00 | Link-rot | `com.rohit.insurancebot.linkrot` | HEAD every external URL in the KB | Wayback Machine snapshot; http↔https swap; query-string strip |
| Weekly Sun 04:00 | PDF freshness | `com.rohit.insurancebot.pdfetags` | ETag/Last-Modified diff for every policy PDF | Re-download + delete-from-Chroma + re-ingest |
| Monthly 1st 06:00 | Premium refresh | `com.rohit.insurancebot.premiums` | Re-fetch every premium anchor; detect price drift; HEAD aggregator URLs | Wayback if dead; >15% drift goes to MUST_FIX |
| Quarterly (manual) | Full rebuild | `bash tools/quarterly_rebuild.sh` | Wipe Chroma, re-ingest everything, re-extract schema, rebuild KB pages, run gold eval | — |

## Auto-fix philosophy

Every cron job is **detect → auto-fix → report**:

1. **Detect** — record the issue with structured logs in `~/Library/Logs/insurance-bot/`
2. **Auto-fix** — try every safe repair strategy applicable to that error class
3. **Report** — if and only if no fix worked, write to `MUST_FIX.md` at the project root and post a macOS notification

**Things we auto-fix without asking:**
- Dead URLs → swap to working Wayback snapshot
- Stale PDFs → re-download + re-ingest
- Cosmetic URL drift (http↔https, query-string ambiguity)

**Things we don't auto-fix (logged to MUST_FIX.md instead):**
- Premium price drift > 15% — a real rate change needs human eyeballs
- PDF re-ingest crashes — could mask a schema regression
- Hosts that block bots entirely — manually verify via Playwright

`MUST_FIX.md` is regenerated every run, so it always reflects current state.
If it's missing, everything is clean.

## What gets logged

```
~/Library/Logs/insurance-bot/
├── link_rot.log               # JSON-per-URL audit trail (append-only)
├── link_rot.stdout.log        # Summary stdout
├── link_rot.stderr.log        # Any crashes
├── pdf_etags.log              # ETag history per policy URL
├── premium_refresh.log        # Price-drift detail
└── com.rohit.insurancebot.*.{out,err}.log  # launchd's own view
```

Tail any log with `tail -f` to watch live.

## Operations

```bash
# Trigger any job manually
launchctl start com.rohit.insurancebot.linkrot
launchctl start com.rohit.insurancebot.pdfetags
launchctl start com.rohit.insurancebot.premiums

# Run the quarterly full rebuild (interactive)
bash tools/quarterly_rebuild.sh

# Check which agents are loaded + last exit code
launchctl list | grep com.rohit.insurancebot

# Disable a job
launchctl unload ~/Library/LaunchAgents/com.rohit.insurancebot.linkrot.plist

# Re-enable / re-install everything
bash tools/install_crons.sh
```

## Why scripts live in `~/Library/Scripts/` not the project

macOS Transparency, Consent, and Control (TCC) blocks launchd-spawned shells
from reading files in `~/Documents/` unless Full Disk Access is granted.
The wrapper scripts are installed under `~/Library/Scripts/insurance-bot/`
which TCC doesn't gate. Those wrappers `cd` into the project and invoke the
project's venv Python (which itself isn't TCC-restricted because it lives
inside a user-owned project directory).

This is the same lesson learned from the AI Thesis project's cache-sweeper
incident (May 2026) — LaunchAgent scripts under `~/Documents/` silently fail
with exit code 126 ("Operation not permitted") and never run.

## Why the venv Python, not the system Python

The system `python3` at `/Library/Developer/CommandLineTools/usr/bin/python3`
is itself TCC-restricted when invoked from launchd context. The project's
`.venv/bin/python3` is not (it's a binary inside the user's project, owned
by the user, not under any system path). Plus the venv has `httpx` already
installed — system Python doesn't.

## First-time install

```bash
cd "Insurance Sales Bot"
bash tools/install_crons.sh
```

The installer:
1. Writes wrapper shell scripts to `~/Library/Scripts/insurance-bot/`
2. Writes LaunchAgent plists to `~/Library/LaunchAgents/`
3. Loads each agent with `launchctl load`
4. Runs a TCC self-test by firing the link-rot job and checking stderr
5. If TCC is blocking, opens the relevant System Settings pane and exits 2
