#!/usr/bin/env bash
# Verify the intranet expool plugin distribution without uploading anything.

set -euo pipefail

UI_BASE="${EXP_UI_PUBLIC_URL:-}"
PLUGIN_BASE="${EXP_PLUGIN_BASE:-${EXP_PUBLIC_BASE_URL:-${EXP_PUBLIC_API_BASE:-${EXP_BIND_BASE_URL:-}}}}"

usage() {
  cat <<'EOF'
Usage: check-plugin-deploy.sh [--ui URL] [--base URL]

Checks the current intranet plugin deployment:
  - /v1/plugin/package metadata
  - /plugins/expool.tgz download + sha256
  - /plugins/install.sh download + bash syntax
  - /plugins UI page contains the expected install/bind/auto commands

No upload, bind, or auto-upload commands are executed.

Environment:
  EXP_UI_PUBLIC_URL  UI proxy URL, e.g. https://.../proxy/3002
  EXP_PLUGIN_BASE    Gateway/API proxy URL, e.g. https://.../proxy/3080
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ui)
      UI_BASE="${2:?missing value for --ui}"
      shift 2
      ;;
    --base)
      PLUGIN_BASE="${2:?missing value for --base}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'check-plugin-deploy: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

clean_url() {
  printf '%s' "$1" | sed 's#/$##'
}

derive_base_from_ui() {
  local ui="$1"
  printf '%s' "$ui" | sed -E 's#/proxy/[0-9]+/?$#/proxy/3080#'
}

if [ -z "$PLUGIN_BASE" ] && [ -n "$UI_BASE" ]; then
  PLUGIN_BASE="$(derive_base_from_ui "$UI_BASE")"
fi
if [ -z "$UI_BASE" ] && [ -n "$PLUGIN_BASE" ]; then
  UI_BASE="$(printf '%s' "$PLUGIN_BASE" | sed -E 's#/proxy/[0-9]+/?$#/proxy/3002#')"
fi
if [ -z "$PLUGIN_BASE" ] || [ -z "$UI_BASE" ]; then
  printf 'check-plugin-deploy: missing URL. Pass --ui and/or --base.\n' >&2
  exit 2
fi

PLUGIN_BASE="$(clean_url "$PLUGIN_BASE")"
UI_BASE="$(clean_url "$UI_BASE")"
WORK="${TMPDIR:-/tmp}/expool-plugin-check.$$"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

need() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'check-plugin-deploy: missing required command: %s\n' "$1" >&2
    exit 2
  }
}

need curl
need python3
need sha256sum
need bash

printf '[check-plugin] base=%s\n' "$PLUGIN_BASE"
printf '[check-plugin] ui=%s\n' "$UI_BASE"

curl --noproxy '*' -fsSL "$PLUGIN_BASE/v1/plugin/package" -o "$WORK/package.json"
python3 - "$WORK/package.json" <<'PY' > "$WORK/meta.env"
import json
import shlex
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
required = [
    "name", "version", "filename", "size_bytes", "sha256",
    "download_url", "install_script_url", "install_script_command",
    "install_command",
]
missing = [key for key in required if not data.get(key)]
if missing:
    raise SystemExit(f"missing metadata fields: {', '.join(missing)}")
if data["name"] != "@haohui666/expool-plugin":
    raise SystemExit(f"unexpected package name: {data['name']}")
for key in ("download_url", "install_script_url"):
    print(f"{key.upper()}={shlex.quote(str(data[key]))}")
print(f"VERSION={shlex.quote(str(data['version']))}")
print(f"SHA256={shlex.quote(str(data['sha256']))}")
print(f"SIZE={shlex.quote(str(data['size_bytes']))}")
PY
. "$WORK/meta.env"
printf '[check-plugin] package=%s sha256=%s size=%s\n' "$VERSION" "$SHA256" "$SIZE"

curl --noproxy '*' -fsSL "$DOWNLOAD_URL" -o "$WORK/expool.tgz"
got="$(sha256sum "$WORK/expool.tgz" | awk '{print $1}')"
if [ "$got" != "$SHA256" ]; then
  printf 'check-plugin-deploy: tgz sha256 mismatch: expected %s got %s\n' "$SHA256" "$got" >&2
  exit 1
fi
tar -tzf "$WORK/expool.tgz" >/dev/null
printf '[check-plugin] tgz ok\n'

curl --noproxy '*' -fsSL "$INSTALL_SCRIPT_URL" -o "$WORK/install.sh"
bash -n "$WORK/install.sh"
grep -q 'sha256 ok' "$WORK/install.sh"
grep -q 'expool-plugin install --agents' "$WORK/install.sh"
printf '[check-plugin] install.sh ok\n'

curl --noproxy '*' -fsSL "$UI_BASE/plugins" -o "$WORK/plugins.html"
for token in \
  'Experience Pool 插件' \
  'github.com/xhh678876/expool-mcp-plugin' \
  '@haohui666/expool-plugin' \
  '一键脚本安装' \
  '手动包安装' \
  'plugins/install.sh' \
  'bind+api' \
  'expool-plugin auto status' \
  '/expool:auto-off'
do
  grep -Fq "$token" "$WORK/plugins.html" || {
    printf 'check-plugin-deploy: UI missing token: %s\n' "$token" >&2
    exit 1
  }
done
printf '[check-plugin] UI ok\n'
printf '[check-plugin] ok\n'
