"""AI-generated insight dashboards ("看板").

GET  /v1/reports/latest?scope=recent_24h  → newest stored report (404 if none)
POST /v1/reports/generate {scope}         → generate now (synchronous), returns it

Browser-facing → same ``require_principal`` gate as records (wired in
create_app via business_deps). Generation calls the LLM (slow + costs money),
so the scheduler in bootstrap refreshes on a 30-min cadence; this POST is the
manual "重新生成" button.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from timetrace.server.report.generator import SCOPE_HOURS, ReportGenerator

router = APIRouter(tags=["reports"])


class GenerateRequest(BaseModel):
    scope: str = "recent_24h"


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
