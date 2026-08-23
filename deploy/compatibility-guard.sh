#!/usr/bin/env bash
# Shared image/database compatibility checks for timetrace-update and the
# release activation helper. This file defines functions only.

TIMETRACE_DEVICE_DB_COMPAT_LABEL_KEY=io.yukirin.timetrace.database.device-identity
TIMETRACE_DEVICE_DB_COMPAT_LABEL_VALUE=1
TIMETRACE_DEVICE_DB_MINIMUM_MARKER_VALUE="${TIMETRACE_DEVICE_DB_COMPAT_LABEL_KEY}=${TIMETRACE_DEVICE_DB_COMPAT_LABEL_VALUE}"

timetrace_normalize_device_db_compat() {
  if [[ "${1:-}" == "${TIMETRACE_DEVICE_DB_COMPAT_LABEL_VALUE}" ]]; then
    printf '%s\n' compatible
  else
    printf '%s\n' incompatible
  fi
}

timetrace_image_device_db_compat() {
  local image_ref=$1 raw
  if ! raw=$(docker image inspect \
      --format "{{with index .Config.Labels \"${TIMETRACE_DEVICE_DB_COMPAT_LABEL_KEY}\"}}{{.}}{{end}}" \
      "${image_ref}"); then
    printf 'could not inspect database compatibility label on image %s\n' \
      "${image_ref}" >&2
    return 2
  fi
  timetrace_normalize_device_db_compat "${raw}"
}

timetrace_container_device_db_compat() {
  local container=$1 raw
  if ! raw=$(docker inspect \
      --format "{{with index .Config.Labels \"${TIMETRACE_DEVICE_DB_COMPAT_LABEL_KEY}\"}}{{.}}{{end}}" \
      "${container}"); then
    printf 'could not inspect database compatibility label on container %s\n' \
      "${container}" >&2
    return 2
  fi
  timetrace_normalize_device_db_compat "${raw}"
}

timetrace_device_database_state() {
  local inspector_image=$1 data_dir=$2 host_uid=$3 host_gid=$4
  local database_path="${data_dir}/db/timetrace.db" state

  if [[ ! -e "${database_path}" ]]; then
    printf '%s\n' missing
    return 0
  fi
  if [[ ! -f "${database_path}" ]]; then
    printf 'database path is not a regular file: %s\n' "${database_path}" >&2
    return 2
  fi

  local inspect_script
  inspect_script=$(cat <<'PY'
import sqlite3
import sys

path = sys.argv[1]
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
    records = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'"
    ).fetchone()
    if records is None:
        print("legacy")
    else:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
        if "device_id" not in columns:
            print("legacy")
        elif conn.execute(
            "SELECT 1 FROM records WHERE device_id IS NOT NULL LIMIT 1"
        ).fetchone():
            print("owned")
        else:
            print("empty")
PY
)

  if ! state=$(docker run --rm --pull never \
      --network none \
      --read-only \
      --user "${host_uid}:${host_gid}" \
      --mount "type=bind,src=${data_dir},dst=/timetrace-data" \
      --entrypoint python \
      "${inspector_image}" \
      -I -S -c "${inspect_script}" /timetrace-data/db/timetrace.db); then
    printf 'could not inspect device ownership in %s\n' "${database_path}" >&2
    return 2
  fi
  case "${state}" in
    legacy | empty | owned) printf '%s\n' "${state}" ;;
    *)
      printf 'database compatibility inspection returned an invalid state: %s\n' \
        "${state}" >&2
      return 2
      ;;
  esac
}

timetrace_minimum_device_db_compat() {
  local marker=$1
  local -a lines=()
  if [[ ! -e "${marker}" ]]; then
    printf '%s\n' none
    return 0
  fi
  if [[ ! -f "${marker}" || ! -r "${marker}" ]]; then
    printf 'minimum database compatibility marker is not a readable file: %s\n' \
      "${marker}" >&2
    return 2
  fi
  mapfile -t lines <"${marker}"
  if ((${#lines[@]} != 1)) || \
      [[ "${lines[0]}" != "${TIMETRACE_DEVICE_DB_MINIMUM_MARKER_VALUE}" ]]; then
    printf 'minimum database compatibility marker is invalid: %s\n' "${marker}" >&2
    return 2
  fi
  printf '%s\n' compatible
}

timetrace_assert_compat_state_for_database() {
  local compat_state=$1 inspector_image=$2 data_dir=$3 host_uid=$4 host_gid=$5
  local context=${6:-target runtime} minimum_marker=${7:-} minimum_state=none database_state

  if [[ -n "${minimum_marker}" ]]; then
    if ! minimum_state=$(timetrace_minimum_device_db_compat "${minimum_marker}"); then
      printf 'refusing %s because the minimum database compatibility marker could not be verified\n' \
        "${context}" >&2
      return 1
    fi
  fi
  if [[ "${minimum_state}" == compatible ]]; then
    if [[ "${compat_state}" != compatible ]]; then
      printf 'refusing %s: committed minimum database compatibility requires %s\n' \
        "${context}" "${TIMETRACE_DEVICE_DB_MINIMUM_MARKER_VALUE}" >&2
      return 1
    fi
    printf 'database compatibility: minimum=%s runtime=%s context=%s\n' \
      "${minimum_state}" "${compat_state}" "${context}"
    return 0
  fi

  if ! database_state=$(timetrace_device_database_state \
      "${inspector_image}" "${data_dir}" "${host_uid}" "${host_gid}"); then
    printf 'refusing %s because the production database state could not be verified\n' \
      "${context}" >&2
    return 1
  fi
  if [[ "${database_state}" == owned && "${compat_state}" != compatible ]]; then
    printf 'refusing %s: records.device_id contains owned rows but the runtime does not declare %s=%s\n' \
      "${context}" \
      "${TIMETRACE_DEVICE_DB_COMPAT_LABEL_KEY}" \
      "${TIMETRACE_DEVICE_DB_COMPAT_LABEL_VALUE}" >&2
    return 1
  fi
  printf 'database compatibility: state=%s runtime=%s context=%s\n' \
    "${database_state}" "${compat_state}" "${context}"
}

timetrace_assert_image_for_database() {
  local target_image=$1 data_dir=$2 host_uid=$3 host_gid=$4
  local context=${5:-target image} minimum_marker=${6:-} compat_state

  if ! compat_state=$(timetrace_image_device_db_compat "${target_image}"); then
    printf 'refusing %s because its image compatibility label could not be inspected\n' \
      "${context}" >&2
    return 1
  fi
  timetrace_assert_compat_state_for_database \
    "${compat_state}" "${target_image}" "${data_dir}" "${host_uid}" "${host_gid}" \
    "${context}" "${minimum_marker}"
}

timetrace_publish_guarded_updater() {
  local guard_source=$1 guard_target=$2 updater_source=$3 updater_target=$4

  # Publish the dependency first and make the wrapper's atomic replace the
  # activation point. Callers must complete this before stopping the current
  # runtime or starting a candidate that could write a newer database shape.
  # Staging variables intentionally remain global so the deploy rollback
  # handler can remove a file left behind by a failed command.
  compatibility_staging=$(mktemp "${guard_target%/*}/.timetrace-compatibility.XXXXXX")
  install -m 644 "${guard_source}" "${compatibility_staging}"
  mv -Tf "${compatibility_staging}" "${guard_target}"
  compatibility_staging=

  updater_staging=$(mktemp "${updater_target%/*}/.timetrace-update.XXXXXX")
  install -m 755 "${updater_source}" "${updater_staging}"
  mv -Tf "${updater_staging}" "${updater_target}"
  updater_staging=
}

timetrace_commit_minimum_device_db_compat() {
  local minimum_marker=$1 target_compat=$2 runtime_root=$3
  if [[ "${target_compat}" == compatible ]]; then
    minimum_compatibility_staging=$(mktemp \
      "${runtime_root}/.minimum-database-compatibility.XXXXXX")
    printf '%s\n' "${TIMETRACE_DEVICE_DB_MINIMUM_MARKER_VALUE}" \
      >"${minimum_compatibility_staging}"
    chmod 600 "${minimum_compatibility_staging}"
    mv -Tf "${minimum_compatibility_staging}" "${minimum_marker}"
    minimum_compatibility_staging=
  fi
}
