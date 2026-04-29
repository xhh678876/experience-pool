#!/usr/bin/env bash
# Helper that pulls a trajectory out of `claude --output-format json` envelopes
# and uploads it. Useful as a `Stop` hook in ~/.claude/settings.json:
#
#   {"hooks": {"Stop": [{"command": "experience-pool/scripts/auto-upload.sh"}]}}
#
# Reads the path to the session JSON via $CLAUDE_SESSION_PATH (set by the harness).

set -euo pipefail

SESSION_PATH="${CLAUDE_SESSION_PATH:-${1:-}}"
TASK="${EXP_TASK:-misc}"
MODEL="${EXP_MODEL:-claude-haiku-4-5}"
SENSITIVITY="${EXP_SENSITIVITY:-medium}"
ACL="${EXP_ACL:-private}"

if [ -z "$SESSION_PATH" ] || [ ! -f "$SESSION_PATH" ]; then
    echo "no trajectory at $SESSION_PATH" >&2
    exit 0  # don't abort the hook chain
fi

# Convert the session JSONL to {trajectory: [...]} shape.
TRAJ=$(mktemp -t exp_auto_upload).json
python3 - "$SESSION_PATH" "$TRAJ" <<'PY'
import json, sys, pathlib
src, dst = sys.argv[1], sys.argv[2]
turns = []
for line in pathlib.Path(src).read_text().splitlines():
    if not line.strip():
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    role = msg.get("role") or msg.get("type")
    content = msg.get("content") or msg.get("result") or msg.get("text", "")
    if role and content:
        turns.append({"role": role, "content": content if isinstance(content, str) else json.dumps(content)})
pathlib.Path(dst).write_text(json.dumps({"trajectory": turns}))
PY

exp push --task "$TASK" --model "$MODEL" --file "$TRAJ" \
    --sensitivity "$SENSITIVITY" --acl "$ACL" || {
    echo "exp push failed; trajectory left at $TRAJ" >&2
    exit 0
}
rm -f "$TRAJ"
