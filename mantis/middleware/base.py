"""미들웨어 기반 클래스 — Protocol + 기본 구현."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class RunContext:
    """Agent 실행 컨텍스트 — 미들웨어 간 공유 상태."""

    session_id: str
    agent_name: str
    trace_id: str | None = None
    last_user_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ─── blocked 메커니즘 ───
    _blocked_tools: dict[str, str] = field(default_factory=dict, repr=False)

    def block_tool(self, call_id: str, reason: str) -> None:
        """특정 도구 호출을 차단 등록."""
        self._blocked_tools[call_id] = reason

    def is_blocked(self, call_id: str) -> str | None:
        """차단 사유 반환. 차단되지 않았으면 None."""
        return self._blocked_tools.get(call_id)

    def clear_blocked(self) -> None:
        """차단 목록 초기화."""
        self._blocked_tools.clear()


@runtime_checkable
class Middleware(Protocol):
    """미들웨어 Protocol — Agent 루프의 횡단 관심사 인터페이스."""

    async def on_start(self, ctx: RunContext) -> None:
        """Agent 실행 시작 시 호출."""
        ...

    async def on_before_llm(self, ctx: RunContext, tools: list[dict]) -> list[dict]:
        """LLM 호출 전. tools 목록을 필터/변환하여 반환."""
        ...

    async def on_before_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict
    ) -> tuple[str, dict, str | None]:
        """도구 호출 전. (name, args, block_reason) 반환. block_reason이 None이면 허용."""
        ...

    async def on_after_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict, result: dict
    ) -> None:
        """도구 호출 후."""
        ...

    async def on_end(self, ctx: RunContext, output: str) -> None:
        """Agent 실행 종료 시 호출."""
        ...


class BaseMiddleware:
    """기본 미들웨어 — 모든 메서드가 no-op (pass-through).

    구체 미들웨어는 이 클래스를 상속하여 필요한 메서드만 오버라이드한다.
    """

    async def on_start(self, ctx: RunContext) -> None:
        pass

    async def on_before_llm(self, ctx: RunContext, tools: list[dict]) -> list[dict]:
        return tools

    async def on_before_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict
    ) -> tuple[str, dict, str | None]:
        return tool_name, arguments, None

    async def on_after_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict, result: dict
    ) -> None:
        pass

    async def on_end(self, ctx: RunContext, output: str) -> None:
        pass
