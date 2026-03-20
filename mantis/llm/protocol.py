"""LLM Provider 프로토콜 및 공통 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


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


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 호출 프로토콜.

    OpenAI-호환 API, 커스텀 모델 서버 등 다양한 백엔드를 추상화.
    """

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> ModelResponse: ...
