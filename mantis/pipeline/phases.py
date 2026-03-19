"""Phase 구현 — PREPARE / RESOLVE / EXECUTE / STREAM / PERSIST."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Protocol

from mantis.pipeline.models import (
    ExecutionRequest,
    ExecutionContext,
    ResolvedContext,
    ExecutionEvent,
    StreamEvent,
    ExecutionResult,
)

logger = logging.getLogger(__name__)


# ─── Phase 1: PREPARE ───


class PreparePhase:
    """전처리 — 요청을 실행 가능한 컨텍스트로 변환."""

    def __init__(
        self,
        system_prompt: str = "",
        state_store: Any | None = None,
    ):
        self.system_prompt = system_prompt
        self.state_store = state_store

    async def run(self, request: ExecutionRequest) -> ExecutionContext:
        session_id = request.session_id or str(uuid.uuid4())

        if request.workflow_data:
            order = self._topological_sort(request.workflow_data)
            return ExecutionContext(
                mode="workflow",
                message=request.input_data,
                session_id=session_id,
                system_prompt=self.system_prompt,
                workflow_order=order,
                metadata=request.config,
            )

        return ExecutionContext(
            mode="agent",
            message=request.input_data,
            session_id=session_id,
            system_prompt=self.system_prompt,
            metadata=request.config,
        )

    def _topological_sort(self, workflow_data: dict) -> list[str]:
        """노드 DAG 위상 정렬. 간단한 구현."""
        nodes = workflow_data.get("nodes", [])
        edges = workflow_data.get("edges", [])

        # 인접 리스트 + 진입차수
        adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
        in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}

        for edge in edges:
            src, dst = edge["source"], edge["target"]
            if src in adj:
                adj[src].append(dst)
            if dst in in_degree:
                in_degree[dst] += 1

        # BFS
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order: list[str] = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return order


# ─── Phase 2: RESOLVE ───


class ResolvePhase:
    """결정 — 도구 검색, RAG, 메모리 수집."""

    def __init__(
        self,
        tool_registry: Any | None = None,
        graph_search: Any | None = None,
        tester: Any | None = None,
        search_threshold: int = 10,
    ):
        self.tool_registry = tool_registry
        self.graph_search = graph_search
        self.tester = tester
        self.search_threshold = search_threshold

    async def run(self, ctx: ExecutionContext) -> ResolvedContext:
        tools_schema: list[dict] = []

        if self.tool_registry:
            all_tools = self.tool_registry.list_tools()

            # 도구 검증 게이트
            if self.tester:
                verified = []
                for t in all_tools:
                    result = self.tester.validate_schema(t)
                    if result.passed:
                        verified.append(t)
                    else:
                        logger.warning(
                            "도구 '%s' 검증 실패, LLM에 전달 안 함: %s",
                            t.name,
                            result.errors,
                        )
                all_tools = verified

            # 도구 검색 (graph-tool-call)
            if (
                self.graph_search
                and self.graph_search.should_use_search
                and len(all_tools) >= self.search_threshold
            ):
                tools_schema = self.graph_search.retrieve_as_openai_tools(
                    ctx.message
                )
            else:
                tools_schema = self.tool_registry.to_openai_tools()

        return ResolvedContext(
            context=ctx,
            tools=all_tools if self.tool_registry else [],
            tools_schema=tools_schema,
            system_prompt=ctx.system_prompt,
        )


# ─── Phase 3: EXECUTE ───


class ExecutePhase:
    """실행 — Think→Act→Observe 루프."""

    def __init__(
        self,
        llm: Any,
        tool_registry: Any,
        context_manager: Any | None = None,
        approval: Any | None = None,
        graph_search: Any | None = None,
        state_store: Any | None = None,
        max_iterations: int = 50,
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.context_manager = context_manager
        self.approval = approval
        self.graph_search = graph_search
        self.state_store = state_store
        self.max_iterations = max_iterations

    async def run(self, resolved: ResolvedContext) -> AsyncIterator[ExecutionEvent]:
        ctx = resolved.context
        from mantis.context.conversation import ConversationContext

        conversation = self.context_manager or ConversationContext(
            system_prompt=resolved.system_prompt
        )
        conversation.add_user(ctx.message)

        for iteration in range(self.max_iterations):
            # Think
            yield ExecutionEvent("thinking", {"iteration": iteration + 1})

            think_start = time.time()
            try:
                response = await self.llm.generate(
                    messages=conversation.to_messages(),
                    tools=resolved.tools_schema or None,
                )
            except Exception as e:
                logger.error("모델 호출 실패: %s", e)
                yield ExecutionEvent("error", {"error": str(e), "resumable": True})
                return

            think_ms = (time.time() - think_start) * 1000

            # 종료 조건
            if not response.has_tool_calls:
                conversation.add_assistant(content=response.text)
                yield ExecutionEvent("done", {"text": response.text or ""})
                return

            # Assistant 메시지 기록
            conversation.add_assistant(
                content=response.text,
                tool_calls=[
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ],
            )

            # Act
            for tc in response.tool_calls:
                # 승인 체크
                if self.approval and self.approval.requires_approval(
                    tc.name, tc.arguments
                ):
                    yield ExecutionEvent(
                        "approval_required",
                        {"tool": tc.name, "arguments": tc.arguments},
                    )
                    # 승인 대기는 호출자가 처리

                # 도구 이름 교정
                actual_name = tc.name
                actual_args = tc.arguments
                if self.graph_search:
                    validation = self.graph_search.validate_call(
                        tc.name, tc.arguments
                    )
                    if (
                        validation.get("corrected_name")
                        and validation["corrected_name"] != tc.name
                    ):
                        actual_name = validation["corrected_name"]
                    if validation.get("corrected_arguments"):
                        actual_args = validation["corrected_arguments"]

                yield ExecutionEvent(
                    "tool_call", {"name": actual_name, "arguments": actual_args}
                )

                # 도구 실행
                tool_start = time.time()
                try:
                    result = await self.tool_registry.execute(
                        {"name": actual_name, "arguments": actual_args}
                    )
                except Exception as e:
                    logger.error("도구 '%s' 실행 실패: %s", actual_name, e)
                    result = {"name": actual_name, "error": str(e)}

                tool_ms = (time.time() - tool_start) * 1000

                result_str = json.dumps(
                    result.get("result", result.get("error", "")),
                    ensure_ascii=False,
                    default=str,
                )
                conversation.add_tool_result(tc.id, tc.name, result_str)

                yield ExecutionEvent("tool_result", result)

            # 체크포인트
            if self.state_store:
                state = {
                    "messages": conversation.to_messages(),
                    "system_prompt": conversation.system_prompt,
                }
                await self.state_store.checkpoint(ctx.session_id, state)

        yield ExecutionEvent("error", {"error": "최대 실행 횟수 초과"})


# ─── Phase 4: STREAM ───


class StreamAdapter(Protocol):
    """이벤트 변환 어댑터 프로토콜."""

    def convert(self, event: ExecutionEvent) -> StreamEvent: ...


class DefaultStreamAdapter:
    """기본 JSON 스트림 어댑터."""

    def convert(self, event: ExecutionEvent) -> StreamEvent:
        return StreamEvent(
            event=event.type,
            data=json.dumps(event.data, ensure_ascii=False, default=str),
        )


class StreamPhase:
    """전달 — 실행 이벤트를 클라이언트 포맷으로 변환."""

    def __init__(self, adapter: StreamAdapter | None = None):
        self.adapter = adapter or DefaultStreamAdapter()

    async def run(
        self, events: AsyncIterator[ExecutionEvent]
    ) -> AsyncIterator[StreamEvent]:
        async for event in events:
            yield self.adapter.convert(event)
        yield StreamEvent(event="end", data='{"message": "Stream finished"}')


# ─── Phase 5: PERSIST ───


class PersistPhase:
    """저장 — DB, Trace, 세션 상태 저장."""

    def __init__(
        self,
        trace_collector: Any | None = None,
        state_store: Any | None = None,
    ):
        self.trace = trace_collector
        self.state_store = state_store

    async def run(self, result: ExecutionResult) -> None:
        if self.trace and result.trace_id:
            self.trace.end_trace(result.trace_id)

        if self.state_store:
            await self.state_store.checkpoint(
                result.session_id,
                {"status": result.status, "output": result.output},
            )

        logger.debug("PersistPhase 완료: session=%s", result.session_id)
