"""VLM (vision-language model) client over the OpenAI Chat Completions protocol.

Works against OpenAI proper or any compatible endpoint (DashScope, SiliconFlow,
Ollama, vLLM, ...).

The thinking switch is opt-in: vanilla ``api.openai.com`` rejects unknown body
fields with HTTP 400, so we only attach ``extra_body={"enable_thinking": False}``
when the caller sets ``TIMETRACE_VLM_DISABLE_THINKING=true``. Endpoints whose
default has thinking ON (DashScope qwen / SiliconFlow Qwen3+) need that flag;
OpenAI proper must have it off.
"""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass
from typing import Any

import structlog
from openai import AsyncOpenAI
from PIL import Image

logger = structlog.get_logger(__name__)


# Long literal Chinese prompt; CJK characters skew the line-length lint.
# fmt: off
_DESCRIBE_PROMPT = (
    "请仔细观察这张桌面截图，**只输出 JSON 对象**（不要前缀、不要解释、不要 markdown 围栏）：\n\n"
    "{\n"
    '  "keywords":    ["..."],   // 截图中显著可见的文字、人名、产品名、文件名等关键词；按重要性排列；最多 8 项；可空数组\n'  # noqa: E501
    '  "summary":     "...",     // 30 字以内的画面要点\n'
    '  "description": "..."      // 100 字以内对画面的完整描述（用户在做什么、应用、内容大致主题）\n'  # noqa: E501
    "}\n"
)
# fmt: on
_HEARTBEAT_PROMPT = "1+1=? 直接给出数字答案，不要解释。"
_HEARTBEAT_EXPECTED = "2"
_DESCRIBE_TIMEOUT_S = 60.0
_HEARTBEAT_TIMEOUT_S = 15.0
_JPEG_QUALITY = 80
_MAX_DESCRIBE_EDGE = 1280  # cap longest edge before sending; cuts payload + cost


class VLMError(Exception):
    """Raised when a VLM call fails (network, parse, validation)."""


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class VLMConfig:
    base_url: str
    api_key: str
    model: str
    # Some OpenAI-compatible providers (DashScope qwen, SiliconFlow Qwen, etc.)
    # default thinking ON and accept ``extra_body={"enable_thinking": False}``
    # to disable it. Vanilla OpenAI rejects unknown body fields with HTTP 400,
    # so we only opt into this defensive override when the user asks for it.
    disable_thinking: bool = False

    @classmethod
    def from_env(cls) -> VLMConfig | None:
        """Build config from environment; return None if API key is missing/blank."""
        api_key = os.getenv("TIMETRACE_VLM_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            base_url=os.getenv("TIMETRACE_VLM_BASE_URL", "https://api.openai.com/v1").strip(),
            api_key=api_key,
            model=os.getenv("TIMETRACE_VLM_MODEL", "gpt-4o-mini").strip(),
            disable_thinking=_env_truthy(os.getenv("TIMETRACE_VLM_DISABLE_THINKING")),
        )


def format_description(d: dict) -> str:
    """Concatenate the three structured fields into a single LIKE-friendly string."""
    description = (d.get("description") or "").strip()
    summary = (d.get("summary") or "").strip()
    keywords = d.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    keywords_line = "、".join(str(k).strip() for k in keywords if str(k).strip())

    parts: list[str] = []
    if description:
        parts.append(description)
    if summary:
        parts.append(f"摘要：{summary}")
    if keywords_line:
        parts.append(f"关键词：{keywords_line}")
    return "\n\n".join(parts)


def _encode_image_data_url(image: Image.Image) -> str:
    img = image
    # Cap longest edge to control upload size + token usage.
    longest = max(img.width, img.height)
    if longest > _MAX_DESCRIBE_EDGE:
        scale = _MAX_DESCRIBE_EDGE / longest
        new_size = (int(img.width * scale), int(img.height * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _validate_describe_payload(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise VLMError(f"VLM response is not a JSON object: {type(raw).__name__}")
    keywords = raw.get("keywords")
    summary = raw.get("summary")
    description = raw.get("description")
    if not isinstance(keywords, list):
        raise VLMError("VLM response missing or non-list 'keywords'")
    if not isinstance(summary, str):
        raise VLMError("VLM response missing or non-string 'summary'")
    if not isinstance(description, str):
        raise VLMError("VLM response missing or non-string 'description'")
    return {
        "keywords": [str(k) for k in keywords],
        "summary": summary,
        "description": description,
    }


class VLMClient:
    """Thin async client over OpenAI Chat Completions for vision describe + heartbeat."""

    def __init__(self, cfg: VLMConfig) -> None:
        self._cfg = cfg
        self._client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    @property
    def model(self) -> str:
        return self._cfg.model

    @property
    def base_url(self) -> str:
        return self._cfg.base_url

    def _extra_kwargs(self) -> dict[str, Any]:
        """Build the kwargs that vary by provider (currently just thinking-off)."""
        if self._cfg.disable_thinking:
            return {"extra_body": {"enable_thinking": False}}
        return {}

    async def describe(
        self,
        image: Image.Image,
        window_title: str | None = None,
    ) -> dict:
        """Send an image to the VLM and return a validated 3-field dict.

        Raises VLMError on any failure (network, non-JSON, missing fields).
        """
        data_url = _encode_image_data_url(image)
        text = _DESCRIBE_PROMPT
        if window_title:
            text = f"{text}\n窗口标题（仅供辅助参考，可能不准确）：{window_title}"

        try:
            resp = await self._client.chat.completions.create(
                model=self._cfg.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": text},
                        ],
                    }
                ],
                response_format={"type": "json_object"},
                timeout=_DESCRIBE_TIMEOUT_S,
                **self._extra_kwargs(),
            )
        except Exception as exc:
            raise VLMError(f"VLM describe API call failed: {exc}") from exc

        try:
            content = resp.choices[0].message.content or ""
        except (IndexError, AttributeError) as exc:
            raise VLMError(f"VLM response shape unexpected: {exc}") from exc

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM returned non-JSON content: {exc}; raw={content[:200]!r}") from exc

        return _validate_describe_payload(parsed)

    async def heartbeat(self) -> bool:
        """Cheap text-only liveness probe. Returns True iff endpoint answers '2'."""
        try:
            resp = await self._client.chat.completions.create(
                model=self._cfg.model,
                messages=[{"role": "user", "content": _HEARTBEAT_PROMPT}],
                timeout=_HEARTBEAT_TIMEOUT_S,
                **self._extra_kwargs(),
            )
            content = resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("vlm.heartbeat_failed", error=str(exc))
            return False
        return _HEARTBEAT_EXPECTED in content

    async def aclose(self) -> None:
        await self._client.close()
