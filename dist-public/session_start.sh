#!/usr/bin/env bash
# Claude Code SessionStart hook — runs once when a session starts.
#
# Two responsibilities:
#   1. Inject the [task-summary]: convention so agents can self-label
#      task boundaries in long sessions (helps the trajectory segmenter).
#   2. If consent.json says mode='prompt-on-start' for this agent/cwd,
#      pop the prompt NOW and record the decision as a session_override
#      so the Stop hook later doesn't re-ask.
#
# This hook should write its output to stdout (Claude Code reads it as
# additional system context). Errors must NEVER block the session.

set -eu

WRAPPER="${EXP_WRAPPER:-$HOME/.experience-pool/bin/exp}"
SID="${CLAUDE_SESSION_ID:-}"
CWD="${CLAUDE_PROJECT_DIR:-$PWD}"

# 1. Inject the [task-summary]: convention into agent context.
cat <<'EOF'
[experience-pool]
When you finish a discrete sub-task in this session, prefix a short
single line summary with "[task-summary]: " — this lets the experience
pool's segmenter split long sessions into independent task records.
Example: "[task-summary]: replaced layer1 sanitizer with 30+ rules"
EOF

# 2. Optional prompt-on-start gate.
if [ -z "$SID" ]; then
    exit 0
fi

# Quick mode read (non-interactive) — skip the prompt entirely if the
# decision is already 'always' or 'never'.
DECISION=$("$WRAPPER" consent decide --agent claude-code \
              --cwd "$CWD" --session "$SID" 2>/dev/null || echo ask)

case "$DECISION" in
    upload|never)
        # Decision already cached or hard-set; nothing to do.
        exit 0
        ;;
    ask)
        # Only drive the start-time prompt when the consent file
        # explicitly opted into prompt-on-start. Otherwise we leave
        # the decision for the Stop hook to ask at upload time.
        MODE=$("$WRAPPER" consent show 2>/dev/null \
                | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("agents",{}).get("claude-code",{}).get("mode") or d.get("mode","ask"))' \
                2>/dev/null || echo ask)
        if [ "$MODE" = "prompt-on-start" ]; then
            "$WRAPPER" consent decide --interactive \
                --agent claude-code --cwd "$CWD" --session "$SID" \
                >/dev/null 2>&1 || true
        fi
        ;;
esac

exit 0
