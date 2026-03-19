"""graph-tool-call 연동 — 검색 기반 동적 도구 로딩.

graph-tool-call 패키지(0.13.1)의 ToolGraph를 래핑하여
대량의 도구를 그래프 구조로 관리하고, 쿼리 기반 검색으로
관련 도구만 동적 로딩한다.

도구 소스 3가지:
- @tool 데코레이터 (Python 함수)
- MCP 서버 도구
- OpenAPI 스펙
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

try:
    from graph_tool_call import ToolGraph, ToolSchema, RetrievalResult
    from graph_tool_call.assist.validator import validate_tool_call
except ImportError as _exc:
    raise ImportError(
        "graph-tool-call 패키지가 필요합니다: pip install mantis[search]"
    ) from _exc

from mantis.tools.decorator import ToolSpec
from mantis.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 도구 수가 이 값 이상이면 자동으로 graph-tool-call 검색 모드 활성화
# 0으로 설정하면 항상 graph search 사용
GRAPH_SEARCH_THRESHOLD = 0


@dataclass
class GraphToolConfig:
    """graph-tool-call 설정."""

    # 검색 결과 상한
    max_results: int = 10
    # 그래프 탐색 깊이
    max_graph_depth: int = 2
    # 검색 모드: basic, enhanced, full
    search_mode: str = "basic"
    # 자동 검색 활성화 임계값 (도구 수)
    auto_threshold: int = GRAPH_SEARCH_THRESHOLD
    # 임베딩 활성화 여부 (모델명 또는 None)
    embedding: str | None = None
    # OpenAPI 소스 URL 목록
    openapi_sources: list[dict[str, Any]] = field(default_factory=list)
    # 의존성 자동 감지
    detect_dependencies: bool = True
    # 의존성 감지 최소 신뢰도
    min_confidence: float = 0.7


class GraphToolManager:
    """graph-tool-call 패키지 래퍼.

    ToolGraph 인스턴스를 관리하고, @tool 도구 / MCP 도구 / OpenAPI 소스를
    모두 하나의 그래프로 통합한다.

    사용법:
        manager = GraphToolManager(config)
        manager.ingest_from_registry(registry)    # @tool 도구 로드
        manager.ingest_openapi("http://api/spec")  # OpenAPI 추가
        tools = manager.retrieve("주문 조회")       # 검색
    """

    def __init__(self, config: GraphToolConfig | None = None):
        self.config = config or GraphToolConfig()
        self._tool_graph = ToolGraph()
        # ToolSchema.name → ToolSpec 매핑 (실행용)
        self._spec_map: dict[str, ToolSpec] = {}
        # 사용 이력 (history-aware retrieval)
        self._call_history: list[str] = []

    @property
    def tool_graph(self) -> ToolGraph:
        """내부 ToolGraph 인스턴스 접근."""
        return self._tool_graph

    @property
    def tool_count(self) -> int:
        """그래프에 등록된 도구 수."""
        return len(self._tool_graph.tools)

    @property
    def should_use_search(self) -> bool:
        """도구 수 기반 자동 검색 모드 판단."""
        return self.tool_count >= self.config.auto_threshold

    # ─── Ingest: @tool 데코레이터 도구 ───

    def ingest_from_registry(self, registry: ToolRegistry) -> int:
        """ToolRegistry의 모든 도구를 ToolGraph에 등록.

        @tool 데코레이터로 등록된 도구를 graph-tool-call의
        ToolSchema로 변환하여 그래프에 추가한다.
        등록 후 auto_organize()로 자동 분류한다.

        Returns:
            등록된 도구 수.
        """
        tools_added = 0
        for spec in registry.list_tools():
            try:
                # ToolSpec → OpenAI tool dict → ToolGraph.add_tool
                openai_schema = spec.to_openai_schema()
                self._tool_graph.add_tool(openai_schema)
                self._spec_map[spec.name] = spec
                tools_added += 1
            except Exception as e:
                logger.warning("도구 '%s' ingest 실패: %s", spec.name, e)

        if tools_added > 0:
            logger.info(
                "graph-tool-call: Registry에서 %d개 도구 ingest 완료 (총 %d개)",
                tools_added,
                self.tool_count,
            )
            # 자동 분류: 태그/도메인/카테고리 생성 + 의존성 관계 구축
            self._auto_organize()

        return tools_added

    def _auto_organize(self) -> None:
        """도구 자동 분류 — 태그 기반 카테고리/도메인 + 의존성 관계 구축."""
        try:
            self._tool_graph.auto_organize()
            logger.info(
                "graph-tool-call: auto_organize 완료 — 도구 %d개 분류됨",
                self.tool_count,
            )
        except Exception as e:
            logger.warning("graph-tool-call: auto_organize 실패 (무시): %s", e)

    # ─── Ingest: OpenAPI ───

    def ingest_openapi(
        self,
        source: str | dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        required_only: bool = False,
        skip_deprecated: bool = True,
    ) -> int:
        """OpenAPI 스펙을 ToolGraph에 등록.

        Args:
            source: OpenAPI 스펙 URL, 파일 경로, 또는 dict.
            headers: 인증 헤더 (URL 소스 시 사용).
            required_only: 필수 파라미터만 포함.
            skip_deprecated: deprecated 제외.

        Returns:
            등록된 도구 수.
        """
        try:
            schemas = self._tool_graph.ingest_openapi(
                source,
                required_only=required_only,
                skip_deprecated=skip_deprecated,
                detect_dependencies=self.config.detect_dependencies,
                min_confidence=self.config.min_confidence,
            )
            logger.info(
                "graph-tool-call: OpenAPI에서 %d개 도구 ingest 완료",
                len(schemas),
            )
            return len(schemas)
        except Exception as e:
            logger.error("OpenAPI ingest 실패: %s", e)
            return 0

    # ─── Ingest: OpenAPI from URL (ToolGraph.from_url) ───

    @classmethod
    def from_url(
        cls,
        url: str,
        config: GraphToolConfig | None = None,
        **kwargs: Any,
    ) -> GraphToolManager:
        """OpenAPI URL에서 ToolGraph를 생성하여 매니저 초기화.

        Args:
            url: OpenAPI/Swagger URL.
            config: 설정.
            **kwargs: ToolGraph.from_url에 전달할 추가 옵션.

        Returns:
            초기화된 GraphToolManager.
        """
        config = config or GraphToolConfig()
        manager = cls(config)
        manager._tool_graph = ToolGraph.from_url(
            url,
            detect_dependencies=config.detect_dependencies,
            min_confidence=config.min_confidence,
            **kwargs,
        )
        logger.info(
            "graph-tool-call: URL '%s'에서 %d개 도구 로드",
            url,
            manager.tool_count,
        )
        return manager

    # ─── Ingest: MCP 도구 ───

    def ingest_mcp_tools(
        self,
        tools: list[dict[str, Any]],
        server_name: str | None = None,
    ) -> int:
        """MCP 도구 목록을 ToolGraph에 등록.

        Args:
            tools: MCP 도구 딕셔너리 목록 (name, inputSchema, annotations).
            server_name: MCP 서버 이름 (태깅용).

        Returns:
            등록된 도구 수.
        """
        try:
            schemas = self._tool_graph.ingest_mcp_tools(
                tools,
                server_name=server_name,
                detect_dependencies=self.config.detect_dependencies,
                min_confidence=self.config.min_confidence,
            )
            logger.info(
                "graph-tool-call: MCP '%s'에서 %d개 도구 ingest 완료",
                server_name or "unknown",
                len(schemas),
            )
            return len(schemas)
        except Exception as e:
            logger.error("MCP 도구 ingest 실패: %s", e)
            return 0

    # ─── Ingest: MCP 서버 직접 연결 ───

    def ingest_mcp_server(self, server_url: str, server_name: str | None = None) -> int:
        """MCP 서버에서 도구를 가져와 등록.

        Args:
            server_url: MCP 서버 URL.
            server_name: 서버 이름.

        Returns:
            등록된 도구 수.
        """
        try:
            schemas = self._tool_graph.ingest_mcp_server(
                server_url,
                server_name=server_name,
                detect_dependencies=self.config.detect_dependencies,
                min_confidence=self.config.min_confidence,
            )
            logger.info(
                "graph-tool-call: MCP 서버 '%s'에서 %d개 도구 ingest",
                server_url,
                len(schemas),
            )
            return len(schemas)
        except Exception as e:
            logger.error("MCP 서버 ingest 실패: %s — %s", server_url, e)
            return 0

    # ─── Ingest: Python 함수 ───

    def ingest_functions(self, fns: list[Any]) -> int:
        """Python callable 목록을 ToolGraph에 등록.

        Args:
            fns: Python 함수 목록.

        Returns:
            등록된 도구 수.
        """
        try:
            schemas = self._tool_graph.ingest_functions(
                fns,
                detect_dependencies=self.config.detect_dependencies,
                min_confidence=self.config.min_confidence,
            )
            logger.info(
                "graph-tool-call: Python 함수 %d개 ingest 완료",
                len(schemas),
            )
            return len(schemas)
        except Exception as e:
            logger.error("Python 함수 ingest 실패: %s", e)
            return 0

    # ─── 임베딩 설정 ───

    def enable_embedding(self, model: str | None = None) -> None:
        """임베딩 기반 하이브리드 검색 활성화.

        Args:
            model: 임베딩 모델명. None이면 config에서 가져옴.
                예: "sentence-transformers/all-MiniLM-L6-v2",
                    "openai/text-embedding-3-large"
        """
        model = model or self.config.embedding
        if not model:
            model = "sentence-transformers/all-MiniLM-L6-v2"
        self._tool_graph.enable_embedding(model)
        logger.info("graph-tool-call: 임베딩 활성화 — %s", model)

    # ─── 검색 ───

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        history: list[str] | None = None,
    ) -> list[ToolSchema]:
        """쿼리와 관련된 도구 검색.

        BM25 + 그래프 탐색 + (선택) 임베딩 하이브리드 검색.

        Args:
            query: 자연어 검색 쿼리.
            top_k: 반환할 최대 도구 수. None이면 config 값 사용.
            history: 이전 호출 도구 이력 (history-aware retrieval).

        Returns:
            관련 도구 ToolSchema 목록 (점수 내림차순).
        """
        top_k = top_k or self.config.max_results
        effective_history = history or self._call_history

        return self._tool_graph.retrieve(
            query,
            top_k=top_k,
            max_graph_depth=self.config.max_graph_depth,
            mode=self.config.search_mode,
            history=effective_history if effective_history else None,
        )

    def retrieve_with_scores(
        self,
        query: str,
        top_k: int | None = None,
        history: list[str] | None = None,
    ) -> list[RetrievalResult]:
        """점수 포함 검색 결과 반환.

        Args:
            query: 자연어 검색 쿼리.
            top_k: 반환할 최대 도구 수.
            history: 이전 호출 도구 이력.

        Returns:
            RetrievalResult 목록 (score, keyword_score, graph_score 등 포함).
        """
        top_k = top_k or self.config.max_results
        effective_history = history or self._call_history

        return self._tool_graph.retrieve_with_scores(
            query,
            top_k=top_k,
            max_graph_depth=self.config.max_graph_depth,
            mode=self.config.search_mode,
            history=effective_history if effective_history else None,
        )

    def retrieve_as_openai_tools(
        self,
        query: str,
        top_k: int | None = None,
        history: list[str] | None = None,
    ) -> list[dict]:
        """검색 결과를 OpenAI tools 형식으로 반환.

        Agent의 마스터 루프에서 LLM에 전달할 때 사용.

        Args:
            query: 자연어 검색 쿼리.
            top_k: 반환할 최대 도구 수.
            history: 이전 호출 도구 이력.

        Returns:
            OpenAI function-calling 호환 tool dict 목록.
        """
        schemas = self.retrieve(query, top_k=top_k, history=history)
        return [self._tool_schema_to_openai(ts) for ts in schemas]

    # ─── 도구 실행 ───

    def record_call(self, tool_name: str) -> None:
        """도구 호출 이력 기록 (history-aware retrieval용)."""
        self._call_history.append(tool_name)

    def clear_history(self) -> None:
        """호출 이력 초기화."""
        self._call_history.clear()

    def get_tool_spec(self, tool_name: str) -> ToolSpec | None:
        """ToolGraph 도구명으로 실행 가능한 ToolSpec 조회.

        @tool로 등록된 도구만 직접 실행 가능.
        OpenAPI/MCP 도구는 별도 실행 경로 필요.
        """
        return self._spec_map.get(tool_name)

    def has_tool(self, tool_name: str) -> bool:
        """도구 존재 여부 확인."""
        return tool_name in self._tool_graph.tools

    # ─── Registry 동기화 ───

    def register_retrieved_tools(
        self,
        query: str,
        registry: ToolRegistry,
        top_k: int | None = None,
    ) -> list[str]:
        """검색된 도구를 ToolRegistry에 동적 등록.

        OpenAPI/MCP 소스에서 검색된 도구를 실행 가능하게 등록한다.

        Returns:
            등록된 도구 이름 목록.
        """
        schemas = self.retrieve(query, top_k=top_k)
        registered = []

        for ts in schemas:
            # 이미 registry에 있으면 스킵
            if registry.get(ts.name):
                registered.append(ts.name)
                continue

            # _spec_map에 있으면 이미 @tool로 등록된 도구
            if ts.name in self._spec_map:
                registered.append(ts.name)
                continue

            # OpenAPI/MCP 도구 → HTTP 실행 도구로 변환 등록
            spec = self._tool_schema_to_tool_spec(ts)
            if spec:
                registry.register(spec)
                registered.append(ts.name)

        logger.info(
            "graph-tool-call: 쿼리 '%s' → %d개 도구 등록",
            query,
            len(registered),
        )
        return registered

    # ─── 변환 헬퍼 ───

    @staticmethod
    def _tool_schema_to_openai(ts: ToolSchema) -> dict:
        """ToolSchema → OpenAI function-calling 형식 변환."""
        properties = {}
        required = []

        for param in ts.parameters:
            prop: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": ts.name,
                "description": ts.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    @staticmethod
    def _tool_schema_to_tool_spec(ts: ToolSchema) -> ToolSpec | None:
        """ToolSchema → ToolSpec 변환 (OpenAPI/MCP 도구용).

        HTTP 실행이 필요한 도구를 위한 플레이스홀더 함수 생성.
        실제 실행은 ToolGraph.execute()를 통해 처리.
        """
        parameters = {}
        for param in ts.parameters:
            parameters[param.name] = {
                "type": param.type,
                "description": param.description,
            }
            if not param.required:
                parameters[param.name]["optional"] = True

        async def _placeholder(**kwargs: Any) -> dict:
            return {
                "error": f"도구 '{ts.name}'은 graph-tool-call execute를 통해 실행해야 합니다.",
                "tool": ts.name,
                "arguments": kwargs,
            }

        return ToolSpec(
            name=ts.name,
            description=f"[graph] {ts.description}",
            parameters=parameters,
            fn=_placeholder,
            is_async=True,
        )

    # ─── 도구 호출 검증 ───

    def validate_call(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """LLM이 요청한 도구 호출을 검증하고 자동 교정.

        - 도구 이름 오타 → fuzzy matching으로 올바른 이름 제안
        - 파라미터 누락/초과 → 경고
        - destructive 도구 → 경고 플래그

        Returns:
            {"valid": bool, "corrected_name": str|None, "warnings": list, "errors": list}
        """
        tool_call = {"name": tool_name, "arguments": arguments}
        tools_list = list(self._tool_graph.tools.values())

        try:
            result = validate_tool_call(tool_call, tools_list)
            return {
                "valid": result.valid,
                "corrected_name": result.corrected_name if hasattr(result, "corrected_name") else None,
                "corrected_arguments": result.corrected_arguments if hasattr(result, "corrected_arguments") else None,
                "warnings": [str(w) for w in result.warnings] if hasattr(result, "warnings") else [],
                "errors": [str(e) for e in result.errors] if hasattr(result, "errors") else [],
            }
        except Exception as e:
            logger.warning("validate_tool_call 실패: %s", e)
            return {"valid": True, "corrected_name": None, "warnings": [], "errors": []}

    # ─── 분석/관리 ───

    def analyze(self) -> dict[str, Any]:
        """그래프 품질 분석 리포트.

        중복 도구, 충돌 도구, 고아 도구, 카테고리 분포 등을 반환.
        """
        try:
            report = self._tool_graph.analyze()
            return {
                "tool_count": report.tool_count,
                "node_count": report.node_count,
                "edge_count": report.edge_count,
                "duplicate_count": report.duplicate_count,
                "conflict_count": report.conflict_count,
                "orphan_tool_count": report.orphan_tool_count,
                "category_count": report.category_count,
                "duplicates": [
                    {"tool_a": d.tool_a, "tool_b": d.tool_b, "score": d.score, "stage": d.stage}
                    for d in (report.duplicates or [])
                ],
                "categories": report.categories if hasattr(report, "categories") else [],
            }
        except Exception as e:
            logger.warning("graph analyze 실패: %s", e)
            return {"error": str(e)}

    def find_duplicates(self, threshold: float = 0.85) -> list[dict]:
        """중복 도구 탐지.

        5단계 파이프라인: 해시 → 퍼지이름 → 파라미터Jaccard → 임베딩 → 시맨틱
        """
        try:
            pairs = self._tool_graph.find_duplicates(threshold=threshold)
            return [
                {"tool_a": p.tool_a, "tool_b": p.tool_b, "score": p.score, "stage": p.stage}
                for p in pairs
            ]
        except Exception as e:
            logger.warning("find_duplicates 실패: %s", e)
            return []

    def get_stats(self) -> dict[str, Any]:
        """그래프 통계 반환."""
        tools = self._tool_graph.tools
        domains: set[str] = set()
        categories: set[str] = set()
        for ts in tools.values():
            if ts.domain:
                domains.add(ts.domain)
            if hasattr(ts, "tags") and ts.tags:
                categories.update(ts.tags)

        return {
            "total_tools": len(tools),
            "domains": sorted(domains),
            "domain_count": len(domains),
            "categories": sorted(categories),
            "category_count": len(categories),
            "spec_map_count": len(self._spec_map),
            "call_history_length": len(self._call_history),
            "search_mode": self.config.search_mode,
            "auto_threshold": self.config.auto_threshold,
            "should_use_search": self.should_use_search,
        }
