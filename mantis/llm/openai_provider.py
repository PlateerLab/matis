"""LLM 모델 클라이언트 — xgen-model 또는 외부 API 호출."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """LLM이 요청한 도구 호출."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    """LLM 응답."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class ModelClient:
    """OpenAI-호환 API 클라이언트.

    xgen-model이든 OpenAI든 Claude든, /v1/chat/completions 호환이면 사용 가능.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "default",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._build_headers(),
        )

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> ModelResponse:
        """LLM 호출 (non-streaming)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_response(data)

    async def generate_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str | ToolCall]:
        """LLM 호출 (SSE streaming)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with self._client.stream(
            "POST", "/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        yield delta["content"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    def _parse_response(self, data: dict) -> ModelResponse:
        """API 응답을 ModelResponse로 파싱."""
        choice = data["choices"][0]
        message = choice["message"]

        tool_calls = []
        if "tool_calls" in message and message["tool_calls"]:
            for tc in message["tool_calls"]:
                args = tc["function"].get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append(
                    ToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=args,
                    )
                )

        usage = data.get("usage", {})

        return ModelResponse(
            text=message.get("content"),
            tool_calls=tool_calls,
            usage=usage,
        )

    async def close(self):
        await self._client.aclose()
