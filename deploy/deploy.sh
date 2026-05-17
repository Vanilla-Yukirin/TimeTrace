#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Pull latest code + restart timetrace-server systemd --user unit.
#
# This script runs ON THE TARGET MACHINE (in our setup: a home Ubuntu box
# behind NAT, reached via FRP tunnel from a public cloud server). The GH
# Actions workflow (.github/workflows/deploy.yml) uploads + invokes this
# script over SSH; nothing here assumes it was launched by GH specifically,
# so you can `ssh tt-rb4g 'bash ~/TimeTrace/deploy/deploy.sh'` by hand the
# same way.
#
# Idempotent: pulls latest, syncs deps, restarts the unit, verifies healthz.
# Failing any step propagates a non-zero exit so GH Actions turns red.
#
# === FOR FORK USERS ===========================================================
# Override the variables below via env (NOT secret — these are OS metadata):
#
#     export TIMETRACE_USER=alice
#     export TIMETRACE_REPO_URL=https://github.com/alice/TimeTrace.git
#     export TIMETRACE_BRANCH=main
#     bash deploy/deploy.sh
#
# Real secrets (SSH key, GitHub token if you fetch over HTTPS+token) live in
# GitHub Secrets / your local SSH agent — never in this script.
#
# Adapt for your distro:
#  - "uv" assumed in $PATH (install: https://github.com/astral-sh/uv).
#  - "systemctl --user" assumed (Ubuntu 22+, Debian 12+, Fedora, Arch). For
#    distros without user systemd, swap step 4 with a process-supervisor
#    of your choice (s6, runit, raw nohup) and remove --user.
#  - "curl" assumed for the healthz probe; harmless if absent (we'll just
#    skip the probe with a warning).
# =============================================================================

set -euo pipefail

# --- Step 0: knobs ----------------------------------------------------------
# `:=` syntax: only set if unset / empty. So callers can override any of
# these via `export TIMETRACE_X=...` before invoking us.
: "${TIMETRACE_USER:=Yuki}"
: "${TIMETRACE_HOME:=/home/${TIMETRACE_USER}}"
: "${TIMETRACE_REPO_URL:=https://github.com/Vanilla-Yukirin/TimeTrace.git}"
: "${TIMETRACE_BRANCH:=main}"
: "${TIMETRACE_REPO_DIR:=${TIMETRACE_HOME}/TimeTrace}"
: "${TIMETRACE_DATA_DIR:=${TIMETRACE_HOME}/TimeTraceData}"
: "${TIMETRACE_SERVICE:=timetrace-server.service}"
: "${TIMETRACE_HEALTHZ_URL:=http://127.0.0.1:8765/healthz}"
: "${TIMETRACE_HEALTHZ_TIMEOUT_S:=15}"

# Make the resolved values visible in CI logs — debugging deploy failures
# starts from "what was the script actually trying to do".
echo "[deploy] resolved knobs:"
echo "         TIMETRACE_USER     = ${TIMETRACE_USER}"
echo "         TIMETRACE_REPO_URL = ${TIMETRACE_REPO_URL}"
echo "         TIMETRACE_BRANCH   = ${TIMETRACE_BRANCH}"
echo "         TIMETRACE_REPO_DIR = ${TIMETRACE_REPO_DIR}"
echo "         TIMETRACE_DATA_DIR = ${TIMETRACE_DATA_DIR}"
echo "         TIMETRACE_SERVICE  = ${TIMETRACE_SERVICE}"

# --- Step 1: ensure repo exists, fetch latest -------------------------------
# First-deploy convenience: clone if the dir isn't there yet. Subsequent
# runs do a shallow fetch + reset --hard, which is the right semantics
# for "deploy state mirror" (we don't care about local commits on the target,
# the target is a deployment, not a dev box).
if [[ ! -d "${TIMETRACE_REPO_DIR}/.git" ]]; then
    echo "[deploy] repo not found at ${TIMETRACE_REPO_DIR}, cloning fresh..."
    mkdir -p "$(dirname "${TIMETRACE_REPO_DIR}")"
    git clone "${TIMETRACE_REPO_URL}" "${TIMETRACE_REPO_DIR}"
fi

cd "${TIMETRACE_REPO_DIR}"
echo "[deploy] fetching origin (incl. tags)..."
# --tags so a `gh workflow run --ref v0.1.0` lands a fresh tag too.
# --force on tags so a tag move (re-pointing v0.1.0 to a new commit) actually
# updates locally rather than the safe-by-default reject.
git fetch --prune --tags --force origin

# Resolve TIMETRACE_BRANCH to whatever git can find: branch name → use
# origin/<name> (so the deploy mirrors the remote, not a possibly stale
# local branch). Tag name or commit SHA → use it directly. Unknown → fail
# loud rather than silently deploying the wrong revision.
if git rev-parse --verify --quiet "origin/${TIMETRACE_BRANCH}" >/dev/null; then
    TARGET_REF="origin/${TIMETRACE_BRANCH}"
elif git rev-parse --verify --quiet "${TIMETRACE_BRANCH}" >/dev/null; then
    TARGET_REF="${TIMETRACE_BRANCH}"
else
    echo "[deploy] FATAL: ref '${TIMETRACE_BRANCH}' not found as branch / tag / SHA"
    exit 1
fi
echo "[deploy] resetting to ${TARGET_REF}..."
git reset --hard "${TARGET_REF}"
HEAD_SHA="$(git rev-parse --short HEAD)"
echo "[deploy] now at ${HEAD_SHA}: $(git log -1 --pretty=%s)"

# --- Step 2: sync Python deps -----------------------------------------------
# `uv sync` reads pyproject.toml + uv.lock. P5 will introduce
# `--extra server` once the optional-deps split lands; for now the union
# `uv sync` installs everything (Windows-only deps just no-op on Linux's
# pip resolver — we intentionally keep them in the resolver-skipped path).
echo "[deploy] uv sync..."
uv sync

# --- Step 3: install / update systemd unit (only if changed) ----------------
# The unit file LIVES in the repo (deploy/timetrace-server.service) so any
# edit goes through code review. Symlink mode would be nicer but breaks if
# systemd's user unit dir has a stricter SELinux label.
UNIT_SRC="${TIMETRACE_REPO_DIR}/deploy/timetrace-server.service"
UNIT_DST_DIR="${TIMETRACE_HOME}/.config/systemd/user"
UNIT_DST="${UNIT_DST_DIR}/${TIMETRACE_SERVICE}"

if [[ ! -f "${UNIT_SRC}" ]]; then
    echo "[deploy] FATAL: ${UNIT_SRC} not found in repo. Aborting."
    exit 1
fi

mkdir -p "${UNIT_DST_DIR}"
if [[ ! -f "${UNIT_DST}" ]] || ! cmp -s "${UNIT_SRC}" "${UNIT_DST}"; then
    echo "[deploy] systemd unit changed; copying ${UNIT_SRC} → ${UNIT_DST}"
    cp "${UNIT_SRC}" "${UNIT_DST}"
    systemctl --user daemon-reload
fi

# Make sure the unit is enabled (idempotent: enable on an already-enabled
# unit is a no-op). `--now` would also start it, but we use restart below
# unconditionally so a no-op enable is enough here.
systemctl --user enable "${TIMETRACE_SERVICE}" >/dev/null

# --- Step 4: restart unit ----------------------------------------------------
echo "[deploy] restarting ${TIMETRACE_SERVICE}..."
systemctl --user restart "${TIMETRACE_SERVICE}"

# --- Step 5: healthz probe ---------------------------------------------------
# Wait up to TIMETRACE_HEALTHZ_TIMEOUT_S for the API to come up. Poll once
# per second so first-time uvicorn boot (slow on small machines) has room.
if ! command -v curl >/dev/null 2>&1; then
    echo "[deploy] WARN: curl missing; skipping healthz probe."
    echo "[deploy] OK (no probe). Deployed ${HEAD_SHA}."
    exit 0
fi

deadline=$(( $(date +%s) + TIMETRACE_HEALTHZ_TIMEOUT_S ))
while (( $(date +%s) < deadline )); do
    if curl -fsS --max-time 3 "${TIMETRACE_HEALTHZ_URL}" >/dev/null 2>&1; then
        echo "[deploy] healthz OK at ${TIMETRACE_HEALTHZ_URL}"
        echo "[deploy] OK. Deployed ${HEAD_SHA}."
        exit 0
    fi
    sleep 1
done

# Final fail: dump the unit's recent log so the GH Actions log shows what
# went wrong, then exit non-zero so the workflow turns red.
echo "[deploy] FATAL: healthz did not return 200 within ${TIMETRACE_HEALTHZ_TIMEOUT_S}s"
echo "[deploy] --- recent journal ---"
journalctl --user -u "${TIMETRACE_SERVICE}" -n 50 --no-pager || true
exit 1
