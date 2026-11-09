"""Shared local/CI acceptance entry. Run from any directory with Python 3.12+."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, env=None, output=None, timeout=1200, check=True):
    command = [str(a) for a in args]
    # Resolve npm.cmd on Windows without invoking a shell.
    command[0] = shutil.which(command[0]) or command[0]
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdout=output,
        stderr=subprocess.STDOUT if output else None,
        timeout=timeout,
    )
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)
    return result.returncode


def e2e():
    run(["docker", "info", "--format", "{{.OSType}}"], timeout=20)
    identifier = uuid.uuid4().hex[:10]
    project = "timetrace-e2e-" + identifier
    artifact_dir = ROOT / "artifacts" / project
    artifact_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        E2E_SERVER_IMAGE="timetrace-e2e-server:" + identifier,
        E2E_CLIENT_IMAGE="timetrace-e2e-client:" + identifier,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    (artifact_dir / "source.json").write_text(
        json.dumps({"commit": revision, "dirty": dirty, "project": project}), encoding="utf-8"
    )
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / "qa/e2e/empty.env"),
        "-p",
        project,
        "-f",
        str(ROOT / "qa/e2e/compose.yml"),
    ]
    result = 1
    try:
        run(
            ["docker", "build", "-f", "deploy/Dockerfile", "-t", env["E2E_SERVER_IMAGE"], "."],
            env=env,
        )
        run(
            [
                "docker",
                "build",
                "-f",
                "qa/e2e/Dockerfile",
                "--build-arg",
                "SERVER_IMAGE=" + env["E2E_SERVER_IMAGE"],
                "-t",
                env["E2E_CLIENT_IMAGE"],
                ".",
            ],
            env=env,
        )
        run([*compose, "up", "-d"], env=env)
        waiter = subprocess.Popen([*compose, "wait", "client"], cwd=ROOT, env=env)
        deadline = time.monotonic() + 240
        restarted = False
        try:
            while waiter.poll() is None:
                if time.monotonic() > deadline:
                    raise RuntimeError("E2E exceeded 240 seconds")
                if not restarted:
                    requested = (
                        subprocess.run(
                            [
                                *compose,
                                "exec",
                                "-T",
                                "client",
                                "test",
                                "-f",
                                "/artifacts/restart-server",
                            ],
                            cwd=ROOT,
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=10,
                        ).returncode
                        == 0
                    )
                    if requested:
                        run([*compose, "restart", "server"], env=env, timeout=60)
                        run(
                            [
                                *compose,
                                "exec",
                                "-T",
                                "client",
                                "touch",
                                "/artifacts/server-restarted",
                            ],
                            env=env,
                        )
                        restarted = True
                time.sleep(1)
            result = waiter.returncode
        finally:
            if waiter.poll() is None:
                waiter.terminate()
                waiter.wait(timeout=10)
    finally:
        with (artifact_dir / "compose.log").open("w", encoding="utf-8") as output:
            run([*compose, "logs", "--no-color"], env=env, output=output, check=False, timeout=30)
        run(
            [*compose, "cp", "client:/artifacts/.", str(artifact_dir)],
            env=env,
            check=False,
            timeout=30,
        )
        # This unique project owns disposable named volumes only. No host data mounts.
        run([*compose, "down", "--volumes", "--remove-orphans"], env=env, timeout=70)
        print("E2E artifacts: " + str(artifact_dir), flush=True)
    report = artifact_dir / "result.json"
    if result or not report.exists() or not json.loads(report.read_text())["passed"]:
        raise RuntimeError("E2E failed; inspect " + str(artifact_dir))
    logs = (artifact_dir / "compose.log").read_text(encoding="utf-8")
    if '"request_id": "e2e-observation"' not in logs:
        raise RuntimeError("Request ID is absent from server logs: " + str(artifact_dir))
    client_logs = (artifact_dir / "client-process.log").read_text(encoding="utf-8")
    if "E2E frame" in client_logs or "e2e-only-machine-token" in logs + client_logs:
        raise RuntimeError("Private fixture input leaked into logs: " + str(artifact_dir))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=["lint", "unit", "frontend", "e2e", "all"])
    selected = parser.parse_args().check
    started = dt.datetime.now(dt.timezone.utc)
    if selected in ("lint", "unit", "all"):
        run(["uv", "sync", "--locked"])
    if selected in ("lint", "all"):
        run(["uv", "run", "--locked", "ruff", "check", "src/", "qa/", "scripts/check.py"])
        run(
            [
                "uv",
                "run",
                "--locked",
                "ruff",
                "format",
                "--check",
                "src/",
                "qa/",
                "scripts/check.py",
            ]
        )
    if selected in ("unit", "all"):
        (ROOT / "artifacts").mkdir(exist_ok=True)
        env = os.environ.copy()
        env["PYTHON_DOTENV_DISABLED"] = "1"
        scratch = ROOT / "artifacts" / ("pytest-" + uuid.uuid4().hex)
        run(
            [
                "uv",
                "run",
                "--locked",
                "pytest",
                "--tb=short",
                "--junitxml=artifacts/unit.xml",
                "--basetemp=" + str(scratch),
            ],
            env=env,
        )
    if selected in ("frontend", "all"):
        run(["npm", "ci", "--prefix", "frontend"])
        run(["npm", "run", "lint", "--prefix", "frontend"])
        run(["npm", "run", "build", "--prefix", "frontend"])
    if selected in ("e2e", "all"):
        e2e()
    print(f"PASS {selected}; elapsed={dt.datetime.now(dt.timezone.utc) - started}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.SubprocessError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
