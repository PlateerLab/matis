"""레지스트리 메타 도구 — Agent가 도구 생태계를 탐색할 수 있게 하는 도구."""

from __future__ import annotations

import logging
from typing import Any

from mantis.tools.decorator import tool, ToolSpec
from mantis.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def make_registry_tools(
    registry: ToolRegistry,
    graph_manager: Any | None = None,
) -> list[ToolSpec]:
    """ToolRegistry를 Agent가 조회/검색할 수 있는 도구로 변환.

    Args:
        registry: 도구 레지스트리.
        graph_manager: GraphToolManager 인스턴스 (선택, 있으면 시맨틱 검색).

    Returns:
        [search_tools ToolSpec, list_tools ToolSpec]
    """

    @tool(
        name="search_tools",
        description=(
            "등록된 도구 중 쿼리와 관련된 도구를 검색한다. "
            "어떤 도구를 써야 할지 모를 때, 또는 특정 기능의 도구가 "
            "있는지 확인할 때 사용."
        ),
        parameters={
            "query": {
                "type": "string",
                "description": "검색 쿼리 (자연어, 예: '슬랙 메시지 보내기')",
            },
            "top_k": {
                "type": "integer",
                "description": "반환할 최대 도구 수 (기본 5)",
                "optional": True,
            },
        },
    )
    async def search_tools(query: str, top_k: int = 5) -> dict:
        # graph-tool-call이 있으면 시맨틱 검색
        if graph_manager is not None:
            try:
                results = graph_manager.retrieve(query, top_k=top_k)
                return {
                    "count": len(results),
                    "tools": [
                        {"name": r.name, "description": r.description}
                        for r in results
                    ],
                }
            except Exception as e:
                logger.warning("graph 검색 실패, 키워드 검색으로 폴백: %s", e)

        # 폴백: 키워드 매칭
        query_lower = query.lower()
        all_tools = registry.list_tools()
        scored: list[tuple[float, ToolSpec]] = []

        for t in all_tools:
            score = 0.0
            name_lower = t.name.lower()
            desc_lower = t.description.lower()

            # 이름 완전 일치
            if query_lower == name_lower:
                score = 1.0
            # 이름에 쿼리 포함
            elif query_lower in name_lower:
                score = 0.8
            # 설명에 쿼리 포함
            elif query_lower in desc_lower:
                score = 0.5
            # 쿼리 단어 중 일부 매칭
            else:
                words = query_lower.split()
                matched = sum(
                    1 for w in words
                    if w in name_lower or w in desc_lower
                )
                if matched > 0:
                    score = 0.3 * (matched / len(words))

            if score > 0:
                scored.append((score, t))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        return {
            "count": len(top),
            "tools": [
                {"name": t.name, "description": t.description}
                for _, t in top
            ],
        }

    @tool(
        name="list_tools",
        description="현재 사용 가능한 모든 도구의 이름과 설명을 반환한다.",
        parameters={},
    )
    async def list_tools() -> dict:
        all_tools = registry.list_tools()
        return {
            "count": len(all_tools),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "source": registry.get_source(t.name) or "unknown",
                }
                for t in all_tools
            ],
        }

    return [search_tools._tool_spec, list_tools._tool_spec]
