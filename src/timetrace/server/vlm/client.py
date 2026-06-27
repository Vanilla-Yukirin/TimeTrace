"""VLM (vision-language model) client over the OpenAI Chat Completions protocol.

Works against OpenAI proper or any compatible endpoint (DashScope, SiliconFlow,
Ollama, vLLM, LM Studio, ...).

Two provider-quirk knobs:

1. ``disable_thinking`` (``TIMETRACE_VLM_DISABLE_THINKING=true``) attaches
   ``extra_body={"enable_thinking": False}`` for DashScope / SiliconFlow Qwen3+
   whose default is thinking-on. Vanilla OpenAI rejects unknown body fields
   with HTTP 400, so it's opt-in.

2. ``response_format`` uses ``json_schema`` (structured outputs, GA in
   OpenAI 2024-08+ and supported by LM Studio / vLLM / llama.cpp grammars).
   Older ``json_object`` is rejected by LM Studio with "must be 'json_schema'
   or 'text'", and on OpenAI it does not enforce schema fields anyway.

3. Reasoning-content fallback: LM Studio detects Qwen3+ as a reasoning model
   and pipes ALL output to the non-standard ``reasoning_content`` field with
   ``content`` empty — even when thinking is explicitly disabled via prompt
   tag, chat_template_kwargs, or extra_body. We read both and prefer content,
   so vanilla OpenAI (no reasoning_content) still works.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import structlog
from openai import AsyncOpenAI
from PIL import Image

from timetrace.common.config import VLMConfig
from timetrace.server.llm_log import timed_chat_completion

logger = structlog.get_logger(__name__)


# Category ids the VLM must choose from — mirror of _BUILTIN_CATEGORIES in
# server/db/sqlite.py. Keep the two in sync.
_CATEGORY_IDS = ("work", "study", "social", "entertainment", "system", "uncategorized")

# Long literal Chinese prompt; CJK characters skew the line-length lint.
# fmt: off
_DESCRIBE_PROMPT = (
    "请仔细观察这张桌面截图，**只输出 JSON 对象**（不要前缀、不要解释、不要 markdown 围栏）：\n\n"
    "{\n"
    '  "keywords":    ["..."],   // 截图中显著可见的文字、人名、产品名、文件名等关键词；按重要性排列；最多 8 项；可空数组\n'  # noqa: E501
    '  "summary":     "...",     // 30 字以内的画面要点\n'
    '  "description": "...",     // 100 字以内对画面的完整描述（用户在做什么、应用、内容大致主题）\n'  # noqa: E501
    '  "category":    "..."      // 从下面 6 类里选 1 个最贴切的，只填英文 id\n'  # noqa: E501
    "}\n\n"
    "**category 只能从这 6 个英文 id 里选 1 个**：\n"
    "- work=工作（编程/写文档/邮件/工作类工具）\n"
    "- study=学习（阅读/课程/研究/做题）\n"
    "- social=沟通（微信/QQ/飞书/钉钉/会议等即时通讯）\n"
    "- entertainment=娱乐（游戏/视频/音乐/刷社交媒体）\n"
    "- system=系统（文件管理/设置/桌面/空闲等系统与工具操作）\n"
    "- uncategorized=实在判断不了时才用\n\n"
    "**summary 与 description 的硬性写作规范**：\n"
    "1. 必须以**名词性短语**开头（直接命名画面主体），严禁以陈述句、判断句或动宾结构开头。\n"
    "2. 严禁出现的开头句式（包括但不限于）："
    "「该截图…」「这张图…」「图中…」「画面中…」「此图…」「此截图…」"
    "「画面为…」「画面显示…」「展示了…」「该画面…」「这是…」「截图展示…」"
    "「开发者正在…」「用户正在…」等任何以「截图/画面/图」为主语或以「这是/正在/展示」为谓语的开头。\n"
    "3. 正例：「微信桌面客户端深色模式群聊界面，左侧会话列表含 Damian 与文件传输助手…」\n"
    "4. 反例：「该截图展示了一款深色模式下的桌面即时通讯软件界面…」「这是微信PC客户端…」「画面显示 Microsoft Edge 浏览…」\n"  # noqa: E501
    "5. 把视角放在画面内容本身，不要把「截图/画面」当成被描述的对象。\n"
)
# fmt: on
_HEARTBEAT_PROMPT = "1+1=? 直接给出数字答案，不要解释。"
_HEARTBEAT_EXPECTED = "2"
_DESCRIBE_TIMEOUT_S = 120.0  # LM Studio cold-load + first inference on Qwen3-35BA3B can take ~60s
_HEARTBEAT_TIMEOUT_S = 30.0
_JPEG_QUALITY = 80
_MAX_DESCRIBE_EDGE = 1280  # cap longest edge before sending; cuts payload + cost

# JSON schema for structured outputs. Matches _validate_describe_payload exactly.
_DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "category": {"type": "string", "enum": list(_CATEGORY_IDS)},
    },
    "required": ["keywords", "summary", "description", "category"],
    "additionalProperties": False,
}


def _extract_message_content(message: Any) -> str:
    """Return message.content, falling back to reasoning_content for LM Studio.

    LM Studio routes Qwen3+ output to a non-standard ``reasoning_content``
    field while leaving ``content`` empty; vanilla OpenAI has only ``content``.
    Read via getattr so plain ``SimpleNamespace`` test doubles work the same
    as pydantic ``ChatCompletionMessage`` instances.
    """
    content = getattr(message, "content", None) or ""
    if not content:
        content = getattr(message, "reasoning_content", None) or ""
    return content


class VLMError(Exception):
    """Raised when a VLM call fails (network, parse, validation)."""


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
    # category is best-effort: even with the enum schema some servers omit it or
    # return an off-list value; fall back to uncategorized rather than failing
    # the whole describe (the description is still useful without a category).
    category = raw.get("category")
    if not isinstance(category, str) or category not in _CATEGORY_IDS:
        category = "uncategorized"
    return {
        "keywords": [str(k) for k in keywords],
        "summary": summary,
        "description": description,
        "category": category,
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
        app_note: str | None = None,
    ) -> dict:
        """Send an image to the VLM and return a validated 3-field dict.

        ``app_note`` is optional user-provided context about the current app
        (from the per-app overrides setting); when present it's appended as a
        hint so the model has background for ambiguous windows.

        Raises VLMError on any failure (network, non-JSON, missing fields).
        """
        data_url = _encode_image_data_url(image)
        text = _DESCRIBE_PROMPT
        if window_title:
            text = f"{text}\n窗口标题（仅供辅助参考）：{window_title}"
        if app_note:
            text = f"{text}\n关于当前应用的额外背景（用户提供，仅供参考）：{app_note}"

        try:
            resp = await timed_chat_completion(
                self._client,
                caller="worker_vlm",
                model=self._cfg.model,
                prompt_chars=len(text),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": text},
                        ],
                    }
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "describe",
                        "strict": True,
                        "schema": _DESCRIBE_SCHEMA,
                    },
                },
                timeout=_DESCRIBE_TIMEOUT_S,
                **self._extra_kwargs(),
            )
        except Exception as exc:
            raise VLMError(f"VLM describe API call failed: {exc}") from exc

        try:
            content = _extract_message_content(resp.choices[0].message)
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
            content = _extract_message_content(resp.choices[0].message)
        except Exception as exc:  # noqa: BLE001
            logger.debug("vlm.heartbeat_failed", error=str(exc))
            return False
        return _HEARTBEAT_EXPECTED in content

    async def aclose(self) -> None:
        await self._client.close()
