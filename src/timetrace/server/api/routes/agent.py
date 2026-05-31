"""Web-facing agent chat — a tool-calling loop over the local LLM, streamed as SSE.

POST /v1/agent/chat
  req : {messages:[{role,content}], hours_back?}
  resp: text/event-stream of ``data: {json}`` events (see AgentRunner for shapes)

Browser-facing, so it's gated by the same ``require_principal`` (cookie OR
bearer) as records/search/feedback — wired in ``create_app`` via the shared
``business_deps``. Distinct from the ``/mcp`` surface, which is bearer-only for
machine clients (Claude Code).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from timetrace.server.agent.runner import AgentRunner

router = APIRouter(tags=["agent"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    hours_back: int | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/agent/chat")
async def agent_chat(req: ChatRequest, request: Request) -> StreamingResponse:
    vlm_cfg = getattr(request.app.state, "vlm_cfg", None)
    db = request.app.state.db

    async def gen() -> AsyncIterator[str]:
        if vlm_cfg is None:
            yield _sse(
                {
                    "type": "error",
                    "message": "LLM 未配置（设置 TIMETRACE_VLM_* 环境变量后重启服务）",
                }
            )
            return
        runner = AgentRunner(db, vlm_cfg)
        try:
            async for ev in runner.run(
                [m.model_dump() for m in req.messages],
                hours_back_hint=req.hours_back,
            ):
                yield _sse(ev)
        finally:
            await runner.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tell nginx not to buffer the stream (matches the MCP proxy needs).
            "X-Accel-Buffering": "no",
        },
    )
