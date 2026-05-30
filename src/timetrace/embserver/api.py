"""FastAPI app for the embedding daemon.

Public surface aligns with SiliconFlow's EmbeddingsVLRequest (the de-facto
multimodal-embedding shape for this very model), so text callers are drop-in
OpenAI-compatible and image callers use the documented `{image: url|base64}`
extension. Response is the standard OpenAI embeddings envelope.
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import Any, Union

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import EmbServerConfig
from .engine import EmbeddingEngine, EngineError, normalize_input

# TTL sweep cadence — how often the idle-unload check runs.
_TTL_SWEEP_SECONDS = 15

logger = structlog.get_logger(__name__)

# SiliconFlow input: string | {text}|{image}|{text,image} | list of those.
InputItem = Union[str, dict]
EmbInput = Union[str, dict, list[InputItem]]


class EmbeddingRequest(BaseModel):
    input: EmbInput
    model: str | None = None
    encoding_format: str = Field(default="float")


class LoadRequest(BaseModel):
    dtype: str | None = None
    model_path: str | None = None


class TtlRequest(BaseModel):
    ttl_seconds: int


class SelftestRequest(BaseModel):
    threshold: float = 0.999


def _require_key(cfg: EmbServerConfig):
    async def dep(authorization: str | None = Header(default=None)) -> None:
        expected = cfg.api_key or ""
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not expected or not secrets.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    return dep


def create_app(cfg: EmbServerConfig, engine: EmbeddingEngine) -> FastAPI:
    async def _ttl_sweep() -> None:
        while True:
            await asyncio.sleep(_TTL_SWEEP_SECONDS)
            try:
                await engine.maybe_unload_if_idle()
            except Exception:  # noqa: BLE001
                logger.warning("embserver.ttl.sweep_error", exc_info=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if cfg.preload:
            try:
                await engine.ensure_loaded()
            except EngineError:
                logger.warning("embserver.preload.failed", exc_info=True)
        sweeper = asyncio.create_task(_ttl_sweep())
        yield
        sweeper.cancel()
        await engine.unload()

    app = FastAPI(title="TimeTrace Embedding Server", lifespan=lifespan)
    auth = _require_key(cfg)

    @app.get("/healthz")
    async def healthz() -> dict:
        # Liveness only — does NOT prove the model is loadable (JIT happens on
        # first /v1/embeddings). Mirrors the main API's healthz semantics.
        return {"status": "ok", "loaded": engine.loaded}

    @app.get("/admin/status", dependencies=[Depends(auth)])
    async def status() -> dict:
        return engine.status()

    @app.post("/admin/load", dependencies=[Depends(auth)])
    async def load(req: LoadRequest) -> dict:
        try:
            await engine.reload(dtype=req.dtype, model_path=req.model_path)
        except EngineError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return engine.status()

    @app.post("/admin/unload", dependencies=[Depends(auth)])
    async def unload() -> dict:
        await engine.unload()
        return engine.status()

    @app.post("/admin/ttl", dependencies=[Depends(auth)])
    async def set_ttl(req: TtlRequest) -> dict:
        engine.idle_ttl_seconds = max(0, req.ttl_seconds)
        return engine.status()

    @app.post("/admin/selftest", dependencies=[Depends(auth)])
    async def selftest(req: SelftestRequest) -> dict:
        from .selftest import run_selftest  # noqa: PLC0415

        try:
            return await run_selftest(engine, threshold=req.threshold)
        except EngineError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/v1/embeddings", dependencies=[Depends(auth)])
    async def embeddings(req: EmbeddingRequest) -> dict[str, Any]:
        try:
            items = normalize_input(req.input)
        except EngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            vectors = await engine.embed(items)
        except EngineError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
        return {
            "object": "list",
            "data": data,
            "model": req.model or cfg.model_path.name,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    return app
