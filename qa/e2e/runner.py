"""Two-container acceptance: real TCP/auth/client/server/worker/MCP/SPA.

Fixtures replace screen pixels/model responses and inject an OS replace failure.
All application persistence, queues, HTTP payloads and analysis loops are real.
"""

import asyncio
import datetime as dt
import json
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from playwright.async_api import async_playwright, expect

ARTIFACTS = Path("/artifacts")
TOKEN = "e2e-only-machine-token"
DEVICE = "b2ce6dbd-e18c-40e4-8705-138ef26bf343"
BASE = "http://127.0.0.1:18080"
app = FastAPI()
state = {
    "offline": True,
    "model_failures": 1,
    "model_calls": 0,
    "embedding_calls": 0,
    "lost_ack": True,
    "accepted_before_ack_loss": False,
    "ingest_requests": 0,
}
proxy = None


@app.post("/v1/chat/completions")
async def model(request: Request):
    data = await request.json()
    state["model_calls"] += 1
    if state["model_failures"]:
        state["model_failures"] -= 1
        return JSONResponse({"error": {"message": "fixture transient outage"}}, status_code=503)
    messages = json.dumps(data["messages"], ensure_ascii=False)
    if "1+1" in messages:
        content = "2"
    else:
        visual = any(isinstance(m.get("content"), list) for m in data["messages"])
        content = json.dumps(
            {
                "keywords": ["E2E"],
                "summary": "E2E editor fixture",
                "description": "E2E synthetic editor activity",
                "category": "work",
            }
            if visual
            else {
                "description": "E2E synthetic editor summary",
                "key_points": ["Fixture verified"],
                "evaluation": "Synthetic test input",
            },
            ensure_ascii=False,
        )
    return {
        "id": "fixture-response",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    state["embedding_calls"] += 1
    data = await request.json()
    return {
        "object": "list",
        "model": data["model"],
        "data": [
            {
                "object": "embedding",
                "index": 0,
                "embedding": [1.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
            }
        ],
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def gateway(path: str, request: Request):
    if path.startswith(("v1/", "mcp", "healthz", "readyz", "metrics", "thumbs/", "blob/")):
        if state["offline"] and path.startswith(("v1/ingest", "healthz")):
            return JSONResponse({"detail": "fixture network unavailable"}, status_code=503)
        if path.startswith("v1/ingest"):
            state["ingest_requests"] += 1
        headers = {k: v for k, v in request.headers.items() if k not in ("host", "content-length")}
        headers["host"] = "127.0.0.1:8765"
        response = await proxy.request(
            request.method,
            "http://server:8765/" + path,
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
        # Server committed, but acknowledgement was lost: the real sender must replay.
        if path == "v1/ingest/record" and response.is_success and state["lost_ack"]:
            state["lost_ack"] = False
            state["accepted_before_ack_loss"] = True
            return JSONResponse({"detail": "fixture lost acknowledgement"}, status_code=503)
        return Response(
            response.content,
            status_code=response.status_code,
            headers={
                k: v
                for k, v in response.headers.items()
                if k not in ("content-length", "content-encoding", "transfer-encoding")
            },
        )
    root = Path("/opt/timetrace/release/frontend-dist")
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return Response(status_code=404)
    return FileResponse(target if target.is_file() else root / "index.html")


async def until(label, probe, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = await probe()
            if value:
                return value
        except (httpx.HTTPError, KeyError) as exc:
            last = type(exc).__name__
        await asyncio.sleep(0.25)
    raise AssertionError(f"Timed out: {label}; last_error={last}")


async def run(completed):
    global proxy
    ARTIFACTS.mkdir(exist_ok=True)
    process = None
    client_log = (ARTIFACTS / "client-process.log").open("w")
    async with httpx.AsyncClient(timeout=15) as proxy_client:
        proxy = proxy_client
        server = uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=18080, log_level="warning")
        )
        gateway_task = asyncio.create_task(server.serve())
        try:

            async def ready():
                return (await proxy.get("http://server:8765/readyz")).status_code == 200

            await until("server readiness", ready)
            async with httpx.AsyncClient(
                base_url=BASE, headers={"Authorization": "Bearer " + TOKEN}
            ) as api:
                assert (await proxy.get("http://server:8765/v1/records")).status_code == 401
                assert (await proxy.get("http://server:8765/metrics")).status_code == 401
                completed.append("authentication")

                def start(capture, *, deny_ack=False):
                    return subprocess.Popen(
                        [
                            sys.executable,
                            "/qa/client.py",
                            *(["--capture"] if capture else []),
                            *(["--deny-ack"] if deny_ack else []),
                        ],
                        stdout=client_log,
                        stderr=subprocess.STDOUT,
                    )

                process = start(True)

                async def status():
                    response = await proxy.get("http://127.0.0.1:18764/api/status")
                    response.raise_for_status()
                    return response.json()

                await until("client control", status)
                await asyncio.sleep(8)
                before = await status()
                assert before["outbox_pending"] >= 6, before
                # Kill the actual client process while disconnected, then reuse its volume.
                process.kill()
                await asyncio.to_thread(process.wait, 10)
                process = start(False, deny_ack=True)

                async def recovered():
                    value = await status()
                    return value if value["outbox_pending"] >= before["outbox_pending"] else None

                recovered_status = await until("durable outbox after process kill", recovered)
                state["offline"] = False

                async def ack_blocked():
                    value = await status()
                    return (
                        value if value.get("last_error") == "outbox_ack: PermissionError" else None
                    )

                blocked_status = await until("ack failure visible in live client", ack_blocked)
                assert (ARTIFACTS / "ack-blocked").exists()
                assert process.poll() is None
                (ARTIFACTS / "ack-blocked-status.json").write_text(json.dumps(blocked_status))
                assert blocked_status["outbox_pending"] == recovered_status["outbox_pending"], (
                    blocked_status,
                    recovered_status,
                )
                requests_before = state["ingest_requests"]
                await asyncio.sleep(1.2)
                assert process.poll() is None
                assert (await status())["outbox_pending"] == recovered_status["outbox_pending"]
                assert state["ingest_requests"] == requests_before
                (ARTIFACTS / "release-ack").touch()

                async def drained():
                    value = await status()
                    return (
                        value if value["outbox_pending"] == 0 and value["last_upload_at"] else None
                    )

                await until("retry and drain", drained)
                assert state["accepted_before_ack_loss"]
                completed.append("ack_permission_error_recovery")
                completed.extend(
                    ["offline_capture", "client_crash_recovery", "lost_ack_idempotency"]
                )

                async def analyzed():
                    response = await api.get("/v1/records", params={"device_id": DEVICE})
                    response.raise_for_status()
                    rows = response.json()["items"]
                    return (
                        rows
                        if len(rows) == 3
                        and all(r.get("vlm_desc") and r.get("ts_end") for r in rows)
                        else None
                    )

                rows = await until("three analyzed and closed records", analyzed)
                assert state["embedding_calls"] >= 3
                assert len({r["id"] for r in rows}) == 3
                for row in rows:
                    detail = (await api.get("/v1/records/" + row["id"])).json()
                    assert len(detail["screenshots"]) == 1, detail.keys()
                    thumb = await api.get("/thumbs/" + row["thumb_path"])
                    assert thumb.is_success and thumb.headers["content-type"].startswith("image/")
                completed.extend(["analysis_and_embeddings", "screenshots_and_thumbnails"])

                async def mcp(name, arguments):
                    response = await api.post(
                        "/mcp/",
                        headers={"Accept": "application/json, text/event-stream"},
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": name, "arguments": arguments},
                        },
                    )
                    response.raise_for_status()
                    result = response.json()["result"]
                    assert not result.get("isError"), result
                    return result.get("structuredContent") or json.loads(
                        result["content"][0]["text"]
                    )

                latest = await mcp("get_latest_activity", {"device_id": DEVICE})
                assert latest["count"] == 1
                start_ms = min(r["ts_start"] for r in rows) // 300000 * 300000
                end_ms = max(r["ts_start"] for r in rows) // 300000 * 300000 + 300000
                bounds = {
                    "period": "custom",
                    "start_iso": dt.datetime.fromtimestamp(
                        start_ms / 1000, dt.timezone.utc
                    ).isoformat(),
                    "end_iso": dt.datetime.fromtimestamp(
                        end_ms / 1000, dt.timezone.utc
                    ).isoformat(),
                    "device_id": DEVICE,
                }

                async def overview():
                    value = await mcp("get_activity_overview", bounds)
                    return value if value["devices"][0]["summary_state"] == "covered" else None

                overview_result = await until("background summary and MCP overview", overview)
                item = overview_result["devices"][0]["items"][0]
                summaries = await mcp(
                    "search_summaries",
                    {
                        "grain": "5min",
                        "period": "custom",
                        "start_iso": item["window_start_iso"],
                        "end_iso": item["window_end_iso"],
                        "device_id": DEVICE,
                    },
                )
                assert summaries["items"]
                page = await mcp(
                    "get_recent_activity", {"device_id": DEVICE, "limit": 1, "order": "desc"}
                )
                next_page = await mcp(
                    "get_recent_activity",
                    {
                        "device_id": DEVICE,
                        "limit": 1,
                        "order": "desc",
                        "cursor": page["next_cursor"],
                    },
                )
                assert page["items"][0]["id"] != next_page["items"][0]["id"]
                completed.append("summary_mcp_and_paging")

                response = await api.get("/metrics", headers={"X-Request-ID": "e2e-observation"})
                assert response.headers["x-request-id"] == "e2e-observation"
                assert 'timetrace_analysis_records{state="vlm_done"} 3.0' in response.text
                assert (
                    'timetrace_background_last_success_timestamp_seconds{task="rollup"}'
                    in response.text
                )
                (ARTIFACTS / "server-metrics.txt").write_text(response.text)
                response = await proxy.get("http://127.0.0.1:18764/metrics")
                assert "timetrace_client_outbox_pending_entries 0.0" in response.text
                (ARTIFACTS / "client-metrics.txt").write_text(response.text)
                completed.append("metrics_and_request_id")

                original_ids = {r["id"] for r in rows}
                (ARTIFACTS / "restart-server").touch()

                async def restarted():
                    return (ARTIFACTS / "server-restarted").exists() and await ready()

                await until("server container restart", restarted)
                persisted = (await api.get("/v1/records", params={"device_id": DEVICE})).json()[
                    "items"
                ]
                assert {r["id"] for r in persisted} == original_ids
                await until("summary readable after restart", overview)
                completed.append("server_restart_persistence")

                async with async_playwright() as p:
                    browser = await p.chromium.launch(args=["--no-sandbox"])
                    context = await browser.new_context(
                        viewport={"width": 1440, "height": 900}, timezone_id="Asia/Shanghai"
                    )
                    await context.tracing.start(screenshots=True, snapshots=True)
                    page = await context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    try:
                        await page.goto(BASE)
                        await page.locator('input[name="username"]').fill("admin")
                        await page.locator('input[name="password"]').fill(
                            "e2e-initial-password-123"
                        )
                        await page.get_by_role("button", name="登录", exact=True).click()
                        await page.wait_for_url("**/login/change-password")
                        await page.locator('input[name="old_password"]').fill(
                            "e2e-initial-password-123"
                        )
                        await page.locator('input[name="new_password"]').fill(
                            "e2e-changed-password-123"
                        )
                        await page.locator('input[name="confirm_password"]').fill(
                            "e2e-changed-password-123"
                        )
                        await page.locator('button[type="submit"]').click()
                        await page.wait_for_url(BASE + "/")
                        sample_date = dt.datetime.fromtimestamp(
                            rows[0]["ts_start"] / 1000, ZoneInfo("Asia/Shanghai")
                        ).date()
                        await page.goto(
                            f"{BASE}/?date={sample_date}&highlight={rows[0]['id']}&device={DEVICE}"
                        )
                        await expect(
                            page.get_by_text(rows[0]["vlm_desc"], exact=True).first
                        ).to_be_visible()
                        await page.wait_for_function(
                            "() => [...document.images].some(img => "
                            "img.src.includes('/thumbs/') && img.complete && img.naturalWidth > 0)"
                        )
                        await page.screenshot(path=str(ARTIFACTS / "timeline.png"), full_page=True)
                        await page.goto(BASE + "/settings")
                        await expect(
                            page.get_by_text("E2E Client", exact=True).first
                        ).to_be_visible()
                        await page.screenshot(path=str(ARTIFACTS / "settings.png"), full_page=True)
                        await page.goto("http://127.0.0.1:18764/")
                        await expect(page.locator("#pending")).to_have_text("0 项")
                        await page.get_by_role("button", name="暂停采集", exact=True).click()
                        await expect(page.locator("#capture")).to_have_text("已暂停")
                        await page.get_by_role("button", name="继续采集", exact=True).click()
                        await expect(page.locator("#capture")).to_have_text("正在采集")
                        await page.screenshot(
                            path=str(ARTIFACTS / "client-control.png"), full_page=True
                        )
                        assert not errors, errors
                    finally:
                        await context.tracing.stop(path=str(ARTIFACTS / "browser-trace.zip"))
                        await browser.close()
                completed.append("browser_login_timeline_device_and_client_control")
            return completed
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)
            client_log.close()
            server.should_exit = True
            await gateway_task


if __name__ == "__main__":
    started = time.monotonic()
    suite = ET.Element("testsuite", name="two-container-e2e", tests="1")
    case = ET.SubElement(suite, "testcase", name="client_server_acceptance")
    result = {"passed": False, "scenarios": []}
    try:
        asyncio.run(run(result["scenarios"]))
        result["passed"] = True
    except Exception:
        result["failure"] = traceback.format_exc()
        ET.SubElement(case, "failure").text = result["failure"]
        suite.set("failures", "1")
    result["duration_seconds"] = round(time.monotonic() - started, 2)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    ET.ElementTree(suite).write(ARTIFACTS / "junit.xml", encoding="utf-8", xml_declaration=True)
    print(json.dumps(result), flush=True)
    sys.exit(0 if result["passed"] else 1)
