"""Validated connection edits shared by the desktop UI and loopback API."""

from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def connection_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.path
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(ch.isspace() or not ch.isprintable() for ch in value)
            or parts.port == 0
        ):
            raise ValueError
        httpx.URL(value)
    except (ValueError, httpx.InvalidURL):
        raise ValueError(
            "请输入服务端地址，例如 https://example.com 或 http://192.168.1.2:8765"
        ) from None
    return value


class ConnectionPath(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str = Field(max_length=80)
    url: str = Field(max_length=2048)
    enabled: bool = True
    keep_index: int | None = Field(default=None, ge=0)


class ConnectionCredentials(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    token: SecretStr | None = None

    @field_validator("token")
    @classmethod
    def validate_token(cls, token: SecretStr | None) -> SecretStr | None:
        if token is None:
            return None
        value = token.get_secret_value()
        if len(value) > 4096 or any(not 33 <= ord(ch) <= 126 for ch in value):
            raise ValueError("Token 格式无效")
        return token


class ConnectionTest(ConnectionCredentials):
    url: str = Field(max_length=2048)
    same_server: bool = False


class ConnectionEdit(ConnectionCredentials):
    paths: list[ConnectionPath] = Field(max_length=32)
    revision: int = Field(ge=0)
    same_server: bool = False


async def probe_connection(url: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(url + "/v1/runtime-info", headers=headers)
    except httpx.RequestError:
        raise ValueError("无法连接服务端，请检查地址、网络和证书") from None
    if response.status_code in {401, 403}:
        raise ValueError("服务端拒绝访问，请检查设备 Token")
    try:
        info = response.json() if response.status_code == 200 else None
    except ValueError:
        info = None
    if not isinstance(info, dict) or not isinstance(info.get("version"), str):
        raise ValueError(f"服务端未返回 TimeTrace 响应（HTTP {response.status_code}）")
