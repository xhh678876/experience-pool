#!/usr/bin/env bash
# End-to-end integration smoke for the experience pool.
#
# Exercises:
#   - registration of multiple agents on different teams
#   - sanitizer on a high-sensitivity trajectory containing PII + secrets
#   - extractor + judge via the real `claude` CLI
#   - delayed credit assignment when a child references a parent
#   - ACL enforcement (cross-team isolation, denied-read auditing)
#   - dashboard + leaderboard
#   - HMAC credential issuance
#   - Parquet export + roundtrip read

set -euo pipefail
ROOT_DIR="${EXP_ROOT:-/tmp/expintegration}"
EXPORT_DIR="${EXP_EXPORT_DIR:-/tmp/exp_export}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CORE="$HERE/core"

cd "$CORE"
rm -rf "$ROOT_DIR" "$EXPORT_DIR"
export EXP_ROOT="$ROOT_DIR"

echo "[1/8] register agents"
uv run expctl register --name alice --team platform > /dev/null
uv run expctl register --name bob --team data > /dev/null
uv run expctl register --name carol --team platform > /dev/null

echo "[2/8] push PII trajectory (sensitivity=high)"
PII_PATH=$(mktemp -t exp_pii).json
cat > "$PII_PATH" <<'JSON'
{"trajectory":[
  {"role":"user","content":"Email me the report at alice@corp.example.com — phone +1 415 555 0143. Use AKIAIOSFODNN7EXAMPLE for S3."},
  {"role":"assistant","content":"Will send. Calling internal-api.corp.example.com from 10.0.0.4."},
  {"role":"user","content":"Charge card 4111 1111 1111 1111 on completion."},
  {"role":"assistant","content":"Done. Receipt logged."}
]}
JSON
PII_OUT=$(uv run expctl push --agent alice --task pii_check --model claude-sonnet-4-6 \
    --file "$PII_PATH" --sensitivity high --acl team:platform)
PII_ID=$(echo "$PII_OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['experience_id'])")
echo "    pii experience: $PII_ID"

echo "[3/8] push clean parent trajectory"
CLEAN_PATH=$(mktemp -t exp_clean).json
cat > "$CLEAN_PATH" <<'JSON'
{"trajectory":[
  {"role":"user","content":"I have a CSV with sales records. Find the top-3 regions by total revenue."},
  {"role":"assistant","content":"Inspecting columns first, then group by region and sum revenue."},
  {"role":"assistant","content":"Top 3 regions: APAC, EMEA, AMER."}
]}
JSON
PARENT_OUT=$(uv run expctl push --agent alice --task csv_analysis --model claude-sonnet-4-6 \
    --file "$CLEAN_PATH" --sensitivity low --acl team:platform)
PARENT_ID=$(echo "$PARENT_OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['experience_id'])")
echo "    parent experience: $PARENT_ID"

echo "[4/8] push child referencing parent"
CHILD_PATH=$(mktemp -t exp_child).json
cat > "$CHILD_PATH" <<'JSON'
{"trajectory":[
  {"role":"user","content":"Same CSV but quarterly trends now. Use the same approach as before."},
  {"role":"assistant","content":"Reusing the playbook: group by quarter via pd.Grouper."},
  {"role":"assistant","content":"Q1 1.8M, Q2 1.4M, Q3 1.6M, Q4 0.9M."}
]}
JSON
uv run expctl push --agent alice --task csv_analysis --model claude-sonnet-4-6 \
    --file "$CHILD_PATH" --parents "$PARENT_ID" --sensitivity low --acl team:platform > /dev/null

echo "[5/8] verify parent Q updated by child reward"
uv run expctl get "$PARENT_ID" | python3 -c "
import sys, json
row = json.load(sys.stdin)
assert row['q_update_count'] >= 2, f'expected q_update_count>=2, got {row[\"q_update_count\"]}'
assert row['reuse_count'] >= 1, f'expected reuse_count>=1, got {row[\"reuse_count\"]}'
print(f'    q_update_count={row[\"q_update_count\"]}  reuse_count={row[\"reuse_count\"]}  OK')
"

echo "[6/8] verify cross-team ACL"
uv run expctl acl-search --agent bob --q "csv revenue" | python3 -c "
import sys, json
hits = json.load(sys.stdin)
assert len(hits) == 0, f'bob (data team) should not see team:platform rows, got {len(hits)}'
print('    bob (data) sees 0 hits — OK')
"
uv run expctl acl-search --agent carol --q "csv revenue" | python3 -c "
import sys, json
hits = json.load(sys.stdin)
assert len(hits) >= 1, f'carol (platform team) should see team:platform rows, got {len(hits)}'
print(f'    carol (platform) sees {len(hits)} hits — OK')
"
uv run expctl acl-get --agent bob "$PARENT_ID" | python3 -c "
import sys, json
r = json.load(sys.stdin)
assert r.get('error'), f'bob should be denied, got: {r}'
print('    bob denied direct fetch — OK')
"

echo "[7/8] dashboard + leaderboard"
uv run expctl dashboard | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['total_experiences'] >= 3
print(f'    total={d[\"total_experiences\"]}  pending_review={d[\"by_review_status\"].get(\"pending\",0)}')
"

echo "[7.5/8] upload skill bundle, push experience using it, verify Q propagates"
SKILL_DIR=$(mktemp -d /tmp/exp_skill.XXXXXX)
mkdir -p "$SKILL_DIR/csv-helper"
cat > "$SKILL_DIR/csv-helper/SKILL.md" <<'SKILLMD'
---
name: csv-helper
description: Aggregate transactional CSVs by region or quarter with safe column inspection.
version: 0.1.0
triggers:
  - csv
  - aggregation
---
## Usage
1. Inspect columns first.
2. Compute revenue if not pre-computed.
3. Group by the requested dimension and sum.
4. Sort descending and return top-N.
SKILLMD
SKILL_OUT=$(uv run expctl push-skill --agent alice --bundle "$SKILL_DIR/csv-helper" \
    --sensitivity low --acl team:platform --tag csv)
echo "$SKILL_OUT" | python3 -c "
import sys, json
r = json.load(sys.stdin)
assert r['sanitization_status'] == 'done', r
assert r['review_status'] == 'auto_approved', r
print(f'    pushed skill={r[\"name\"]}@{r[\"version\"]}  files={r[\"file_count\"]}  sha256={r[\"bundle_sha256\"][:12]}…')
"
USING_PATH=$(mktemp -t exp_using).json
cat > "$USING_PATH" <<'JSON'
{"trajectory":[
  {"role":"user","content":"Use csv-helper to find top regions by revenue."},
  {"role":"assistant","content":"Following the playbook: inspecting columns, computing revenue, grouping by region."},
  {"role":"assistant","content":"APAC, EMEA, AMER. Done."}
]}
JSON
uv run expctl push --agent alice --task csv_analysis --model claude-sonnet-4-6 \
    --file "$USING_PATH" --uses-skill csv-helper --sensitivity low --acl team:platform > /dev/null
uv run expctl get-skill --name csv-helper | python3 -c "
import sys, json
r = json.load(sys.stdin)
assert r['invoke_count'] >= 1, f'expected invoke_count>=1, got {r[\"invoke_count\"]}'
assert r['q_update_count'] >= 1, f'expected q_update_count>=1, got {r[\"q_update_count\"]}'
print(f'    skill invoke_count={r[\"invoke_count\"]}  q_update_count={r[\"q_update_count\"]}  q_intent={r[\"q_intent\"]:.3f}  OK')
"
INSTALL_TARGET=$(mktemp -d /tmp/exp_skill_install.XXXXXX)
uv run expctl install-skill --name csv-helper --target "$INSTALL_TARGET" --agent bob > /dev/null
test -f "$INSTALL_TARGET/SKILL.md" || { echo "    SKILL.md missing after install"; exit 1; }
echo "    skill installed back to $INSTALL_TARGET — OK"

echo "[8/8] issue credential + export to parquet"
uv run expctl issue-credential --name alice > /dev/null
uv run expctl export --out "$EXPORT_DIR" | python3 -c "
import sys, json
r = json.load(sys.stdin)
assert r['row_count'] >= 3, r
print(f'    rows={r[\"row_count\"]}  partitions={r[\"partition_count\"]}')
"
uv run python -c "
import pyarrow.dataset as ds
t = ds.dataset('$EXPORT_DIR', format='parquet', partitioning=ds.HivePartitioning.discover()).to_table()
assert t.num_rows >= 3
assert 'task_type' in t.schema.names
print(f'    parquet roundtrip: {t.num_rows} rows, {len(t.schema.names)} cols')
"

echo
echo "ALL INTEGRATION CHECKS PASSED"
