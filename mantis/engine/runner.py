"""Agent Core — Think → Act → Observe 마스터 루프.

Phase 2 확장:
- State Store 연동 (멀티턴 컨텍스트 저장/복구)
- Human-in-the-Loop (승인 대기 → 재개)
- 실패 재개 (Checkpointer 패턴)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from mantis.context.conversation import ConversationContext
from mantis.llm.openai_provider import ModelClient, ModelResponse, ToolCall
from mantis.safety.approval import ApprovalManager, ApprovalStatus
from mantis.tools.registry import ToolRegistry
from mantis.trace.collector import TraceCollector, StepType

try:
    from mantis.search.graph_search import GraphToolManager
except ImportError:
    GraphToolManager = None  # type: ignore[assignment,misc]

try:
    from mantis.state.store import StateStore
except ImportError:
    StateStore = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50


class Agent:
    """단일 에이전트. 대화 기반으로 도구를 실행하는 마스터 루프.

    Phase 2 기능:
    - state_store: 세션 상태 자동 저장/복구 (실패 재개)
    - approval_manager: 위험 액션 승인 대기

    Phase 3 기능:
    - graph_tool_manager: 대량 도구 시 graph-tool-call 기반 동적 검색
    """

    def __init__(
        self,
        name: str,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        system_prompt: str = "",
        trace_collector: TraceCollector | None = None,
        state_store: StateStore | None = None,
        approval_patterns: list[str] | None = None,
        graph_tool_manager: GraphToolManager | None = None,
    ):
        self.name = name
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.context = ConversationContext(system_prompt=system_prompt)
        self.trace = trace_collector or TraceCollector()
        self.state_store = state_store
        self.approval = ApprovalManager(patterns=approval_patterns)
        self.graph_tool_manager = graph_tool_manager

    # ─── 세션 상태 저장/복구 ───

    async def _save_state(self, session_id: str) -> None:
        """현재 컨텍스트를 State Store에 저장."""
        if not self.state_store:
            return
        state = {
            "messages": self.context.to_messages(),
            "system_prompt": self.context.system_prompt,
        }
        await self.state_store.checkpoint(session_id, state)

    async def _restore_state(self, session_id: str) -> bool:
        """State Store에서 세션 복구. 복구 성공 시 True."""
        if not self.state_store:
            return False
        state = await self.state_store.resume(session_id)
        if not state:
            return False

        # 컨텍스트 복원
        self.context = ConversationContext(
            system_prompt=state.get("system_prompt", "")
        )
        for msg in state.get("messages", []):
            if msg["role"] == "system":
                continue  # system_prompt는 이미 설정됨
            elif msg["role"] == "user":
                self.context.add_user(msg["content"])
            elif msg["role"] == "assistant":
                self.context.add_assistant(
                    content=msg.get("content"),
                    tool_calls=msg.get("tool_calls"),
                )
            elif msg["role"] == "tool":
                self.context.add_tool_result(
                    tool_call_id=msg.get("tool_call_id", ""),
                    name=msg.get("name", ""),
                    content=msg.get("content", ""),
                )

        logger.info("세션 복구 완료: %s (%d 메시지)", session_id, len(self.context))
        return True

    # ─── 메인 실행 ───

    async def run(
        self,
        user_input: str,
        session_id: str | None = None,
        resume: bool = False,
    ) -> str:
        """사용자 입력을 받아 최종 텍스트 응답을 반환.

        Args:
            user_input: 사용자 메시지
            session_id: 세션 ID (없으면 자동 생성)
            resume: True이면 기존 세션에서 이어서 실행 (실패 재개)
        """
        session_id = session_id or str(uuid.uuid4())
        trace_id = self.trace.start_trace(session_id=session_id, agent_name=self.name)

        # 실패 재개: 기존 상태 복구
        if resume:
            restored = await self._restore_state(session_id)
            if restored:
                logger.info("세션 '%s' 재개", session_id)

        self.context.add_user(user_input)

        for iteration in range(MAX_ITERATIONS):
            # ── 도구 스키마 결정: graph-tool-call 또는 전체 목록 ──
            tools_schema = self._resolve_tools_schema(user_input)

            # ── Think ──
            think_start = time.time()
            try:
                response = await self.model_client.generate(
                    messages=self.context.to_messages(),
                    tools=tools_schema if tools_schema else None,
                )
            except Exception as e:
                # 모델 호출 실패 → 상태 저장 후 예외 전파 (나중에 재개 가능)
                logger.error("모델 호출 실패: %s", e)
                await self._save_state(session_id)
                self.trace.add_step(trace_id, StepType.ERROR, {"error": str(e)})
                self.trace.end_trace(trace_id)
                raise

            think_duration = (time.time() - think_start) * 1000

            self.trace.add_step(
                trace_id=trace_id,
                step_type=StepType.THINK,
                data={
                    "model": self.model_client.model,
                    "usage": response.usage,
                    "duration_ms": round(think_duration),
                    "graph_search_used": self._is_graph_search_active(),
                },
            )

            # ── 종료 조건 ──
            if not response.has_tool_calls:
                self.context.add_assistant(content=response.text)
                self.trace.add_step(trace_id, StepType.RESPONSE, {"text": response.text})
                self.trace.end_trace(trace_id)
                await self._save_state(session_id)
                return response.text or ""

            # ── Act: 도구 실행 ──
            self.context.add_assistant(
                content=response.text,
                tool_calls=[
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ],
            )

            for tc in response.tool_calls:
                # Human-in-the-Loop: 승인 필요 여부 확인
                if self.approval.requires_approval(tc.name, tc.arguments):
                    approval_req = await self.approval.request_approval(
                        session_id=session_id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                    )
                    # 상태 저장 (승인 대기 중 서버 재시작 대비)
                    await self._save_state(session_id)

                    self.trace.add_step(
                        trace_id=trace_id,
                        step_type=StepType.TOOL_CALL,
                        data={
                            "tool": tc.name,
                            "params": tc.arguments,
                            "approval_required": True,
                            "approval_id": approval_req.request_id,
                        },
                    )

                    # 승인 대기
                    approval_req = await self.approval.wait_for_approval(approval_req.request_id)

                    if approval_req.status != ApprovalStatus.APPROVED:
                        # 거절 또는 만료 → 결과에 반영
                        result_str = json.dumps(
                            {"rejected": True, "reason": approval_req.result or "승인 거절됨"},
                            ensure_ascii=False,
                        )
                        self.context.add_tool_result(tc.id, tc.name, result_str)
                        continue

                # 도구 이름 검증 + 자동 교정
                actual_name = tc.name
                actual_args = tc.arguments
                if self.graph_tool_manager:
                    validation = self.graph_tool_manager.validate_call(tc.name, tc.arguments)
                    if validation.get("corrected_name") and validation["corrected_name"] != tc.name:
                        logger.info("도구 이름 자동 교정: '%s' → '%s'", tc.name, validation["corrected_name"])
                        actual_name = validation["corrected_name"]
                    if validation.get("corrected_arguments"):
                        actual_args = validation["corrected_arguments"]

                # 도구 실행
                act_start = time.time()
                try:
                    result = await self.tool_registry.execute(
                        {"name": actual_name, "arguments": actual_args}
                    )
                except Exception as e:
                    # 도구 실행 실패 → 상태 저장 (실패 재개 지원)
                    logger.error("도구 '%s' 실행 실패: %s", actual_name, e)
                    await self._save_state(session_id)
                    result = {"name": actual_name, "error": str(e)}

                act_duration = (time.time() - act_start) * 1000

                result_str = json.dumps(
                    result.get("result", result.get("error", "")),
                    ensure_ascii=False, default=str,
                )
                self.context.add_tool_result(tc.id, tc.name, result_str)

                # graph-tool-call 호출 이력 기록
                if self.graph_tool_manager:
                    self.graph_tool_manager.record_call(tc.name)

                self.trace.add_step(
                    trace_id=trace_id,
                    step_type=StepType.TOOL_CALL,
                    data={
                        "tool": tc.name,
                        "params": tc.arguments,
                        "result": result,
                        "duration_ms": round(act_duration),
                    },
                )

            # 매 반복마다 상태 저장
            await self._save_state(session_id)

        logger.warning("Agent '%s' 최대 반복(%d) 초과", self.name, MAX_ITERATIONS)
        self.trace.end_trace(trace_id)
        return "[오류] 최대 실행 횟수를 초과했습니다."

    # ─── 도구 스키마 결정 ───

    def _resolve_tools_schema(self, query: str) -> list[dict]:
        """도구 수에 따라 전체 목록 또는 graph-tool-call 검색 결과 반환.

        도구 수가 임계값 미만이면 전체 도구를 LLM에 전달.
        임계값 이상이면 graph-tool-call로 쿼리 관련 도구만 검색하여 전달.
        """
        if self._is_graph_search_active():
            try:
                graph_tools = self.graph_tool_manager.retrieve_as_openai_tools(query)
                if graph_tools:
                    logger.info(
                        "graph-tool-call: 쿼리 '%s' → %d개 도구 검색됨",
                        query[:50],
                        len(graph_tools),
                    )
                    return graph_tools
                # 검색 결과 없으면 전체 도구 폴백
                logger.warning("graph-tool-call 검색 결과 없음, 전체 도구 사용")
            except Exception as e:
                logger.error("graph-tool-call 검색 실패: %s — 전체 도구 사용", e)

        return self.tool_registry.to_openai_tools()

    def _is_graph_search_active(self) -> bool:
        """graph-tool-call 검색 모드 활성 여부."""
        return (
            self.graph_tool_manager is not None
            and self.graph_tool_manager.should_use_search
        )

    # ─── 스트리밍 실행 ───

    async def run_stream(
        self,
        user_input: str,
        session_id: str | None = None,
        resume: bool = False,
    ) -> AsyncIterator[dict]:
        """SSE 스트리밍용 — 각 단계를 이벤트로 yield.

        이벤트 타입:
            thinking, tool_call, tool_result, approval_required, done, error
        """
        session_id = session_id or str(uuid.uuid4())
        trace_id = self.trace.start_trace(session_id=session_id, agent_name=self.name)

        if resume:
            restored = await self._restore_state(session_id)
            if restored:
                yield {"type": "resumed", "data": {"session_id": session_id}}

        self.context.add_user(user_input)

        for iteration in range(MAX_ITERATIONS):
            # ── 도구 스키마 결정: graph-tool-call 또는 전체 목록 ──
            tools_schema = self._resolve_tools_schema(user_input)

            yield {"type": "thinking", "data": {
                "iteration": iteration + 1,
                "graph_search_used": self._is_graph_search_active(),
                "tools_count": len(tools_schema) if tools_schema else 0,
            }}

            _think_start = time.time()
            try:
                response = await self.model_client.generate(
                    messages=self.context.to_messages(),
                    tools=tools_schema if tools_schema else None,
                )
            except Exception as e:
                self.trace.add_step(trace_id, StepType.ERROR, {"error": str(e)})
                await self._save_state(session_id)
                yield {"type": "error", "data": {"error": str(e), "resumable": True}}
                return
            _think_ms = (time.time() - _think_start) * 1000

            self.trace.add_step(trace_id, StepType.THINK, {
                "duration_ms": round(_think_ms),
                "model": self.model_client.model,
                "input_tokens": getattr(response, "input_tokens", None),
                "output_tokens": getattr(response, "output_tokens", None),
            })

            if not response.has_tool_calls:
                self.trace.add_step(trace_id, StepType.RESPONSE, {
                    "text": response.text or "",
                    "duration_ms": round(_think_ms),
                })
                self.context.add_assistant(content=response.text)
                self.trace.end_trace(trace_id)
                await self._save_state(session_id)
                yield {"type": "done", "data": response.text or ""}
                return

            self.context.add_assistant(
                content=response.text,
                tool_calls=[
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ],
            )

            for tc in response.tool_calls:
                # 승인 체크
                if self.approval.requires_approval(tc.name, tc.arguments):
                    approval_req = await self.approval.request_approval(
                        session_id=session_id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                    )
                    await self._save_state(session_id)
                    yield {
                        "type": "approval_required",
                        "data": approval_req.to_dict(),
                    }

                    approval_req = await self.approval.wait_for_approval(approval_req.request_id)
                    if approval_req.status != ApprovalStatus.APPROVED:
                        result_str = json.dumps(
                            {"rejected": True, "reason": approval_req.result or "승인 거절"},
                            ensure_ascii=False,
                        )
                        self.context.add_tool_result(tc.id, tc.name, result_str)
                        yield {"type": "approval_rejected", "data": {"request_id": approval_req.request_id}}
                        continue

                # 도구 이름 검증 + 자동 교정 (graph-tool-call validate)
                actual_name = tc.name
                actual_args = tc.arguments
                if self.graph_tool_manager:
                    validation = self.graph_tool_manager.validate_call(tc.name, tc.arguments)
                    if validation.get("corrected_name") and validation["corrected_name"] != tc.name:
                        logger.info(
                            "도구 이름 자동 교정: '%s' → '%s'",
                            tc.name, validation["corrected_name"],
                        )
                        actual_name = validation["corrected_name"]
                    if validation.get("corrected_arguments"):
                        actual_args = validation["corrected_arguments"]

                yield {"type": "tool_call", "data": {"name": actual_name, "arguments": actual_args}}

                _tool_start = time.time()
                try:
                    result = await self.tool_registry.execute(
                        {"name": actual_name, "arguments": actual_args}
                    )
                except Exception as e:
                    await self._save_state(session_id)
                    result = {"name": actual_name, "error": str(e)}
                _tool_ms = (time.time() - _tool_start) * 1000

                result_str = json.dumps(
                    result.get("result", result.get("error", "")),
                    ensure_ascii=False, default=str,
                )
                self.context.add_tool_result(tc.id, tc.name, result_str)

                self.trace.add_step(trace_id, StepType.TOOL_CALL, {
                    "tool": tc.name,
                    "params": tc.arguments,
                    "result": result.get("result", result.get("error")),
                    "success": "error" not in result,
                    "duration_ms": round(_tool_ms),
                })

                # graph-tool-call 호출 이력 기록
                if self.graph_tool_manager:
                    self.graph_tool_manager.record_call(tc.name)

                yield {"type": "tool_result", "data": result}

            await self._save_state(session_id)

        self.trace.end_trace(trace_id)
        yield {"type": "error", "data": "최대 실행 횟수 초과"}
