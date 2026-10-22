#!/usr/bin/env bash
# Check the loopback web gateway, not the backend alone or a public ingress.
# Override only for hosts whose Nginx listener uses a different port.
set -Eeuo pipefail

gateway_url=${TIMETRACE_GATEWAY_URL:-http://127.0.0.1:19446}
gateway_url=${gateway_url%/}
gateway_host=${TIMETRACE_GATEWAY_HOST:-localhost}

curl --noproxy '*' -fsS --max-time 5 -H "Host: ${gateway_host}" \
  "${gateway_url}/healthz" >/dev/null
curl --noproxy '*' -fsS --max-time 5 -H "Host: ${gateway_host}" \
  "${gateway_url}/" | grep -F 'id="root"' >/dev/null
