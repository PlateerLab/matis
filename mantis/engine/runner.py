"""Agent Core — Think → Act → Observe 마스터 루프.

v3: 미들웨어 기반 아키텍처.
- LLMProvider Protocol 사용
- Middleware 체인으로 횡단 관심사 분리
- 레거시 파라미터 → 미들웨어 자동 변환 (하위 호환)
"""

from __future__ import annotations

import json
import logging
import uuid
import warnings
from typing import Any, AsyncIterator

from mantis.context.conversation import ConversationContext
from mantis.exceptions import LLMError, ToolExecutionError
from mantis.llm.protocol import LLMProvider, ModelResponse, ToolCall
from mantis.middleware.base import BaseMiddleware, Middleware, RunContext
from mantis.tools.registry import ToolRegistry

# 미들웨어 임포트 — 선택 의존성이므로 try/except
try:
    from mantis.middleware.trace import TraceMiddleware
except ImportError:
    TraceMiddleware = None  # type: ignore[assignment,misc]

try:
    from mantis.middleware.approval import ApprovalMiddleware
except ImportError:
    ApprovalMiddleware = None  # type: ignore[assignment,misc]

try:
    from mantis.middleware.graph_search import AutoCorrectMiddleware, GraphSearchMiddleware
except ImportError:
    AutoCorrectMiddleware = None  # type: ignore[assignment,misc]
    GraphSearchMiddleware = None  # type: ignore[assignment,misc]

try:
    from mantis.middleware.state import StateMiddleware
except ImportError:
    StateMiddleware = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50


class Agent:
    """단일 에이전트. 대화 기반으로 도구를 실행하는 마스터 루프.

    v3: 미들웨어 체인으로 횡단 관심사(트레이스, 승인, 검색, 상태)를 분리.
    """

    def __init__(
        self,
        name: str,
        model_client: LLMProvider,
        tool_registry: ToolRegistry,
        system_prompt: str = "",
        middlewares: list[Middleware | BaseMiddleware] | None = None,
        # Deprecated — 하위 호환용. middlewares 사용 권장.
        trace_collector: Any | None = None,
        state_store: Any | None = None,
        approval_patterns: list[str] | None = None,
        graph_tool_manager: Any | None = None,
    ):
        self.name = name
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.context = ConversationContext(system_prompt=system_prompt)
        self._session_id: str | None = None

        # 미들웨어 체인 구성
        self._middlewares: list[Middleware | BaseMiddleware] = list(middlewares or [])

        # 레거시 파라미터 → 미들웨어 자동 변환
        self._convert_legacy_params(
            trace_collector=trace_collector,
            state_store=state_store,
            approval_patterns=approval_patterns,
            graph_tool_manager=graph_tool_manager,
        )

    def _convert_legacy_params(
        self,
        trace_collector: Any | None,
        state_store: Any | None,
        approval_patterns: list[str] | None,
        graph_tool_manager: Any | None,
    ) -> None:
        """레거시 파라미터를 미들웨어로 변환. 각각 deprecation 경고 출력."""
        if trace_collector is not None:
            warnings.warn(
                "trace_collector 파라미터는 deprecated입니다. "
                "TraceMiddleware(collector=...) 사용을 권장합니다.",
                DeprecationWarning,
                stacklevel=3,
            )
            if TraceMiddleware is not None:
                self._middlewares.append(TraceMiddleware(collector=trace_collector))
            else:
                logger.warning("TraceMiddleware를 import할 수 없어 trace_collector 무시됨")

        if approval_patterns is not None:
            warnings.warn(
                "approval_patterns 파라미터는 deprecated입니다. "
                "ApprovalMiddleware(patterns=...) 사용을 권장합니다.",
                DeprecationWarning,
                stacklevel=3,
            )
            if ApprovalMiddleware is not None:
                self._middlewares.append(ApprovalMiddleware(patterns=approval_patterns))
            else:
                logger.warning("ApprovalMiddleware를 import할 수 없어 approval_patterns 무시됨")

        if graph_tool_manager is not None:
            warnings.warn(
                "graph_tool_manager 파라미터는 deprecated입니다. "
                "GraphSearchMiddleware / AutoCorrectMiddleware 사용을 권장합니다.",
                DeprecationWarning,
                stacklevel=3,
            )
            if GraphSearchMiddleware is not None:
                self._middlewares.append(GraphSearchMiddleware(manager=graph_tool_manager))
            else:
                logger.warning("GraphSearchMiddleware를 import할 수 없어 graph_tool_manager 무시됨")
            if AutoCorrectMiddleware is not None:
                self._middlewares.append(AutoCorrectMiddleware(manager=graph_tool_manager))
            else:
                logger.warning("AutoCorrectMiddleware를 import할 수 없어 graph_tool_manager 무시됨")

        if state_store is not None:
            warnings.warn(
                "state_store 파라미터는 deprecated입니다. "
                "StateMiddleware(store=...) 사용을 권장합니다.",
                DeprecationWarning,
                stacklevel=3,
            )
            if StateMiddleware is not None:
                self._middlewares.append(StateMiddleware(store=state_store))
            else:
                logger.warning("StateMiddleware를 import할 수 없어 state_store 무시됨")

    def add_middleware(self, mw: Middleware | BaseMiddleware) -> None:
        """미들웨어 추가."""
        self._middlewares.append(mw)

    # ─── 메인 실행 ───

    async def run(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> str:
        """사용자 입력을 받아 최종 텍스트 응답을 반환.

        Args:
            user_input: 사용자 메시지
            session_id: 세션 ID (없으면 자동 생성)

        Returns:
            LLM의 최종 텍스트 응답
        """
        session_id = session_id or str(uuid.uuid4())
        self._session_id = session_id

        ctx = RunContext(
            session_id=session_id,
            agent_name=self.name,
            last_user_message=user_input,
        )

        # on_start
        for mw in self._middlewares:
            await mw.on_start(ctx)

        self.context.add_user(user_input)
        final_text = ""

        try:
            for iteration in range(MAX_ITERATIONS):
                # 매 iteration마다 최신 도구 조회
                tools_schema = self.tool_registry.to_openai_tools(session_id=session_id)

                # on_before_llm — 도구 필터링/변환
                for mw in self._middlewares:
                    tools_schema = await mw.on_before_llm(ctx, tools_schema)

                # Think
                try:
                    response = await self.model_client.generate(
                        messages=self.context.to_messages(),
                        tools=tools_schema if tools_schema else None,
                    )
                except Exception as e:
                    raise LLMError(f"모델 호출 실패: {e}") from e

                # 종료 조건
                if not response.has_tool_calls:
                    final_text = response.text or ""
                    self.context.add_assistant(content=final_text)
                    break

                # assistant 메시지 기록 (tool_calls 포함)
                self.context.add_assistant(
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
                    name, args, block_reason = tc.name, tc.arguments, None

                    # on_before_tool — 승인, 자동 교정
                    for mw in self._middlewares:
                        name, args, block_reason = await mw.on_before_tool(ctx, name, args)
                        if block_reason:
                            break

                    if block_reason:
                        self.context.add_tool_result(
                            tc.id,
                            tc.name,
                            json.dumps(
                                {"blocked": True, "reason": block_reason},
                                ensure_ascii=False,
                            ),
                        )
                        continue

                    # 도구 실행
                    try:
                        result = await self.tool_registry.execute(
                            {"name": name, "arguments": args},
                            session_id=session_id,
                        )
                    except Exception as e:
                        logger.error("도구 '%s' 실행 실패: %s", name, e)
                        result = {"name": name, "error": str(e)}

                    # on_after_tool — 트레이스, 상태 체크포인트
                    for mw in self._middlewares:
                        await mw.on_after_tool(ctx, name, args, result)

                    # Observe
                    result_str = json.dumps(
                        result.get("result", result.get("error", "")),
                        ensure_ascii=False,
                        default=str,
                    )
                    self.context.add_tool_result(tc.id, tc.name, result_str)
            else:
                # MAX_ITERATIONS 초과
                logger.warning("Agent '%s' 최대 반복(%d) 초과", self.name, MAX_ITERATIONS)
                final_text = "[오류] 최대 실행 횟수를 초과했습니다."

        finally:
            # on_end — 항상 실행
            for mw in self._middlewares:
                await mw.on_end(ctx, final_text)

        return final_text

    # ─── 스트리밍 실행 ───

    async def run_stream(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """SSE 스트리밍용 — 각 단계를 이벤트로 yield.

        이벤트 타입:
            thinking, tool_call, tool_result, blocked, done, error
        """
        session_id = session_id or str(uuid.uuid4())
        self._session_id = session_id

        ctx = RunContext(
            session_id=session_id,
            agent_name=self.name,
            last_user_message=user_input,
        )

        # on_start
        for mw in self._middlewares:
            await mw.on_start(ctx)

        self.context.add_user(user_input)
        final_text = ""

        try:
            for iteration in range(MAX_ITERATIONS):
                # 매 iteration마다 최신 도구 조회
                tools_schema = self.tool_registry.to_openai_tools(session_id=session_id)

                # on_before_llm
                for mw in self._middlewares:
                    tools_schema = await mw.on_before_llm(ctx, tools_schema)

                yield {
                    "type": "thinking",
                    "data": {
                        "iteration": iteration + 1,
                        "tools_count": len(tools_schema) if tools_schema else 0,
                    },
                }

                # Think
                try:
                    response = await self.model_client.generate(
                        messages=self.context.to_messages(),
                        tools=tools_schema if tools_schema else None,
                    )
                except Exception as e:
                    yield {
                        "type": "error",
                        "data": {"error": str(e), "resumable": True},
                    }
                    return

                # 종료 조건
                if not response.has_tool_calls:
                    final_text = response.text or ""
                    self.context.add_assistant(content=final_text)
                    yield {"type": "done", "data": final_text}
                    return

                # assistant 메시지 기록 (tool_calls 포함)
                self.context.add_assistant(
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
                    name, args, block_reason = tc.name, tc.arguments, None

                    # on_before_tool
                    for mw in self._middlewares:
                        name, args, block_reason = await mw.on_before_tool(ctx, name, args)
                        if block_reason:
                            break

                    if block_reason:
                        self.context.add_tool_result(
                            tc.id,
                            tc.name,
                            json.dumps(
                                {"blocked": True, "reason": block_reason},
                                ensure_ascii=False,
                            ),
                        )
                        yield {
                            "type": "blocked",
                            "data": {
                                "tool": tc.name,
                                "reason": block_reason,
                            },
                        }
                        continue

                    yield {
                        "type": "tool_call",
                        "data": {"name": name, "arguments": args},
                    }

                    # 도구 실행
                    try:
                        result = await self.tool_registry.execute(
                            {"name": name, "arguments": args},
                            session_id=session_id,
                        )
                    except Exception as e:
                        logger.error("도구 '%s' 실행 실패: %s", name, e)
                        result = {"name": name, "error": str(e)}

                    # on_after_tool
                    for mw in self._middlewares:
                        await mw.on_after_tool(ctx, name, args, result)

                    # Observe
                    result_str = json.dumps(
                        result.get("result", result.get("error", "")),
                        ensure_ascii=False,
                        default=str,
                    )
                    self.context.add_tool_result(tc.id, tc.name, result_str)

                    yield {"type": "tool_result", "data": result}

            # MAX_ITERATIONS 초과
            yield {"type": "error", "data": "최대 실행 횟수 초과"}

        finally:
            # on_end — 항상 실행
            for mw in self._middlewares:
                await mw.on_end(ctx, final_text)
