#!/usr/bin/env bash
# Production cutover for the TimeTrace server container.
#
# Usage (normally called by GitHub Actions):
#   bash deploy-container.sh <git-sha> <ghcr-image-without-tag>

set -Eeuo pipefail

ref=${1:?usage: deploy-container.sh <git-sha> <image>}
image=${2:?usage: deploy-container.sh <git-sha> <image>}

runtime_root=/srv/timetrace/runtime
release_dir="${runtime_root}/releases/${ref}"
compose_file="${release_dir}/docker-compose.yml"
current_link="${runtime_root}/current"
config_dir=/srv/timetrace/config
env_file="${config_dir}/timetrace.env"
legacy_env="${HOME}/Github/TimeTrace/.env"
data_dir="${HOME}/TimeTraceData"
token_dir="${HOME}/.config/timetrace-server"
service=timetrace-server.service

previous_mode=none
previous_release=
cutover_started=0

log() {
  printf '==> %s\n' "$*"
}

compose() {
  TIMETRACE_IMAGE="${image}" TIMETRACE_IMAGE_TAG="${ref}" \
    docker compose -p timetrace -f "${compose_file}" "$@"
}

wait_for_health() {
  local attempts=${1:-24}
  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS --max-time 5 http://127.0.0.1:8765/healthz >/dev/null; then
      return 0
    fi
    sleep 5
  done
  return 1
}

wait_for_container_health() {
  local attempts=${1:-24}
  local i status
  for ((i = 1; i <= attempts; i++)); do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' timetrace-server 2>/dev/null || true)
    if [[ "${status}" == healthy ]]; then
      return 0
    fi
    if [[ "${status}" == unhealthy ]]; then
      return 1
    fi
    sleep 5
  done
  return 1
}

rollback() {
  local status=$?
  trap - ERR
  if ((cutover_started == 0)); then
    exit "${status}"
  fi

  log "deployment failed; collecting logs and restoring the previous runtime"
  docker logs --tail 200 timetrace-server 2>&1 || true
  compose down --remove-orphans || true

  if [[ "${previous_mode}" == docker && -n "${previous_release}" ]]; then
    local previous_compose="${previous_release}/docker-compose.yml"
    local previous_ref
    previous_ref=$(basename "${previous_release}")
    if [[ -f "${previous_compose}" ]]; then
      TIMETRACE_IMAGE="${image}" TIMETRACE_IMAGE_TAG="${previous_ref}" \
        docker compose -p timetrace -f "${previous_compose}" up -d --remove-orphans --pull never || true
      wait_for_health 24 || true
    fi
  elif [[ "${previous_mode}" == systemd ]]; then
    systemctl --user start "${service}" || true
    wait_for_health 24 || true
  fi

  exit "${status}"
}
trap rollback ERR

[[ "${ref}" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ref must be a full 40-character Git SHA" >&2
  exit 2
}
[[ "${image}" == ghcr.io/* ]] || {
  echo "image must be hosted on ghcr.io" >&2
  exit 2
}

test -f "${compose_file}"
test -d "${data_dir}"
test -d "${token_dir}"
install -d -m 700 "${runtime_root}/releases" "${config_dir}"

if [[ ! -f "${env_file}" ]]; then
  test -s "${legacy_env}"
  install -m 600 "${legacy_env}" "${env_file}"
  log "copied the legacy environment file once to ${env_file}"
fi
chmod 600 "${env_file}"

if [[ -L "${current_link}" ]]; then
  previous_release=$(readlink -f "${current_link}" || true)
fi
if docker inspect timetrace-server >/dev/null 2>&1; then
  previous_mode=docker
elif systemctl --user is-active --quiet "${service}" \
  || systemctl --user is-enabled --quiet "${service}"; then
  previous_mode=systemd
fi

log "validating Compose release ${ref}"
compose config --quiet
log "pulling ${image}:${ref} before changing the running service"
compose pull server

if [[ "${previous_mode}" == systemd ]]; then
  log "stopping the legacy systemd service for the first container cutover"
  systemctl --user stop "${service}"
  cutover_started=1

  backup_dir="${data_dir}/db/pre-docker-${ref}"
  if [[ ! -e "${backup_dir}" ]]; then
    install -d -m 700 "${backup_dir}"
    for name in timetrace.db timetrace.db-wal timetrace.db-shm; do
      if [[ -f "${data_dir}/db/${name}" ]]; then
        cp --reflink=auto --preserve=mode,timestamps \
          "${data_dir}/db/${name}" "${backup_dir}/${name}"
      fi
    done
    log "created a stopped-service SQLite snapshot at ${backup_dir}"
  fi
elif [[ "${previous_mode}" == docker ]]; then
  cutover_started=1
else
  if ss -ltn | grep -qE '127\.0\.0\.1:8765[[:space:]]'; then
    echo "port 127.0.0.1:8765 is occupied by an unmanaged process" >&2
    exit 1
  fi
  cutover_started=1
fi

log "starting the container"
compose up -d --remove-orphans --pull never
wait_for_health 36
wait_for_container_health 24

next_link="${runtime_root}/.current-${ref}"
ln -sfn "releases/${ref}" "${next_link}"
mv -Tf "${next_link}" "${current_link}"

if systemctl --user is-enabled --quiet "${service}"; then
  systemctl --user disable "${service}"
fi

trap - ERR
log "deployed ${image}:${ref}"
docker inspect --format 'container={{.Name}} image={{.Config.Image}} status={{.State.Status}} health={{.State.Health.Status}}' timetrace-server
