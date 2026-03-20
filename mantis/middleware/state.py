"""상태 미들웨어 — StateStore를 Agent 루프에 통합."""

from __future__ import annotations

import logging
from typing import Any

from mantis.middleware.base import BaseMiddleware, RunContext

logger = logging.getLogger(__name__)

# asyncpg는 선택 의존성
try:
    from mantis.state.store import StateStore
    _HAS_STATE = True
except ImportError:
    _HAS_STATE = False
    StateStore = None  # type: ignore[assignment,misc]


class StateMiddleware(BaseMiddleware):
    """세션 상태를 자동 체크포인트하는 미들웨어.

    on_start에서 기존 세션 복구를 시도하고,
    on_after_tool / on_end에서 상태를 자동 저장한다.

    사용법:
        store = StateStore("postgresql://...")
        await store.initialize()
        mw = StateMiddleware(store=store)
        runner.add_middleware(mw)
    """

    def __init__(self, store: Any = None):
        self._store = store
        self._step_count: int = 0

    @property
    def available(self) -> bool:
        """StateStore가 사용 가능한지 여부."""
        return self._store is not None

    async def on_start(self, ctx: RunContext) -> None:
        """세션 시작 — 기존 체크포인트 복구 시도."""
        if not self.available:
            return

        try:
            state = await self._store.resume(ctx.session_id)
            if state:
                logger.info(
                    "StateMiddleware: 세션 '%s' 복구됨 (키 %d개)",
                    ctx.session_id,
                    len(state),
                )
                # 복구된 상태를 메타데이터에 기록
                ctx.metadata["restored_state"] = state
            else:
                logger.debug(
                    "StateMiddleware: 세션 '%s' — 기존 체크포인트 없음",
                    ctx.session_id,
                )
        except Exception as e:
            logger.warning(
                "StateMiddleware: 세션 복구 실패 (무시): %s", e
            )

        self._step_count = 0

    async def on_after_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict, result: dict
    ) -> None:
        """도구 호출 후 체크포인트 저장."""
        if not self.available:
            return

        self._step_count += 1

        state = {
            "session_id": ctx.session_id,
            "agent_name": ctx.agent_name,
            "trace_id": ctx.trace_id,
            "step_count": self._step_count,
            "last_tool": tool_name,
            "last_arguments": arguments,
            "metadata": ctx.metadata,
        }

        try:
            await self._store.checkpoint(ctx.session_id, state)
            logger.debug(
                "StateMiddleware: 체크포인트 저장 — session=%s, step=%d",
                ctx.session_id,
                self._step_count,
            )
        except Exception as e:
            logger.warning(
                "StateMiddleware: 체크포인트 저장 실패 (무시): %s", e
            )

    async def on_end(self, ctx: RunContext, output: str) -> None:
        """Agent 종료 — 최종 체크포인트 저장."""
        if not self.available:
            return

        state = {
            "session_id": ctx.session_id,
            "agent_name": ctx.agent_name,
            "trace_id": ctx.trace_id,
            "step_count": self._step_count,
            "completed": True,
            "output_preview": output[:500] if output else "",
            "metadata": ctx.metadata,
        }

        try:
            await self._store.checkpoint(ctx.session_id, state)
            logger.debug(
                "StateMiddleware: 최종 체크포인트 — session=%s",
                ctx.session_id,
            )
        except Exception as e:
            logger.warning(
                "StateMiddleware: 최종 체크포인트 실패 (무시): %s", e
            )
