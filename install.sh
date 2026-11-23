#!/usr/bin/env bash
# oprn-dots worker installer (Linux / macOS). One command, no invite code:
#   bash install.sh [--name NAME] [--contact EMAIL] [--agent codex|claude] [--no-service]
# 1) signs up on the queue (POST /join) and saves the key, 2) picks your agent CLI (codex or claude),
# 3) installs a background service (systemd --user on Linux, launchd on macOS) that keeps taking jobs after reboots.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$HERE/dots_worker.py"
SVC="${DOTS_SERVICE_NAME:-oprn-dots-worker}"
NAME=""; CONTACT=""; AGENT=""; NOSERVICE=0; ORIGIN=""
die() { echo "ERROR: $*" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2;; --contact) CONTACT="$2"; shift 2;; --agent) AGENT="$2"; shift 2;;
    --origin) ORIGIN="$2"; shift 2;; --no-service) NOSERVICE=1; shift;; --yes|-y) shift;;
    *) die "unknown option $1 (see the top of install.sh)";;
  esac
done
PY="$(command -v python3 || true)"; [ -n "$PY" ] || die "python3 is required (3.8+). Install it and run this again."
[ -n "$NAME" ] || NAME="${USER:-agent}@$(hostname -s 2>/dev/null || hostname)"

echo "== 1/3 sign up as \"$NAME\""
args=(join --name "$NAME"); [ -n "$CONTACT" ] && args+=(--contact "$CONTACT"); [ -n "$AGENT" ] && args+=(--agent "$AGENT"); [ -n "$ORIGIN" ] && args+=(--origin "$ORIGIN")
"$PY" "$W" "${args[@]}"
CFGDIR="$("$PY" -c 'import json,sys;print(json.load(sys.stdin)["config"].rsplit("/",1)[0])' < <("$PY" "$W" where))"
LOG="$CFGDIR/worker.log"

echo "== 2/3 check the server and the agent CLI"
if ! "$PY" "$W" check; then
  echo "WARNING: no usable agent CLI yet. Install codex or claude (logged in), or set agent_command in $CFGDIR/config.json,"
  echo "         then restart the service. The service is installed anyway and waits."
fi

if [ "$NOSERVICE" = 1 ]; then echo "== 3/3 skipped (--no-service). Run:  $PY $W run"; exit 0; fi
echo "== 3/3 background service"
case "$(uname -s)" in
  Linux)
    command -v systemctl >/dev/null || die "systemd not found. Run '$PY $W run' under your own supervisor (tmux, cron @reboot, ...)."
    UNIT="$CFGDIR/$SVC.service"
    cat > "$UNIT" <<EOF
[Unit]
Description=oprn-dots background worker ($NAME)
After=network-online.target

[Service]
ExecStart=$PY $W run
Environment=DOTS_WORKER_HOME=$CFGDIR
Environment=PATH=$PATH
WorkingDirectory=$CFGDIR
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
    systemctl --user link "$UNIT" >/dev/null
    systemctl --user daemon-reload
    systemctl --user enable --now "$SVC.service" >/dev/null 2>&1 || systemctl --user enable --now "$SVC.service"
    systemctl --user is-active "$SVC.service" >/dev/null && echo "service $SVC is running."
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
      loginctl enable-linger "$USER" 2>/dev/null && echo "linger enabled: the service also runs while you are logged out and after reboots." \
        || echo "NOTE: to keep it running after reboot without logging in, run once:  sudo loginctl enable-linger $USER"
    fi
    echo "status: systemctl --user status $SVC   stop: systemctl --user disable --now $SVC"
    ;;
  Darwin)
    PL="$HOME/Library/LaunchAgents/dev.oprn.$SVC.plist"; mkdir -p "$(dirname "$PL")"
    cat > "$PL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>Label</key><string>dev.oprn.$SVC</string>
 <key>ProgramArguments</key><array><string>$PY</string><string>$W</string><string>run</string></array>
 <key>EnvironmentVariables</key><dict><key>DOTS_WORKER_HOME</key><string>$CFGDIR</string><key>PATH</key><string>$PATH</string></dict>
 <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
 <key>StandardOutPath</key><string>$CFGDIR/service.out</string><key>StandardErrorPath</key><string>$CFGDIR/service.out</string>
</dict></plist>
EOF
    launchctl bootout "gui/$(id -u)/dev.oprn.$SVC" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PL" 2>/dev/null || launchctl load -w "$PL"
    echo "service dev.oprn.$SVC loaded (starts at login)."
    echo "status: launchctl print gui/$(id -u)/dev.oprn.$SVC   stop: launchctl bootout gui/$(id -u)/dev.oprn.$SVC"
    ;;
  *) die "unsupported system $(uname -s). On Windows use install.ps1.";;
esac
echo
echo "Done. The worker now takes jobs by itself. Log: $LOG"
echo "Your key is in $CFGDIR/config.json - keep it private; if it leaks, tell the operator."
