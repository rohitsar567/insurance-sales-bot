#!/usr/bin/env bash
# Install the four cron jobs as macOS LaunchAgents.
#
# Scripts go to  ~/Library/Scripts/insurance-bot/      (TCC-safe location)
# Plists go to   ~/Library/LaunchAgents/com.rohit.insurancebot.*.plist
# Logs go to     ~/Library/Logs/insurance-bot/
#
# Schedule:
#   daily-link-rot   03:00  → check_link_rot.py
#   weekly-pdf       Sun 04:00 → check_pdf_etags.py
#   monthly-premium  1st 06:00 → refresh_premiums.py
#
# Quarterly rebuild is manual: bash tools/quarterly_rebuild.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$HOME/Library/Scripts/insurance-bot"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/insurance-bot"

# Prefer the project venv (httpx is installed there). Fall back to system python.
if [ -x "$PROJECT_ROOT/.venv/bin/python3" ]; then
  PY="$PROJECT_ROOT/.venv/bin/python3"
else
  PY="$(command -v python3)"
fi
echo "[install_crons] using python: $PY"

mkdir -p "$SCRIPT_DIR" "$PLIST_DIR" "$LOG_DIR"

# 1. Write wrapper scripts in ~/Library/Scripts/ that cd into the project and
#    invoke python3 on the canonical tools/*.py. The wrappers MUST live here
#    (not in ~/Documents/) because macOS TCC blocks launchd from reading
#    ~/Documents/* without explicit Full Disk Access (see cache-sweeper
#    incident 2026-05-12).

cat > "$SCRIPT_DIR/run_link_rot.sh" <<EOF
#!/usr/bin/env bash
cd "$PROJECT_ROOT"
"$PY" tools/check_link_rot.py >> "$LOG_DIR/link_rot.stdout.log" 2>> "$LOG_DIR/link_rot.stderr.log"
EOF

cat > "$SCRIPT_DIR/run_pdf_etags.sh" <<EOF
#!/usr/bin/env bash
cd "$PROJECT_ROOT"
"$PY" tools/check_pdf_etags.py >> "$LOG_DIR/pdf_etags.stdout.log" 2>> "$LOG_DIR/pdf_etags.stderr.log"
EOF

cat > "$SCRIPT_DIR/run_refresh_premiums.sh" <<EOF
#!/usr/bin/env bash
cd "$PROJECT_ROOT"
"$PY" tools/refresh_premiums.py >> "$LOG_DIR/premium_refresh.stdout.log" 2>> "$LOG_DIR/premium_refresh.stderr.log"
EOF

chmod +x "$SCRIPT_DIR"/*.sh

# 2. LaunchAgent plists

write_plist() {
  local label="$1" script="$2" schedule_xml="$3"
  cat > "$PLIST_DIR/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$SCRIPT_DIR/$script</string>
    </array>
$schedule_xml
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/$label.out.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/$label.err.log</string>
</dict>
</plist>
PLIST
}

# Daily 03:00
write_plist "com.rohit.insurancebot.linkrot" "run_link_rot.sh" "    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>"

# Weekly Sunday 04:00 (Weekday 0 = Sunday)
write_plist "com.rohit.insurancebot.pdfetags" "run_pdf_etags.sh" "    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>4</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>"

# Monthly 1st 06:00
write_plist "com.rohit.insurancebot.premiums" "run_refresh_premiums.sh" "    <key>StartCalendarInterval</key>
    <dict>
        <key>Day</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>"

# 3. Load them (unload first to ensure idempotency)
for label in com.rohit.insurancebot.linkrot com.rohit.insurancebot.pdfetags com.rohit.insurancebot.premiums; do
  launchctl unload "$PLIST_DIR/$label.plist" 2>/dev/null || true
  launchctl load   "$PLIST_DIR/$label.plist"
  echo "[install_crons] loaded $label"
done

echo ""
echo "Installed LaunchAgents:"
launchctl list | grep com.rohit.insurancebot || echo "(none visible — check log dir)"

# ---- TCC self-test ----
# launchd-spawned processes cannot read ~/Documents/ unless the spawning binary
# (bash or python) has Full Disk Access. Test by running a tiny job and
# inspecting stderr for "Operation not permitted".
echo ""
echo "[install_crons] running TCC self-test..."
TEST_OUT="$(mktemp)"
launchctl start com.rohit.insurancebot.linkrot
sleep 6
if grep -q "Operation not permitted" "$LOG_DIR"/link_rot.stderr.log 2>/dev/null || \
   grep -q "Operation not permitted" "$LOG_DIR"/com.rohit.insurancebot.linkrot.err.log 2>/dev/null; then
  cat <<'WARN'

╔════════════════════════════════════════════════════════════════════════════╗
║  TCC BLOCKING DETECTED — cron jobs cannot read ~/Documents/                ║
║                                                                            ║
║  macOS requires Full Disk Access to be granted to the binaries that        ║
║  launchd uses. Open System Settings → Privacy & Security → Full Disk       ║
║  Access, and add BOTH:                                                     ║
║                                                                            ║
║    1. /bin/bash                                                            ║
║    2. The project's Python:                                                ║
WARN
  echo "         $PY"
  cat <<'WARN'
║                                                                            ║
║  After adding, click the toggle ON for each, then re-run:                  ║
║      bash tools/install_crons.sh                                           ║
║                                                                            ║
║  Opening Settings pane for you now...                                      ║
╚════════════════════════════════════════════════════════════════════════════╝
WARN
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
  exit 2
else
  echo "[install_crons] TCC ok — jobs can read project files"
fi
rm -f "$TEST_OUT"

echo ""
echo "Logs land in:  $LOG_DIR/"
echo "Scripts in:    $SCRIPT_DIR/"
echo "Plists in:     $PLIST_DIR/com.rohit.insurancebot.*.plist"
echo ""
echo "Manual trigger:  bash tools/quarterly_rebuild.sh"
echo "Force a run:     launchctl start com.rohit.insurancebot.linkrot"
echo "Disable:         launchctl unload ~/Library/LaunchAgents/com.rohit.insurancebot.<x>.plist"
