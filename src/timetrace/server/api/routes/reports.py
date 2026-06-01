"""AI-generated insight dashboards ("看板").

GET  /v1/reports/latest?scope=recent_24h    → newest stored report (404 if none)
POST /v1/reports/generate {scope}           → generate now (synchronous), returns it
POST /v1/reports/generate/stream {scope}    → generate now, SSE-stream the agent's
                                              tool steps + live token output, then
                                              the final report (so the UI can show
                                              "正在分析…" + what the model is writing)

Browser-facing → same ``require_principal`` gate as records (wired in
create_app via business_deps). Generation calls the LLM (slow + costs money),
so the scheduler in bootstrap refreshes on a 30-min cadence; the POSTs are the
manual "重新生成" button.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from timetrace.server.report.generator import SCOPE_HOURS, ReportGenerator

router = APIRouter(tags=["reports"])


class GenerateRequest(BaseModel):
    scope: str = "recent_24h"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/reports/latest")
async def latest_report(request: Request, scope: str = "recent_24h") -> dict:
    if scope not in SCOPE_HOURS:
        raise HTTPException(status_code=422, detail=f"unknown scope: {scope}")
    report = await request.app.state.db.get_latest_report(scope)
    if report is None:
        raise HTTPException(status_code=404, detail="no report yet for this scope")
    return report


@router.post("/reports/generate")
async def generate_report(req: GenerateRequest, request: Request) -> dict:
    if req.scope not in SCOPE_HOURS:
        raise HTTPException(status_code=422, detail=f"unknown scope: {req.scope}")
    vlm_cfg = getattr(request.app.state, "vlm_cfg", None)
    if vlm_cfg is None:
        raise HTTPException(status_code=503, detail="LLM 未配置，无法生成看板")
    gen = ReportGenerator(request.app.state.db, vlm_cfg)
    return await gen.generate(req.scope)


@router.post("/reports/generate/stream")
async def generate_report_stream(req: GenerateRequest, request: Request) -> StreamingResponse:
    vlm_cfg = getattr(request.app.state, "vlm_cfg", None)
    db = request.app.state.db

    async def gen() -> AsyncIterator[str]:
        if req.scope not in SCOPE_HOURS:
            yield _sse({"type": "error", "message": f"未知的时间范围: {req.scope}"})
            return
        if vlm_cfg is None:
            yield _sse({"type": "error", "message": "LLM 未配置，无法生成看板"})
            return
        generator = ReportGenerator(db, vlm_cfg)
        async for ev in generator.generate_stream(req.scope):
            yield _sse(ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
