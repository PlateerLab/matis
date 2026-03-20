"""그래프 검색 미들웨어 — GraphToolManager를 Agent 루프에 통합."""

from __future__ import annotations

import logging
from typing import Any

from mantis.middleware.base import BaseMiddleware, RunContext

logger = logging.getLogger(__name__)

# graph-tool-call은 선택 의존성
try:
    from mantis.search.graph_search import GraphToolManager
    _HAS_GRAPH = True
except ImportError:
    _HAS_GRAPH = False
    GraphToolManager = None  # type: ignore[assignment,misc]


class GraphSearchMiddleware(BaseMiddleware):
    """도구 수가 threshold 이상이면 자동으로 그래프 검색으로 필터링.

    LLM에 전달하는 도구 목록이 너무 많으면 성능이 떨어지므로,
    사용자 메시지를 기반으로 관련 도구만 필터링한다.

    사용법:
        manager = GraphToolManager(config)
        mw = GraphSearchMiddleware(manager=manager, threshold=15)
        runner.add_middleware(mw)
    """

    def __init__(
        self,
        manager: Any = None,
        *,
        threshold: int = 15,
        top_k: int = 10,
    ):
        if manager is not None:
            self._manager = manager
        elif _HAS_GRAPH:
            self._manager = GraphToolManager()
        else:
            self._manager = None

        self._threshold = threshold
        self._top_k = top_k

    @property
    def available(self) -> bool:
        """graph-tool-call이 사용 가능한지 여부."""
        return self._manager is not None

    async def on_before_llm(self, ctx: RunContext, tools: list[dict]) -> list[dict]:
        """도구 수가 threshold 이상이면 그래프 검색으로 필터링."""
        if not self.available or not self._manager:
            return tools

        if len(tools) < self._threshold:
            return tools

        query = ctx.last_user_message
        if not query:
            return tools

        try:
            # 검색으로 관련 도구 이름 추출
            retrieved = self._manager.retrieve(query, top_k=self._top_k)
            retrieved_names = {ts.name for ts in retrieved}

            # 원래 도구 목록에서 관련 도구만 필터링
            filtered = [
                t for t in tools
                if t.get("function", {}).get("name") in retrieved_names
            ]

            logger.info(
                "GraphSearchMiddleware: %d → %d개 도구 필터링 (쿼리: %s)",
                len(tools),
                len(filtered),
                query[:50],
            )

            # 필터링 결과가 없으면 원래 목록 반환
            return filtered if filtered else tools

        except Exception as e:
            logger.warning("GraphSearchMiddleware: 검색 실패 (원래 목록 유지): %s", e)
            return tools


class AutoCorrectMiddleware(BaseMiddleware):
    """도구 호출 자동 교정 미들웨어.

    LLM이 잘못된 도구 이름이나 파라미터를 생성했을 때
    GraphToolManager의 validate_call로 자동 교정한다.

    사용법:
        manager = GraphToolManager(config)
        mw = AutoCorrectMiddleware(manager=manager)
        runner.add_middleware(mw)
    """

    def __init__(self, manager: Any = None):
        if manager is not None:
            self._manager = manager
        elif _HAS_GRAPH:
            self._manager = GraphToolManager()
        else:
            self._manager = None

    @property
    def available(self) -> bool:
        """graph-tool-call이 사용 가능한지 여부."""
        return self._manager is not None

    async def on_before_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict
    ) -> tuple[str, dict, str | None]:
        """도구 호출 검증 및 자동 교정."""
        if not self.available or not self._manager:
            return tool_name, arguments, None

        try:
            result = self._manager.validate_call(tool_name, arguments)

            if not result.get("valid", True):
                errors = result.get("errors", [])
                if errors:
                    logger.warning(
                        "AutoCorrectMiddleware: 검증 실패 — tool=%s, errors=%s",
                        tool_name,
                        errors,
                    )

            # 이름 교정
            corrected_name = result.get("corrected_name")
            if corrected_name and corrected_name != tool_name:
                logger.info(
                    "AutoCorrectMiddleware: 이름 교정 '%s' → '%s'",
                    tool_name,
                    corrected_name,
                )
                tool_name = corrected_name

            # 파라미터 교정
            corrected_args = result.get("corrected_arguments")
            if corrected_args is not None:
                logger.info(
                    "AutoCorrectMiddleware: 파라미터 교정 — tool=%s",
                    tool_name,
                )
                arguments = corrected_args

            # 경고 로깅
            for warning in result.get("warnings", []):
                logger.warning(
                    "AutoCorrectMiddleware: %s — %s", tool_name, warning
                )

            return tool_name, arguments, None

        except Exception as e:
            logger.warning(
                "AutoCorrectMiddleware: 검증 실패 (원래 값 유지): %s", e
            )
            return tool_name, arguments, None
