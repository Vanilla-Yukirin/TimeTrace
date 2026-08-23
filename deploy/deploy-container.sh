#!/usr/bin/env bash
# Production cutover for the TimeTrace server container and bundled SPA.
#
# Usage (normally called by the host-side timetrace-update command):
#   bash deploy-container.sh <git-sha> <ghcr-image-without-tag>

set -Eeuo pipefail

ref=${1:?usage: deploy-container.sh <git-sha> <image>}
image=${2:?usage: deploy-container.sh <git-sha> <image>}

runtime_root=${TIMETRACE_RUNTIME_ROOT:-/srv/timetrace/runtime}
release_dir="${runtime_root}/releases/${ref}"
compose_file="${release_dir}/docker-compose.yml"
current_link="${runtime_root}/current"
config_dir=/srv/timetrace/config
env_file="${config_dir}/timetrace.env"
host_user=${TIMETRACE_HOST_USER:-vanilla}
host_home=${TIMETRACE_HOST_HOME:-/home/${host_user}}
install_target=${TIMETRACE_INSTALL_TARGET:-${host_home}/.local/bin/timetrace-update}
legacy_env="${host_home}/Github/TimeTrace/.env"
data_dir="${host_home}/TimeTraceData"
token_dir="${host_home}/.config/timetrace-server"
web_root=/srv/timetrace/web
web_release="${web_root}/releases/${ref}"
frontend_source="${release_dir}/frontend-dist"
service=timetrace-server.service

previous_mode=none
previous_release=
previous_image_ref=
previous_image_repository=
previous_image_tag=
previous_runtime_target=
previous_web_target=
legacy_was_enabled=0
cutover_started=0
runtime_switched=0
web_switched=0
web_staging=
backup_staging=

log() {
  printf '==> %s\n' "$*"
}

compose() {
  TIMETRACE_IMAGE_REF="${image}:${ref}" \
    TIMETRACE_IMAGE="${image}" \
    TIMETRACE_IMAGE_TAG="${ref}" \
    TIMETRACE_ENV_FILE="${env_file}" \
    TIMETRACE_DATA_DIR="${data_dir}" \
    TIMETRACE_TOKEN_DIR="${token_dir}" \
    docker compose -p timetrace -f "${compose_file}" "$@"
}

legacy_systemctl() {
  if [[ $(id -un) == "${host_user}" ]]; then
    systemctl --user "$@"
    return
  fi
  local host_uid
  host_uid=$(id -u "${host_user}")
  runuser -u "${host_user}" -- \
    env XDG_RUNTIME_DIR="/run/user/${host_uid}" systemctl --user "$@"
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
  if (($# > 0)); then
    status=$1
  fi
  trap - ERR
  trap '' HUP INT TERM
  if ((cutover_started == 0)); then
    exit "${status}"
  fi

  log "deployment failed; collecting logs and restoring the previous runtime"
  docker logs --tail 200 timetrace-server 2>&1 || true
  compose down --remove-orphans || true

  if ((runtime_switched == 1)); then
    local runtime_rollback_link="${runtime_root}/.current-rollback-${ref}"
    if [[ -n "${previous_runtime_target}" ]]; then
      ln -sfn "${previous_runtime_target}" "${runtime_rollback_link}"
      mv -Tf "${runtime_rollback_link}" "${current_link}"
    else
      rm -f -- "${current_link}"
    fi
  fi
  if ((web_switched == 1)); then
    local rollback_link="${web_root}/.current-rollback-${ref}"
    if [[ -n "${previous_web_target}" ]]; then
      ln -sfn "${previous_web_target}" "${rollback_link}"
      mv -Tf "${rollback_link}" "${web_root}/current"
    else
      rm -f -- "${web_root}/current"
    fi
  fi
  if [[ -n "${web_staging}" && -d "${web_staging}" ]]; then
    case "${web_staging}" in
      "${web_root}"/releases/.staging-*) rm -rf -- "${web_staging}" ;;
    esac
  fi
  if [[ -n "${backup_staging}" && -d "${backup_staging}" ]]; then
    case "${backup_staging}" in
      "${data_dir}"/db/.pre-docker-*) rm -rf -- "${backup_staging}" ;;
    esac
  fi

  if [[ "${previous_mode}" == docker && -n "${previous_release}" ]]; then
    local previous_compose="${previous_release}/docker-compose.yml"
    if [[ -f "${previous_compose}" ]]; then
      TIMETRACE_IMAGE_REF="${previous_image_ref}" \
        TIMETRACE_IMAGE="${previous_image_repository}" \
        TIMETRACE_IMAGE_TAG="${previous_image_tag}" \
        TIMETRACE_ENV_FILE="${env_file}" \
        TIMETRACE_DATA_DIR="${data_dir}" \
        TIMETRACE_TOKEN_DIR="${token_dir}" \
        docker compose -p timetrace -f "${previous_compose}" up -d --remove-orphans --pull never || true
      wait_for_health 24 || true
    fi
  elif [[ "${previous_mode}" == systemd ]]; then
    if ((legacy_was_enabled == 1)); then
      legacy_systemctl enable "${service}" || true
    fi
    legacy_systemctl start "${service}" || true
    wait_for_health 24 || true
  fi

  exit "${status}"
}
trap rollback ERR
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

[[ "${ref}" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ref must be a full 40-character Git SHA" >&2
  exit 2
}
[[ "${image}" == ghcr.io/* ]] || {
  echo "image must be hosted on ghcr.io" >&2
  exit 2
}

test -f "${compose_file}"
test -s "${frontend_source}/index.html"
test -d "${data_dir}"
test -d "${token_dir}"
install -d -m 700 "${runtime_root}/releases" "${config_dir}"
install -d -m 755 "${web_root}/releases"

if [[ ! -f "${env_file}" ]]; then
  if [[ -f "${legacy_env}" ]]; then
    install -m 600 "${legacy_env}" "${env_file}"
    log "copied the legacy environment file once to ${env_file}"
  else
    install -m 600 /dev/null "${env_file}"
    log "created an empty environment file at ${env_file}"
  fi
fi
chmod 600 "${env_file}"

if [[ -L "${current_link}" ]]; then
  previous_runtime_target=$(readlink "${current_link}" || true)
  previous_release=$(readlink -f "${current_link}" || true)
fi
if [[ -L "${web_root}/current" ]]; then
  previous_web_target=$(readlink "${web_root}/current" || true)
fi
if previous_image_ref=$(docker inspect --format '{{.Config.Image}}' timetrace-server 2>/dev/null); then
  previous_mode=docker
  previous_image_repository=${previous_image_ref%:*}
  previous_image_tag=${previous_image_ref##*:}
else
  if legacy_systemctl is-enabled --quiet "${service}"; then
    legacy_was_enabled=1
  fi
  if legacy_systemctl is-active --quiet "${service}" || ((legacy_was_enabled == 1)); then
    previous_mode=systemd
  fi
fi

log "validating Compose release ${ref}"
compose config --quiet
log "pulling ${image}:${ref} before changing the running service"
compose pull server

if [[ "${previous_mode}" == systemd ]]; then
  log "stopping the legacy systemd service for the first container cutover"
  cutover_started=1
  legacy_systemctl stop "${service}"

  backup_dir="${data_dir}/db/pre-docker-${ref}"
  if [[ ! -e "${backup_dir}" ]]; then
    backup_staging=$(mktemp -d "${data_dir}/db/.pre-docker-${ref}.XXXXXX")
    for name in timetrace.db timetrace.db-wal timetrace.db-shm; do
      if [[ -f "${data_dir}/db/${name}" ]]; then
        cp --reflink=auto --preserve=mode,timestamps \
          "${data_dir}/db/${name}" "${backup_staging}/${name}"
      fi
    done
    mv -T "${backup_staging}" "${backup_dir}"
    backup_staging=
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

if [[ ! -d "${web_release}" ]]; then
  web_staging=$(mktemp -d "${web_root}/releases/.staging-${ref}.XXXXXX")
  chmod 755 "${web_staging}"
  cp -a "${frontend_source}/." "${web_staging}/"
  mv "${web_staging}" "${web_release}"
  web_staging=
fi
chmod 755 "${web_release}"
test -s "${web_release}/index.html"
grep -F 'id="root"' "${web_release}/index.html" >/dev/null
test -n "$(find "${web_release}/assets" -type f -print -quit)"

web_next_link="${web_root}/.current-${ref}"
ln -sfn "releases/${ref}" "${web_next_link}"
web_switched=1
mv -Tf "${web_next_link}" "${web_root}/current"

curl -fsS --max-time 5 -H 'Host: timetrace.yukirin.me' http://127.0.0.1:8080/healthz >/dev/null
curl -fsS --max-time 5 -H 'Host: timetrace.yukirin.me' http://127.0.0.1:8080/ \
  | grep -F 'id="root"' >/dev/null

runtime_next_link="${runtime_root}/.current-${ref}"
ln -sfn "releases/${ref}" "${runtime_next_link}"
runtime_switched=1
mv -Tf "${runtime_next_link}" "${current_link}"

if legacy_systemctl is-enabled --quiet "${service}"; then
  legacy_systemctl disable "${service}"
fi
# The legacy uv launcher exits 143 after its graceful SIGTERM path, which
# systemd records as failed even though shutdown completed cleanly. Keep the
# retired rollback unit visible as inactive rather than leaving a false alarm.
legacy_systemctl reset-failed "${service}" || true

# Keep the stable host command synchronized only after the release has passed
# every health check. A failed release therefore never replaces the updater.
install -m 755 "${release_dir}/timetrace-update.sh" "${install_target}"

trap - ERR HUP INT TERM
log "deployed ${image}:${ref} and published its bundled SPA"
log "installed ${install_target} from ${ref}"
log "update complete"
docker inspect --format 'container={{.Name}} image={{.Config.Image}} status={{.State.Status}} health={{.State.Health.Status}}' timetrace-server
