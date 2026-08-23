#!/usr/bin/env bash
# publish-frontend.sh — EMERGENCY manual SPA publish to the home nginx gateway.
#
# ⚠️ 正路是 pull-based 更新器：生产镜像内已经携带同 SHA 的 SPA，部署机
# 执行 `timetrace-update` 会同时切换后端与前端。这个脚本只是在镜像
# 发布或更新器无法使用时的手动兜底，使用前需要 owner 明确批准。
#
# Prereqs: local `ssh yukirin-server-2v4G` alias works; node + npm installed.
#
# Usage:
#   bash deploy/publish-frontend.sh
#   TARGET=vanilla@121.43.33.13 TARGET_PORT=10089 bash deploy/publish-frontend.sh

set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${TARGET:-yukirin-server-2v4G}" # ssh alias or user@host
TARGET_PORT="${TARGET_PORT:-}"          # empty → let ssh config decide
WEB_ROOT="${WEB_ROOT:-/srv/timetrace/web}"
RELEASE_ID="${RELEASE_ID:-$(git rev-parse HEAD)}"
RELEASE="${WEB_ROOT}/releases/${RELEASE_ID}"

SSH=(ssh)
if [[ -n "${TARGET_PORT}" ]]; then
  SSH+=(-p "${TARGET_PORT}")
fi
RSH="ssh${TARGET_PORT:+ -p ${TARGET_PORT}}"

echo "==> building SPA (tsc + vite)"
npm ci --prefix frontend
npm run build --prefix frontend         # → frontend-dist/

echo "==> publishing immutable release ${TARGET}:${RELEASE}"
"${SSH[@]}" "$TARGET" \
  "set -e; test -d '${WEB_ROOT}/releases'; install -d -m 755 '${RELEASE}'"
rsync -rtz -e "$RSH" frontend-dist/ "${TARGET}:${RELEASE}/"

echo "==> validating release and switching ${WEB_ROOT}/current"
"${SSH[@]}" "$TARGET" bash -s -- "$WEB_ROOT" "$RELEASE_ID" <<'REMOTE'
set -euo pipefail
web_root=$1
ref=$2
release="${web_root}/releases/${ref}"
next_link="${web_root}/.current-${ref}"
previous=$(readlink "${web_root}/current")

test -s "${release}/index.html"
grep -F 'id="root"' "${release}/index.html" >/dev/null
test -n "$(find "${release}/assets" -type f -print -quit)"

ln -sfn "releases/${ref}" "${next_link}"
mv -Tf "${next_link}" "${web_root}/current"

if ! {
  curl -fsS --max-time 5 -H 'Host: timetrace.yukirin.me' \
    'http://127.0.0.1:8080/healthz' >/dev/null &&
  curl -fsS --max-time 5 -H 'Host: timetrace.yukirin.me' \
    'http://127.0.0.1:8080/' | grep -F 'id="root"' >/dev/null
}; then
  echo "nginx smoke test failed; restoring ${previous}" >&2
  ln -sfn "${previous}" "${next_link}"
  mv -Tf "${next_link}" "${web_root}/current"
  exit 1
fi
REMOTE

echo "==> published bundle: $(grep -oE 'index-[A-Za-z0-9_-]+\.js' frontend-dist/index.html | head -1)"
