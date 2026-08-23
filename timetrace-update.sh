#!/usr/bin/env bash
# Pull and activate the latest immutable TimeTrace release from GHCR.
#
# Install once on the deployment host:
#   install -d "$HOME/.local/bin"
#   install -m 755 timetrace-update.sh "$HOME/.local/bin/timetrace-update"
#
# Update to the current deploy branch (default):
#   timetrace-update
#
# Pin or roll back to a known commit:
#   timetrace-update <40-character-git-sha>

set -Eeuo pipefail

image=${TIMETRACE_IMAGE:-ghcr.io/vanilla-yukirin/timetrace-server}
repository_url=${TIMETRACE_REPOSITORY_URL:-https://github.com/Vanilla-Yukirin/TimeTrace.git}
requested_ref=${1:-deploy}
runtime_root=${TIMETRACE_RUNTIME_ROOT:-/srv/timetrace/runtime}
releases_dir="${runtime_root}/releases"
host_user=${TIMETRACE_HOST_USER:-$(id -un)}
host_home=${TIMETRACE_HOST_HOME:-$(getent passwd "${host_user}" | cut -d: -f6)}
pull_attempts=${TIMETRACE_PULL_ATTEMPTS:-18}
pull_interval=${TIMETRACE_PULL_INTERVAL:-10}
install_dir=${TIMETRACE_INSTALL_DIR:-${HOME}/.local/bin}
install_target="${install_dir}/timetrace-update"

log() {
  printf '==> %s\n' "$*"
}

for command in docker git getent; do
  command -v "${command}" >/dev/null || {
    echo "required command not found: ${command}" >&2
    exit 1
  }
done

[[ "${host_home}" == /* ]] || {
  echo "could not resolve an absolute home directory for ${host_user}" >&2
  exit 1
}

if [[ "${requested_ref}" =~ ^[0-9a-f]{40}$ ]]; then
  ref=${requested_ref}
else
  [[ "${requested_ref}" =~ ^[A-Za-z0-9._/-]+$ ]] || {
    echo "branch name contains unsupported characters: ${requested_ref}" >&2
    exit 2
  }
  log "resolving ${requested_ref} from ${repository_url}"
  remote_line=$(git ls-remote --exit-code "${repository_url}" "refs/heads/${requested_ref}")
  ref=${remote_line%%$'\t'*}
fi

[[ "${ref}" =~ ^[0-9a-f]{40}$ ]] || {
  echo "resolved ref is not a full Git SHA: ${ref}" >&2
  exit 2
}
[[ "${image}" == ghcr.io/* ]] || {
  echo "TIMETRACE_IMAGE must refer to ghcr.io" >&2
  exit 2
}
[[ "${pull_attempts}" =~ ^[1-9][0-9]*$ ]] || {
  echo "TIMETRACE_PULL_ATTEMPTS must be a positive integer" >&2
  exit 2
}
[[ "${pull_interval}" =~ ^[0-9]+$ ]] || {
  echo "TIMETRACE_PULL_INTERVAL must be a non-negative integer" >&2
  exit 2
}

image_ref="${image}:${ref}"
pulled=0
for ((attempt = 1; attempt <= pull_attempts; attempt++)); do
  log "pulling ${image_ref} (${attempt}/${pull_attempts})"
  if pull_output=$(docker pull "${image_ref}" 2>&1); then
    printf '%s\n' "${pull_output}"
    pulled=1
    break
  fi
  printf '%s\n' "${pull_output}" >&2
  if grep -Eqi 'unauthorized|denied|authentication required' <<<"${pull_output}"; then
    cat >&2 <<'EOF'
GHCR rejected the pull. Log in once with a read-only package token:
  printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
EOF
    exit 1
  fi
  if ((attempt < pull_attempts)); then
    sleep "${pull_interval}"
  fi
done
((pulled == 1)) || {
  echo "image did not become available: ${image_ref}" >&2
  exit 1
}

install -d -m 700 "${releases_dir}"
release_dir="${releases_dir}/${ref}"
staging_dir=
extract_container=

cleanup() {
  if [[ -n "${extract_container}" ]]; then
    docker rm -f "${extract_container}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${staging_dir}" && -d "${staging_dir}" ]]; then
    case "${staging_dir}" in
      "${releases_dir}"/.staging-*) rm -rf -- "${staging_dir}" ;;
    esac
  fi
}
trap cleanup EXIT

if [[ ! -d "${release_dir}" ]]; then
  staging_dir=$(mktemp -d "${releases_dir}/.staging-${ref}.XXXXXX")
  extract_container="timetrace-extract-${ref:0:12}-$$"
  log "extracting the Compose definition, deploy helper, and SPA"
  docker create --name "${extract_container}" "${image_ref}" >/dev/null
  docker cp "${extract_container}:/opt/timetrace/release/." "${staging_dir}/"
  docker rm "${extract_container}" >/dev/null
  extract_container=

  test -s "${staging_dir}/docker-compose.yml"
  test -s "${staging_dir}/deploy-container.sh"
  test -s "${staging_dir}/timetrace-update.sh"
  test -s "${staging_dir}/frontend-dist/index.html"
  mv "${staging_dir}" "${release_dir}"
  staging_dir=
fi

test -s "${release_dir}/docker-compose.yml"
test -s "${release_dir}/deploy-container.sh"
test -s "${release_dir}/timetrace-update.sh"
test -s "${release_dir}/frontend-dist/index.html"

log "activating ${ref}"
TIMETRACE_HOST_USER="${host_user}" \
TIMETRACE_HOST_HOME="${host_home}" \
TIMETRACE_RUNTIME_ROOT="${runtime_root}" \
  bash "${release_dir}/deploy-container.sh" "${ref}" "${image}"

# Keep the stable host command synchronized with the successfully activated
# release. A failed release never replaces the updater.
install -d -m 755 "${install_dir}"
install -m 755 "${release_dir}/timetrace-update.sh" "${install_target}"
log "installed ${install_target} from ${ref}"
log "update complete"
