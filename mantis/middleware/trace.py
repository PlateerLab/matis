"""트레이스 미들웨어 — TraceCollector를 Agent 루프에 통합."""

from __future__ import annotations

import logging
from typing import Any

from mantis.middleware.base import BaseMiddleware, RunContext
from mantis.trace.collector import TraceCollector, StepType

logger = logging.getLogger(__name__)


class TraceMiddleware(BaseMiddleware):
    """Agent 실행 전 과정을 자동 추적하는 미들웨어.

    on_start에서 트레이스를 시작하고, on_after_tool에서 각 단계를 기록하며,
    on_end에서 트레이스를 종료한다.

    사용법:
        mw = TraceMiddleware()
        runner.add_middleware(mw)
        # 실행 후 mw.collector.list_traces()로 조회 가능
    """

    def __init__(self, collector: TraceCollector | None = None):
        self._collector = collector or TraceCollector()

    @property
    def collector(self) -> TraceCollector:
        """내부 TraceCollector 접근."""
        return self._collector

    async def on_start(self, ctx: RunContext) -> None:
        """트레이스 시작 — trace_id를 RunContext에 기록."""
        trace_id = self._collector.start_trace(
            session_id=ctx.session_id,
            agent_name=ctx.agent_name,
        )
        ctx.trace_id = trace_id
        logger.debug("TraceMiddleware: 트레이스 시작 — %s", trace_id)

    async def on_after_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict, result: dict
    ) -> None:
        """도구 호출 결과를 트레이스에 기록."""
        if not ctx.trace_id:
            return

        self._collector.add_step(
            trace_id=ctx.trace_id,
            step_type=StepType.TOOL_CALL,
            data={
                "tool": tool_name,
                "arguments": arguments,
                "result": result,
            },
        )

    async def on_end(self, ctx: RunContext, output: str) -> None:
        """트레이스 종료."""
        if not ctx.trace_id:
            return

        # 최종 응답도 기록
        self._collector.add_step(
            trace_id=ctx.trace_id,
            step_type=StepType.RESPONSE,
            data={"output": output},
        )

        trace = self._collector.end_trace(ctx.trace_id)
        if trace:
            logger.debug(
                "TraceMiddleware: 트레이스 종료 — %s (%.0fms)",
                ctx.trace_id,
                trace.duration_ms or 0,
            )
