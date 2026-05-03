#!/usr/bin/env bash
# experience-pool universal installer.
#
# Usage:
#     curl -sSL https://expool.clawsii.com/install | bash
#     curl -sSL https://expool.clawsii.com/install | EXP_AGENT_NAME=alice EXP_TEAM=platform bash
#
# Env:
#     EXP_BASE_URL       gateway URL (default https://expool.clawsii.com)
#     EXP_AGENT_NAME     agent identifier (default $USER-$(hostname -s))
#     EXP_TEAM           team for the agent (default "default")
#     EXP_INSTALL_DIR    where to place the uploader (default ~/.experience-pool)
#     EXP_SKIP_HOOK      "1" to skip Stop hook patch into ~/.claude/settings.json

set -eu

BASE="${EXP_BASE_URL:-https://expool.clawsii.com}"
NAME="${EXP_AGENT_NAME:-${USER:-agent}-$(hostname -s 2>/dev/null || hostname)}"
TEAM="${EXP_TEAM:-default}"
INSTALL_DIR="${EXP_INSTALL_DIR:-$HOME/.experience-pool}"
BIN_DIR="$INSTALL_DIR/bin"
UPLOADER="$BIN_DIR/exp_uploader.py"
WRAPPER="$BIN_DIR/exp"
HOOK_SCRIPT="$BIN_DIR/auto_upload.sh"

note() { printf '\033[36m[exp]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[33m[exp]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[31m[exp]\033[0m %s\n' "$*" >&2; exit 1; }

note "installing experience-pool client into $INSTALL_DIR"
note "gateway: $BASE  agent: $NAME  team: $TEAM"

# ---------- prerequisites ----------
command -v python3 >/dev/null 2>&1 || fail "python3 not found (need >=3.9)"
PY_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3,9)))') || PY_OK=0
[ "$PY_OK" = "1" ] || fail "python3 >= 3.9 required"
command -v curl >/dev/null 2>&1 || fail "curl not found"

mkdir -p "$BIN_DIR"
chmod 700 "$INSTALL_DIR"

# ---------- 1. fetch uploader + annotator + consent module ----------
note "[1/4] downloading exp_uploader.py + exp_annotator.py + exp_consent.py"
TMP_UP="$(mktemp)"
TMP_ANN="$(mktemp)"
TMP_CON="$(mktemp)"
TMP_SS="$(mktemp)"
trap 'rm -f "$TMP_UP" "$TMP_ANN" "$TMP_CON" "$TMP_SS"' EXIT
curl -fsSL --max-time 30 "$BASE/exp_uploader.py" -o "$TMP_UP" \
    || fail "failed to download $BASE/exp_uploader.py"
head -1 "$TMP_UP" | grep -q '^#!/usr/bin/env python3' \
    || fail "downloaded uploader doesn't look right"
mv "$TMP_UP" "$UPLOADER"
chmod 755 "$UPLOADER"

# Consent module — required for the opt-in/opt-out flow. Sits next to
# exp_uploader.py so it gets imported by relative path.
CONSENT="$BIN_DIR/exp_consent.py"
if curl -fsSL --max-time 30 "$BASE/exp_consent.py" -o "$TMP_CON" 2>/dev/null \
   && head -1 "$TMP_CON" | grep -q '^#!/usr/bin/env python3'; then
    mv "$TMP_CON" "$CONSENT"
    chmod 755 "$CONSENT"
    note "      consent module installed"
else
    warn "      consent module not available; uploads will proceed without prompt"
fi

# Optional SessionStart hook for Claude Code — injects [task-summary]:
# convention and (when enabled) the prompt-on-start gate.
SS_HOOK="$BIN_DIR/session_start.sh"
if curl -fsSL --max-time 30 "$BASE/session_start.sh" -o "$TMP_SS" 2>/dev/null \
   && head -1 "$TMP_SS" | grep -q '^#!/usr/bin/env bash'; then
    mv "$TMP_SS" "$SS_HOOK"
    chmod 755 "$SS_HOOK"
fi

# annotator is optional — if download fails, uploader still works without --annotate
ANNOTATOR="$BIN_DIR/exp_annotator.py"
if curl -fsSL --max-time 30 "$BASE/exp_annotator.py" -o "$TMP_ANN" 2>/dev/null \
   && head -1 "$TMP_ANN" | grep -q '^#!/usr/bin/env python3'; then
    mv "$TMP_ANN" "$ANNOTATOR"
    chmod 755 "$ANNOTATOR"
    note "      annotator installed (run with: $WRAPPER push --annotate)"
else
    warn "      annotator not available; --annotate flag will be a no-op"
fi
trap - EXIT

# ---------- 2. shell wrapper ----------
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
exec python3 "$UPLOADER" --base "\${EXP_BASE_URL:-$BASE}" "\$@"
EOF
chmod 755 "$WRAPPER"

# ---------- 3. register agent (idempotent) ----------
CRED_DIR="$HOME/.experience-pool/credentials"
mkdir -p "$CRED_DIR"
chmod 700 "$CRED_DIR"
CRED_FILE="$CRED_DIR/$NAME.json"
if [ -f "$CRED_FILE" ]; then
    note "[2/4] credential already exists at $CRED_FILE (skipping register)"
else
    note "[2/4] registering agent $NAME on team $TEAM"
    "$WRAPPER" register --name "$NAME" --team "$TEAM" >/dev/null \
        || fail "register failed; check $BASE is reachable"
    note "      credential saved to $CRED_FILE"
fi

# ---------- 2.5. optional: install OpenAI Privacy Filter (~3GB) ----------
# When EXP_INSTALL_OPF=1, install the opf package + download model weights.
# This adds context-aware PII redaction (8 categories) on top of the
# regex-only pass that runs by default. Server-side OPF still runs as a
# defense-in-depth layer — installing it client-side just means raw L1
# never even leaves the host.
if [ "${EXP_INSTALL_OPF:-0}" = "1" ]; then
    note "[2.5] installing OpenAI Privacy Filter client-side (~3GB download)"
    if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
        warn "      pip not available; skipping OPF install"
    else
        OPF_PIP="python3 -m pip install --user --quiet"
        if $OPF_PIP "git+https://github.com/openai/privacy-filter.git" 2>/dev/null; then
            note "      opf package installed"
            note "      downloading model weights to ~/.opf/privacy_filter (first run)"
            python3 - <<'PY' || warn "      model download failed; OPF will retry on first push"
import os, sys
try:
    from opf._api import RedactorAPI
    ckpt = os.path.expanduser("~/.opf/privacy_filter")
    RedactorAPI(checkpoint=ckpt, device="cpu")
    print("      OPF model ready at " + ckpt, file=sys.stderr)
except Exception as e:
    print("      OPF model load deferred: " + str(e), file=sys.stderr)
    sys.exit(1)
PY
        else
            warn "      pip install failed; OPF disabled (regex-only redaction will still apply)"
        fi
    fi
else
    note "[2.5] OPF client-side skipped (set EXP_INSTALL_OPF=1 to enable; server-side OPF still active)"
fi

# ---------- 4. detect agents on this host & wire hooks ----------
note "[3/4] detecting local agent installations"
DETECTED=()
[ -d "$HOME/.claude/projects" ] && DETECTED+=("claude-code")
[ -d "$HOME/Library/Application Support/Cursor/User" ] && DETECTED+=("cursor")
[ -d "$HOME/.config/Cursor/User" ] && DETECTED+=("cursor")
[ -d "$HOME/.codex/sessions" ] && DETECTED+=("codex")
[ -d "$HOME/.hermes" ] || [ -d "$HOME/.openclaw" ] && DETECTED+=("hermes")
[ -d "$HOME/agents-chat" ] && DETECTED+=("agents-chat")
# de-dup
if [ "${#DETECTED[@]}" -gt 0 ]; then
    DETECTED=($(printf '%s\n' "${DETECTED[@]}" | awk '!seen[$0]++'))
fi

if [ "${#DETECTED[@]}" -eq 0 ]; then
    warn "      no local agent sessions found; you can still 'exp push-file --file traj.json'"
else
    note "      found: ${DETECTED[*]}"
fi

# ---------- 3.5. consent wizard — interactive opt-in/opt-out ----------
# Skipped when EXP_NONINTERACTIVE=1 (CI/curl-pipe) or stdin is not a TTY.
if [ "${EXP_NONINTERACTIVE:-0}" = "1" ] || [ ! -t 0 ]; then
    note "[3.5] consent wizard skipped (non-interactive); default mode = ask"
    "$WRAPPER" consent reset >/dev/null 2>&1 || true
elif [ "${EXP_SKIP_CONSENT:-0}" = "1" ]; then
    note "[3.5] consent wizard skipped (EXP_SKIP_CONSENT=1)"
else
    note "[3.5] consent setup — choose what to share"
    cat <<'BANNER' >&2

experience-pool defaults to ASK before each session upload.
You can pre-set per-agent rules below. Modes:

  [a]lways  upload every session, no prompt
  [n]ever   never upload (sessions stay on this host)
  [k]ask    ask each time (recommended, default)
  [s]kip    inherit the global mode

BANNER
    for agent in "${DETECTED[@]}"; do
        # Default suggestion based on agent type — keep secrets-rich
        # sources (cursor, hermes) opt-in; chat-only (claude-code) ask.
        DEFAULT="k"
        case "$agent" in
            cursor|hermes) DEFAULT="k" ;;
        esac
        printf "  %s → [a/n/k/s] (default %s): " "$agent" "$DEFAULT" >&2
        read -r choice || choice=""
        choice="${choice:-$DEFAULT}"
        case "$choice" in
            a|always)  "$WRAPPER" consent set --agent "$agent" --mode always   >/dev/null && note "      $agent → always" ;;
            n|never)   "$WRAPPER" consent set --agent "$agent" --mode never    >/dev/null && note "      $agent → never" ;;
            k|ask)     "$WRAPPER" consent set --agent "$agent" --mode ask      >/dev/null && note "      $agent → ask" ;;
            s|skip)    note "      $agent → (inherit global)" ;;
            *)         "$WRAPPER" consent set --agent "$agent" --mode ask      >/dev/null && warn "      unknown choice; defaulting to ask" ;;
        esac
    done

    printf "\n  Exclude any directories from upload? (space-separated globs, blank to skip)\n  e.g. ~/work/clients/** ~/.aws/** > " >&2
    read -r EXCLUDES || EXCLUDES=""
    if [ -n "$EXCLUDES" ]; then
        # shellcheck disable=SC2086
        for glob in $EXCLUDES; do
            "$WRAPPER" consent set --cwd "$glob" --mode never \
                --reason "set during install wizard" >/dev/null \
                && note "      $glob → never"
        done
    fi
    printf "\n  [exp] consent saved to ~/.experience-pool/consent.json\n" >&2
fi

# Auto-upload helper script (Stop hook for Claude Code).
# Honors consent.json: 'never' → exit silently, 'always' → upload,
# 'ask' → pop a prompt (osascript on mac / zenity on linux / terminal else)
# with a 30-second timeout that defaults to skip.
cat > "$HOOK_SCRIPT" <<EOF
#!/usr/bin/env bash
# Claude Code Stop hook — consent-aware session upload.
set -eu
SID="\${CLAUDE_SESSION_ID:-}"
CWD="\${CLAUDE_PROJECT_DIR:-\$PWD}"

# 1. Quick non-interactive check — if 'never', exit immediately.
DECISION=\$("$WRAPPER" consent decide --agent claude-code \\
                --cwd "\$CWD" --session "\$SID" 2>/dev/null || echo ask)

case "\$DECISION" in
    never)
        exit 0
        ;;
    upload)
        "$WRAPPER" push-latest --yes \\
            --source claude-code \\
            --task "\${EXP_TASK:-misc}" \\
            --sensitivity "\${EXP_SENSITIVITY:-medium}" \\
            --acl "\${EXP_ACL:-private}" >/dev/null 2>&1 || true
        ;;
    dry-run)
        "$WRAPPER" push-latest --dry-run \\
            --source claude-code >/dev/null 2>&1 || true
        ;;
    ask|*)
        # Drive the prompt; 'consent decide --interactive' returns the verb.
        ANSWER=\$("$WRAPPER" consent decide --interactive \\
                    --agent claude-code --cwd "\$CWD" \\
                    --session "\$SID" 2>/dev/null || echo skip)
        if [ "\$ANSWER" = "upload" ]; then
            "$WRAPPER" push-latest --yes \\
                --source claude-code \\
                --task "\${EXP_TASK:-misc}" \\
                --sensitivity "\${EXP_SENSITIVITY:-medium}" \\
                --acl "\${EXP_ACL:-private}" >/dev/null 2>&1 || true
        fi
        ;;
esac
EOF
chmod 755 "$HOOK_SCRIPT"

# Patch ~/.claude/settings.json (only if Claude Code is detected and not skipped).
if [ "${EXP_SKIP_HOOK:-0}" != "1" ] && [ -d "$HOME/.claude" ]; then
    SETTINGS="$HOME/.claude/settings.json"
    note "[4/5] patching Stop hook into $SETTINGS (zero-latency Claude Code upload)"
    python3 - "$SETTINGS" "$HOOK_SCRIPT" <<'PY'
import json, sys, pathlib, os
settings_path = pathlib.Path(sys.argv[1])
hook_cmd = sys.argv[2]
if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text())
    except Exception:
        backup = settings_path.with_suffix(".json.bak")
        backup.write_text(settings_path.read_text())
        print(f"[exp] existing settings.json was unreadable; backed up to {backup}", file=sys.stderr)
        data = {}
else:
    data = {}
hooks = data.setdefault("hooks", {})
stops = hooks.setdefault("Stop", [])
for entry in stops:
    if isinstance(entry, dict) and entry.get("command") == hook_cmd:
        sys.exit(0)
stops.append({"command": hook_cmd, "description": "experience-pool auto upload"})
settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(data, indent=2))
print(f"[exp] Stop hook installed in {settings_path}", file=sys.stderr)
PY

    # Also wire the SessionStart hook (idempotent).
    if [ -x "$SS_HOOK" ]; then
        python3 - "$SETTINGS" "$SS_HOOK" <<'PY'
import json, sys, pathlib
settings_path = pathlib.Path(sys.argv[1])
hook_cmd = sys.argv[2]
data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
hooks = data.setdefault("hooks", {})
starts = hooks.setdefault("SessionStart", [])
for entry in starts:
    if isinstance(entry, dict) and entry.get("command") == hook_cmd:
        sys.exit(0)
starts.append({"command": hook_cmd, "description": "experience-pool consent + task-summary"})
settings_path.write_text(json.dumps(data, indent=2))
print(f"[exp] SessionStart hook installed in {settings_path}", file=sys.stderr)
PY
    fi
else
    note "[4/5] skipping Claude Code Stop hook (EXP_SKIP_HOOK=1 or ~/.claude not present)"
fi

# ---------- 5. universal background daemon (covers all OTHER agents) ----------
TICK_INTERVAL="${EXP_TICK_INTERVAL_SECONDS:-120}"
AUTO_SOURCES="${EXP_AUTO_SOURCES:-claude-code,hermes,continue-dev,codex,agents-chat}"
AUTO_ACL="${EXP_AUTO_ACL:-private}"

if [ "${EXP_SKIP_DAEMON:-0}" = "1" ]; then
    note "[5/5] skipping background daemon (EXP_SKIP_DAEMON=1)"
elif [ "$(uname -s)" = "Darwin" ]; then
    note "[5/5] installing launchd LaunchAgent (every ${TICK_INTERVAL}s)"
    PLIST="$HOME/Library/LaunchAgents/com.experience-pool.daemon.plist"
    LOG_DIR="$HOME/Library/Logs/experience-pool"
    mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.experience-pool.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>$WRAPPER</string>
        <string>daemon-tick</string>
        <string>--max-per-source</string><string>5</string>
        <string>--acl</string><string>$AUTO_ACL</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>EXP_BASE_URL</key><string>$BASE</string>
        <key>EXP_AUTO_SOURCES</key><string>$AUTO_SOURCES</string>
        <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
    <key>StartInterval</key><integer>$TICK_INTERVAL</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$LOG_DIR/daemon.log</string>
    <key>StandardErrorPath</key><string>$LOG_DIR/daemon.err</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/com.experience-pool.daemon" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
        || launchctl load -w "$PLIST" 2>/dev/null \
        || warn "      launchctl bootstrap failed; you can load manually with: launchctl load -w $PLIST"
    note "      log: $LOG_DIR/daemon.log"
elif [ "$(uname -s)" = "Linux" ]; then
    note "[5/5] installing systemd user timer (every ${TICK_INTERVAL}s)"
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/expool-daemon.service" <<EOF
[Unit]
Description=Experience Pool background sync

[Service]
Type=oneshot
Environment=EXP_BASE_URL=$BASE
Environment=EXP_AUTO_SOURCES=$AUTO_SOURCES
ExecStart=$WRAPPER daemon-tick --max-per-source 5 --acl $AUTO_ACL
EOF
    cat > "$UNIT_DIR/expool-daemon.timer" <<EOF
[Unit]
Description=Run experience-pool sync every ${TICK_INTERVAL}s

[Timer]
OnBootSec=30
OnUnitActiveSec=${TICK_INTERVAL}
Unit=expool-daemon.service

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload 2>/dev/null \
        && systemctl --user enable --now expool-daemon.timer 2>/dev/null \
        || warn "      systemctl --user not available; enable manually: systemctl --user enable --now expool-daemon.timer"
else
    warn "[5/5] no scheduler for $(uname -s); install a cron entry manually:"
    warn "      */2 * * * * EXP_AUTO_SOURCES=$AUTO_SOURCES $WRAPPER daemon-tick --acl $AUTO_ACL"
fi

# Run one tick immediately so the user sees output and any backlog gets uploaded.
note "running first daemon tick now..."
"$WRAPPER" daemon-tick --max-per-source 3 --acl "$AUTO_ACL" 2>&1 | sed 's/^/      /' || true

cat <<EOF

✓ installed — auto-upload is now ON for: $AUTO_SOURCES
  every session you finish will be uploaded within ${TICK_INTERVAL}s
  (Claude Code uploads instantly via Stop hook).

  binary  : $WRAPPER
  uploader: $UPLOADER
  cred    : $CRED_FILE
  log     : ~/Library/Logs/experience-pool/daemon.log  (macOS)

control commands:
  $WRAPPER daemon-state                    # see what's been synced
  $WRAPPER daemon-reset --source claude-code   # force re-upload of one source
  $WRAPPER daemon-tick --dry-run -v        # preview without uploading

disable:
  launchctl bootout gui/\$(id -u)/com.experience-pool.daemon         # macOS
  systemctl --user disable --now expool-daemon.timer                 # Linux

tweak which agents auto-sync:
  EXP_AUTO_SOURCES=claude-code,hermes  curl -sSL $BASE/install | bash

EOF
