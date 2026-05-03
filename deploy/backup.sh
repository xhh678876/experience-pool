#!/usr/bin/env bash
# Hot backup for the standalone SQLite deployment.

set -euo pipefail

EXP_ROOT="${EXP_ROOT:-/var/lib/expool}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/expool}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

DB="$EXP_ROOT/pool.db"
OUT_DIR="$BACKUP_ROOT/$STAMP"
DB_OUT="$OUT_DIR/pool.db"
FILES_OUT="$OUT_DIR/files.tar.gz"

if [ ! -f "$DB" ]; then
  echo "missing database: $DB" >&2
  exit 1
fi

install -d -m 0700 "$OUT_DIR"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$DB_OUT'"
else
  python3 - "$DB" "$DB_OUT" <<'PY'
import sqlite3
import sys

src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY
fi
chmod 0600 "$DB_OUT"

tar -C "$EXP_ROOT" -czf "$FILES_OUT" \
  --ignore-failed-read \
  trajectories skills credentials 2>/dev/null || true
chmod 0600 "$FILES_OUT"

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$RETENTION_DAYS" -exec rm -rf {} +

echo "backup written: $OUT_DIR"
