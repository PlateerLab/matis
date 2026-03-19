"""ExecutionPipeline — Phase 기반 실행 파이프라인 조합기."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from mantis.pipeline.models import (
    ExecutionRequest,
    ExecutionResult,
    StreamEvent,
)
from mantis.pipeline.phases import (
    PreparePhase,
    ResolvePhase,
    ExecutePhase,
    StreamPhase,
    PersistPhase,
)

logger = logging.getLogger(__name__)


class ExecutionPipeline:
    """Phase 기반 실행 파이프라인.

    PREPARE → RESOLVE → EXECUTE → STREAM → PERSIST
    각 Phase가 독립적이라 확장/테스트/디버깅이 용이하다.
    """

    def __init__(
        self,
        prepare: PreparePhase,
        resolve: ResolvePhase,
        execute: ExecutePhase,
        stream: StreamPhase | None = None,
        persist: PersistPhase | None = None,
    ):
        self.prepare = prepare
        self.resolve = resolve
        self.execute = execute
        self.stream = stream or StreamPhase()
        self.persist = persist or PersistPhase()

    async def run(self, request: ExecutionRequest) -> AsyncIterator[StreamEvent]:
        """전체 파이프라인 실행. StreamEvent를 async yield."""

        # Phase 1: PREPARE
        context = await self.prepare.run(request)
        logger.info("Phase 1 (PREPARE) 완료: mode=%s, session=%s", context.mode, context.session_id)

        # Phase 2: RESOLVE
        resolved = await self.resolve.run(context)
        logger.info(
            "Phase 2 (RESOLVE) 완료: tools=%d",
            len(resolved.tools_schema),
        )

        # Phase 3 + Phase 4: EXECUTE → STREAM (파이프라인 연결)
        execution_events = self.execute.run(resolved)
        result = ExecutionResult(
            session_id=context.session_id,
            input_data=request.input_data,
        )

        try:
            async for stream_event in self.stream.run(execution_events):
                yield stream_event

                # done/error 이벤트에서 결과 캡처
                if stream_event.event == "done":
                    result.status = "completed"
                elif stream_event.event == "error":
                    result.status = "error"
        except Exception as e:
            logger.error("파이프라인 실행 중 오류: %s", e)
            result.status = "error"
            raise
        finally:
            # Phase 5: PERSIST (항상 실행)
            try:
                await self.persist.run(result)
                logger.info("Phase 5 (PERSIST) 완료")
            except Exception as e:
                logger.error("Phase 5 (PERSIST) 실패: %s", e)


def build_pipeline(
    llm: Any,
    tool_registry: Any,
    *,
    system_prompt: str = "",
    graph_search: Any | None = None,
    approval: Any | None = None,
    state_store: Any | None = None,
    trace_collector: Any | None = None,
    tester: Any | None = None,
    stream_adapter: Any | None = None,
    max_iterations: int = 50,
) -> ExecutionPipeline:
    """편의 빌더 — 모든 Phase를 한 번에 조립."""

    return ExecutionPipeline(
        prepare=PreparePhase(
            system_prompt=system_prompt,
            state_store=state_store,
        ),
        resolve=ResolvePhase(
            tool_registry=tool_registry,
            graph_search=graph_search,
            tester=tester,
        ),
        execute=ExecutePhase(
            llm=llm,
            tool_registry=tool_registry,
            approval=approval,
            graph_search=graph_search,
            state_store=state_store,
            max_iterations=max_iterations,
        ),
        stream=StreamPhase(adapter=stream_adapter),
        persist=PersistPhase(
            trace_collector=trace_collector,
            state_store=state_store,
        ),
    )
