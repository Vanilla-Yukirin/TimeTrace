#!/usr/bin/env bash
set -Eeuo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)

# Exercise the real probe with a deterministic curl transport.
curl() {
  local url=${!#}
  [[ "$*" == *"--noproxy *"* ]] || return 91
  [[ "$*" == *"Host: ${EXPECTED_HOST:-localhost}"* ]] || return 92
  [[ "$url" == "${EXPECTED_BASE}/healthz" || "$url" == "${EXPECTED_BASE}/" ]] || return 93
  case "$PROBE_CASE:$url" in
    health-failure:*/healthz) return 22 ;;
    spa-failure:*/) return 22 ;;
    wrong-page:*/) printf '<html>gateway error</html>\n' ;;
    *:*/healthz) printf '{"status":"ok"}\n' ;;
    *) printf '<html><div id="root"></div></html>\n' ;;
  esac
}
export -f curl
unset TIMETRACE_GATEWAY_URL TIMETRACE_GATEWAY_HOST
export EXPECTED_BASE=http://127.0.0.1:19446 PROBE_CASE=ok
bash "${repo_root}/deploy/check-web-gateway.sh"
export TIMETRACE_GATEWAY_URL=http://127.0.0.1:25001/
export EXPECTED_BASE=http://127.0.0.1:25001
export TIMETRACE_GATEWAY_HOST=gateway.example EXPECTED_HOST=gateway.example
bash "${repo_root}/deploy/check-web-gateway.sh"
for PROBE_CASE in health-failure spa-failure wrong-page; do
  export PROBE_CASE
  if bash "${repo_root}/deploy/check-web-gateway.sh"; then
    printf 'unexpected success: %s\n' "$PROBE_CASE" >&2
    exit 1
  fi
done
printf 'web gateway: default, override, HTTP failures, and wrong-page checks passed\n'
