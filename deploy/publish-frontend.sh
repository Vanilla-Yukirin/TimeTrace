#!/usr/bin/env bash
# publish-frontend.sh — EMERGENCY manual publish of the SPA to the xcy docroot.
#
# ⚠️ 正路是工作流：push 到 deploy 分支后，deploy.yml 的 publish-frontend job
# 会自动 build + rsync 到 xcy。这个脚本只是 CI 挂掉 / 无法触发工作流时的
# 手动兜底 —— 用它之前先想想为什么不能走工作流。
#
# Prereqs: local `ssh xcy` alias works (root) OR you hold a key for the
# low-priv `ghdeploy` user; node + npm installed.
#
# Usage:
#   bash deploy/publish-frontend.sh            # uses `xcy` ssh alias (root)
#   XCY=ghdeploy@103.117.123.204 XCY_PORT=22000 bash deploy/publish-frontend.sh

set -euo pipefail
cd "$(dirname "$0")/.."

XCY="${XCY:-xcy}"                       # ssh destination (alias or user@host)
XCY_PORT="${XCY_PORT:-}"                # empty → let ssh config decide
DOCROOT="${DOCROOT:-/var/www/timetrace.yukirin.me}"

RSH="ssh${XCY_PORT:+ -p ${XCY_PORT}}"

echo "==> building SPA (tsc + vite)"
npm ci --prefix frontend
npm run build --prefix frontend         # → frontend-dist/

echo "==> publishing to ${XCY}:${DOCROOT} (assets first, index.html last)"
# Hashed assets first; old ones stay behind for tabs still on the old page.
rsync -rtz -e "$RSH" --exclude index.html frontend-dist/ "${XCY}:${DOCROOT}/"
# Flip the pointer last so no visitor ever sees index.html referencing
# not-yet-uploaded chunks.
rsync -tz -e "$RSH" frontend-dist/index.html "${XCY}:${DOCROOT}/index.html"

echo "==> published bundle: $(grep -oE 'index-[A-Za-z0-9_-]+\.js' frontend-dist/index.html | head -1)"
