#!/usr/bin/env bash

set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck source=deploy/compatibility-guard.sh
source "${repo_root}/deploy/compatibility-guard.sh"

test_root=$(mktemp -d)
wal_writer_pid=
cleanup() {
  if [[ -n "${wal_writer_pid}" ]]; then
    kill "${wal_writer_pid}" 2>/dev/null || true
    wait "${wal_writer_pid}" 2>/dev/null || true
  fi
  rm -rf -- "${test_root}"
}
trap cleanup EXIT
data_dir="${test_root}/data"
mkdir -p "${data_dir}/db"
database_path="${data_dir}/db/timetrace.db"
minimum_marker="${test_root}/minimum-database-compatibility"
fake_label=1
fake_inspect_failure=0
fake_database_failure=0

docker() {
  if [[ "$1 $2" == "image inspect" ]]; then
    ((fake_inspect_failure == 0)) || return 42
    printf '%s\n' "${fake_label}"
    return 0
  fi
  if [[ "$1" == inspect ]]; then
    ((fake_inspect_failure == 0)) || return 42
    printf '%s\n' "${fake_label}"
    return 0
  fi
  if [[ "$1" == run ]]; then
    ((fake_database_failure == 0)) || return 43
    local code= arg
    while (($#)); do
      arg=$1
      shift
      if [[ "${arg}" == -c ]]; then
        code=$1
        break
      fi
    done
    [[ -n "${code}" ]]
    python3 -c "${code}" "${database_path}"
    return
  fi
  printf 'unexpected fake docker invocation: %s\n' "$*" >&2
  return 99
}

reset_database() {
  rm -f -- "${database_path}" "${database_path}-wal" "${database_path}-shm"
}

create_database() {
  local schema=$1
  python3 - "${database_path}" "${schema}" <<'PY'
import sqlite3
import sys

path, schema = sys.argv[1:]
with sqlite3.connect(path) as conn:
    if schema == "legacy":
        conn.execute("CREATE TABLE records (id TEXT PRIMARY KEY)")
    else:
        conn.execute("CREATE TABLE records (id TEXT PRIMARY KEY, device_id TEXT)")
        if schema == "owned":
            conn.execute("INSERT INTO records VALUES ('r1', '57b81d95-8c0d-41d8-8e56-ef116904661c')")
PY
}

expect_allowed() {
  "$@" >/dev/null
}

expect_refused() {
  if "$@" >/dev/null 2>&1; then
    printf 'expected compatibility guard refusal: %s\n' "$*" >&2
    exit 1
  fi
}

# Missing DB and old schema contain no device ownership, so an old image is a
# valid first-release rollback target.
reset_database
fake_label=
expect_allowed timetrace_assert_image_for_database old:image "${data_dir}" 1000 1000 missing-db
create_database legacy
expect_allowed timetrace_assert_image_for_database old:image "${data_dir}" 1000 1000 legacy-db

# A migrated-but-empty schema also permits automatic rollback to the old image.
reset_database
create_database empty
expect_allowed timetrace_assert_compat_state_for_database \
  incompatible inspector:image "${data_dir}" 1000 1000 automatic-rollback

# Once any row has an owner, only a declaring image/SHA may activate or roll back.
reset_database
create_database owned
fake_label=
expect_refused timetrace_assert_image_for_database old:image "${data_dir}" 1000 1000 downgrade
fake_label=1
expect_allowed timetrace_assert_image_for_database compatible:image "${data_dir}" 1000 1000 upgrade
expect_allowed timetrace_assert_compat_state_for_database \
  compatible inspector:image "${data_dir}" 1000 1000 compatible-rollback
expect_refused timetrace_assert_compat_state_for_database \
  incompatible inspector:image "${data_dir}" 1000 1000 old-automatic-rollback

# A successful device-aware release commits a durable floor. It closes the
# live-DB TOCTOU even if the current table happens to be empty at downgrade.
reset_database
create_database empty
printf '%s\n' "${TIMETRACE_DEVICE_DB_MINIMUM_MARKER_VALUE}" >"${minimum_marker}"
fake_label=
expect_refused timetrace_assert_image_for_database \
  old:image "${data_dir}" 1000 1000 marker-downgrade "${minimum_marker}"
fake_label=1
expect_allowed timetrace_assert_image_for_database \
  compatible:image "${data_dir}" 1000 1000 marker-compatible "${minimum_marker}"
printf '%s\n' corrupted >"${minimum_marker}"
expect_refused timetrace_assert_image_for_database \
  compatible:image "${data_dir}" 1000 1000 corrupt-marker "${minimum_marker}"
rm -f -- "${minimum_marker}"

# Keep a writer open so the owned row exists only in a live WAL. The guard's
# exact mode=ro SQLite program must still observe it before rollback.
reset_database
wal_ready="${test_root}/wal-ready"
python3 - "${database_path}" "${wal_ready}" <<'PY' &
import pathlib
import sqlite3
import sys
import time

database, ready = sys.argv[1:]
with sqlite3.connect(database) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE records (id TEXT PRIMARY KEY, device_id TEXT)")
    conn.execute("INSERT INTO records VALUES ('wal-row', '57b81d95-8c0d-41d8-8e56-ef116904661c')")
    conn.commit()
    pathlib.Path(ready).touch()
    time.sleep(30)
PY
wal_writer_pid=$!
for attempt in $(seq 1 50); do
  [[ -f "${wal_ready}" ]] && break
  sleep 0.1
done
[[ -f "${wal_ready}" ]]
fake_label=
fake_database_failure=0
expect_refused timetrace_assert_image_for_database \
  old:image "${data_dir}" 1000 1000 live-wal-downgrade
kill "${wal_writer_pid}"
wait "${wal_writer_pid}" || true
wal_writer_pid=

# Docker label inspection and SQLite inspection failures both fail closed.
fake_inspect_failure=1
expect_refused timetrace_assert_image_for_database compatible:image "${data_dir}" 1000 1000 inspect-failure
fake_inspect_failure=0
fake_database_failure=1
expect_refused timetrace_assert_image_for_database compatible:image "${data_dir}" 1000 1000 db-failure

# Keep both enforcement points wired ahead of mutations. The installed wrapper
# must guard before it extracts/executes an old target helper, and automatic
# rollback must guard before Compose removes the failed candidate.
line_of() {
  local pattern=$1 file=$2
  grep -n -m1 -F "${pattern}" "${file}" | cut -d: -f1
}

wrapper_guard_line=$(line_of 'timetrace_assert_image_for_database \' "${repo_root}/timetrace-update.sh")
wrapper_extract_line=$(line_of 'install -d -m 700 "${releases_dir}"' "${repo_root}/timetrace-update.sh")
((wrapper_guard_line < wrapper_extract_line))

rollback_guard_line=$(line_of 'timetrace_assert_compat_state_for_database \' "${repo_root}/deploy/deploy-container.sh")
rollback_stop_line=$(line_of 'compose stop server' "${repo_root}/deploy/deploy-container.sh")
rollback_down_line=$(line_of 'compose down --remove-orphans' "${repo_root}/deploy/deploy-container.sh")
((rollback_stop_line < rollback_guard_line))
((rollback_guard_line < rollback_down_line))

# The guard-aware updater is a pre-cutover safety foundation; the marker is a
# separate post-health commit. Verify the production ordering first.
deploy_script="${repo_root}/deploy/deploy-container.sh"
guard_script="${repo_root}/deploy/compatibility-guard.sh"
guard_publish_line=$(line_of 'mv -Tf "${compatibility_staging}" "${guard_target}"' "${guard_script}")
updater_publish_line=$(line_of 'mv -Tf "${updater_staging}" "${updater_target}"' "${guard_script}")
marker_publish_line=$(line_of 'mv -Tf "${minimum_compatibility_staging}" "${minimum_marker}"' "${guard_script}")
updater_call_line=$(line_of 'timetrace_publish_guarded_updater \' "${deploy_script}")
first_cutover_line=$(line_of 'cutover_started=1' "${deploy_script}")
candidate_start_line=$(line_of 'compose up -d --remove-orphans --pull never' "${deploy_script}")
marker_call_line=$(line_of 'timetrace_commit_minimum_device_db_compat \' "${deploy_script}")
commit_trap_line=$(line_of 'trap - ERR HUP INT TERM' "${deploy_script}")
((guard_publish_line < updater_publish_line))
((updater_publish_line < marker_publish_line))
((updater_call_line < first_cutover_line))
((updater_call_line < candidate_start_line))
((candidate_start_line < marker_call_line))
((marker_call_line < commit_trap_line))

test_updater_publication_failure() {
  local fail_at=$1
  local transaction_root="${test_root}/updater-transaction-${fail_at}"
  mkdir -p "${transaction_root}/bin" "${transaction_root}/runtime"
  printf 'old-guard\n' >"${transaction_root}/bin/guard"
  printf 'old-updater\n' >"${transaction_root}/bin/updater"
  printf 'new-guard\n' >"${transaction_root}/guard-source"
  printf 'new-updater\n' >"${transaction_root}/updater-source"

  set +e
  FAIL_AT="${fail_at}" bash -Eeuo pipefail -c '
    source "$1"
    move_count=0
    mv() {
      move_count=$((move_count + 1))
      if ((move_count == FAIL_AT)); then
        return 91
      fi
      command mv "$@"
    }
    timetrace_publish_guarded_updater \
      "$2/guard-source" "$2/bin/guard" \
      "$2/updater-source" "$2/bin/updater"
  ' _ "${guard_script}" "${transaction_root}"
  status=$?
  set -e

  if ((fail_at == 0)); then
    ((status == 0))
    grep -Fx 'new-guard' "${transaction_root}/bin/guard" >/dev/null
    grep -Fx 'new-updater' "${transaction_root}/bin/updater" >/dev/null
  else
    ((status != 0))
    if ((fail_at == 1)); then
      grep -Fx 'old-guard' "${transaction_root}/bin/guard" >/dev/null
      grep -Fx 'old-updater' "${transaction_root}/bin/updater" >/dev/null
    else
      grep -Fx 'new-guard' "${transaction_root}/bin/guard" >/dev/null
      grep -Fx 'old-updater' "${transaction_root}/bin/updater" >/dev/null
    fi
  fi
  [[ ! -e "${transaction_root}/runtime/minimum-database-compatibility" ]]
}

test_updater_publication_failure 1  # guard replace fails before cutover
test_updater_publication_failure 2  # wrapper replace fails before cutover
test_updater_publication_failure 0  # safe wrapper foundation succeeds

# If the final marker commit fails after health checks, both pieces of the new
# updater must already be active. This is the state that keeps an owned failed
# candidate from being followed by an unguarded old-SHA activation.
marker_root="${test_root}/marker-failure"
mkdir -p "${marker_root}/bin" "${marker_root}/runtime"
printf 'new-guard\n' >"${marker_root}/guard-source"
printf 'new-updater\n' >"${marker_root}/updater-source"
timetrace_publish_guarded_updater \
  "${marker_root}/guard-source" "${marker_root}/bin/guard" \
  "${marker_root}/updater-source" "${marker_root}/bin/updater"
set +e
bash -Eeuo pipefail -c '
  source "$1"
  mv() { return 91; }
  timetrace_commit_minimum_device_db_compat \
    "$2/runtime/minimum-database-compatibility" compatible "$2/runtime"
' _ "${guard_script}" "${marker_root}"
marker_status=$?
set -e
((marker_status != 0))
grep -Fx 'new-guard' "${marker_root}/bin/guard" >/dev/null
grep -Fx 'new-updater' "${marker_root}/bin/updater" >/dev/null
[[ ! -e "${marker_root}/runtime/minimum-database-compatibility" ]]

# With owned data and no marker, that installed wrapper's database guard still
# rejects an old image. This models the failed-candidate/manual-old-SHA path.
reset_database
create_database owned
fake_label=
fake_database_failure=0
expect_refused timetrace_assert_image_for_database \
  old:image "${data_dir}" 1000 1000 post-failure-old-sha \
  "${marker_root}/runtime/minimum-database-compatibility"

if [[ -n "${TIMETRACE_TEST_IMAGE:-}" ]]; then
  # Re-run the live-WAL case through a real built container. This validates the
  # OCI label template and the writable ancillary-file mount, not just the
  # fake-docker decision layer above.
  unset -f docker
  reset_database
  wal_ready="${test_root}/real-wal-ready"
  python3 - "${database_path}" "${wal_ready}" <<'PY' &
import pathlib
import sqlite3
import sys
import time

database, ready = sys.argv[1:]
with sqlite3.connect(database) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE records (id TEXT PRIMARY KEY, device_id TEXT)")
    conn.execute("INSERT INTO records VALUES ('real-wal', '57b81d95-8c0d-41d8-8e56-ef116904661c')")
    conn.commit()
    pathlib.Path(ready).touch()
    time.sleep(30)
PY
  wal_writer_pid=$!
  for attempt in $(seq 1 50); do
    [[ -f "${wal_ready}" ]] && break
    sleep 0.1
  done
  [[ -f "${wal_ready}" ]]
  expect_allowed timetrace_assert_image_for_database \
    "${TIMETRACE_TEST_IMAGE}" "${data_dir}" "$(id -u)" "$(id -g)" real-live-wal
  kill "${wal_writer_pid}"
  wait "${wal_writer_pid}" || true
  wal_writer_pid=
fi

printf 'deployment compatibility guard tests passed\n'
