"""graph-tool-call 연동 테스트.

실제 graph-tool-call 패키지(0.13.1)를 사용한 테스트.
네트워크 호출 없이 로컬에서 동작한다.
"""

import pytest
from graph_tool_call import ToolGraph, ToolSchema

from mantis.search.graph_search import GraphToolManager, GraphToolConfig, GRAPH_SEARCH_THRESHOLD
from mantis.tools.registry import ToolRegistry
from mantis.tools.decorator import ToolSpec


# ─── Fixtures ───


@pytest.fixture
def sample_openai_tools() -> list[dict]:
    """OpenAI function-calling 형식 샘플 도구."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_users",
                "description": "사용자 목록 조회",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "결과 수"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_user_by_id",
                "description": "ID로 사용자 조회",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "사용자 ID"},
                    },
                    "required": ["user_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_user",
                "description": "새 사용자 생성",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "이름"},
                        "email": {"type": "string", "description": "이메일"},
                    },
                    "required": ["name", "email"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_user",
                "description": "사용자 삭제",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "사용자 ID"},
                    },
                    "required": ["user_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_orders",
                "description": "주문 내역 조회",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "사용자 ID"},
                        "status": {"type": "string", "description": "주문 상태"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_order",
                "description": "새 주문 생성",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "상품 ID"},
                        "quantity": {"type": "integer", "description": "수량"},
                    },
                    "required": ["product_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_products",
                "description": "상품 목록 조회",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "카테고리"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_product",
                "description": "상품 등록",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "상품명"},
                        "price": {"type": "number", "description": "가격"},
                    },
                    "required": ["name", "price"],
                },
            },
        },
    ]


@pytest.fixture
def manager() -> GraphToolManager:
    """기본 GraphToolManager."""
    return GraphToolManager(GraphToolConfig(max_results=5))


@pytest.fixture
def manager_with_tools(manager, sample_openai_tools) -> GraphToolManager:
    """도구가 등록된 GraphToolManager."""
    manager._tool_graph.add_tools(sample_openai_tools)
    return manager


@pytest.fixture
def registry_with_tools() -> ToolRegistry:
    """도구가 등록된 ToolRegistry."""
    registry = ToolRegistry()
    specs = [
        ToolSpec(
            name="query_customer",
            description="고객 정보 DB 조회",
            parameters={"customer_id": {"type": "string", "description": "고객 ID"}},
            fn=lambda customer_id: {"id": customer_id, "name": "홍길동"},
        ),
        ToolSpec(
            name="send_email",
            description="이메일 발송",
            parameters={
                "to": {"type": "string", "description": "수신자"},
                "subject": {"type": "string", "description": "제목"},
                "body": {"type": "string", "description": "본문"},
            },
            fn=lambda to, subject, body: {"sent": True},
        ),
        ToolSpec(
            name="search_products",
            description="상품 검색",
            parameters={"keyword": {"type": "string", "description": "검색어"}},
            fn=lambda keyword: {"results": []},
        ),
    ]
    for spec in specs:
        registry.register(spec)
    return registry


# ─── 기본 초기화 테스트 ───


def test_manager_init():
    """GraphToolManager 기본 초기화."""
    manager = GraphToolManager()
    assert manager.tool_count == 0
    # threshold=0이므로 도구 0개여도 항상 검색 모드 활성
    assert manager.should_use_search
    assert manager.config.max_results == 10


def test_manager_with_config():
    """커스텀 설정으로 초기화."""
    config = GraphToolConfig(
        max_results=20,
        search_mode="enhanced",
        auto_threshold=5,
    )
    manager = GraphToolManager(config)
    assert manager.config.max_results == 20
    assert manager.config.search_mode == "enhanced"
    assert manager.config.auto_threshold == 5


# ─── ToolGraph 직접 도구 등록 ───


def test_add_tools_to_graph(manager, sample_openai_tools):
    """ToolGraph에 도구 추가."""
    manager._tool_graph.add_tools(sample_openai_tools)
    assert manager.tool_count == 8


def test_tool_count(manager_with_tools):
    """도구 수 확인."""
    assert manager_with_tools.tool_count == 8


# ─── Registry에서 ingest ───


def test_ingest_from_registry(registry_with_tools):
    """ToolRegistry에서 도구 ingest."""
    manager = GraphToolManager()
    count = manager.ingest_from_registry(registry_with_tools)
    assert count == 3
    assert manager.tool_count == 3
    assert manager.has_tool("query_customer")
    assert manager.has_tool("send_email")
    assert manager.has_tool("search_products")


def test_ingest_from_registry_preserves_spec_map(registry_with_tools):
    """ingest 후 spec_map에 ToolSpec이 매핑됨."""
    manager = GraphToolManager()
    manager.ingest_from_registry(registry_with_tools)
    spec = manager.get_tool_spec("query_customer")
    assert spec is not None
    assert spec.name == "query_customer"


# ─── 검색 (retrieve) ───


def test_retrieve(manager_with_tools):
    """쿼리 기반 도구 검색."""
    results = manager_with_tools.retrieve("사용자 목록")
    assert len(results) > 0
    names = [r.name for r in results]
    # 사용자 관련 도구가 상위에 있어야 함
    assert any("user" in n for n in names)


def test_retrieve_with_scores(manager_with_tools):
    """점수 포함 검색."""
    results = manager_with_tools.retrieve_with_scores("주문 생성")
    assert len(results) > 0
    for r in results:
        assert hasattr(r, "score")
        assert hasattr(r, "keyword_score")
        assert hasattr(r, "graph_score")
        assert hasattr(r, "confidence")
        assert r.confidence in ("high", "medium", "low")


def test_retrieve_as_openai_tools(manager_with_tools):
    """OpenAI tools 형식 검색 결과."""
    tools = manager_with_tools.retrieve_as_openai_tools("상품 조회")
    assert len(tools) > 0
    for tool in tools:
        assert tool["type"] == "function"
        assert "function" in tool
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]


def test_retrieve_max_results(manager_with_tools):
    """max_results 제한."""
    results = manager_with_tools.retrieve("user", top_k=2)
    assert len(results) <= 2


# ─── 호출 이력 (history-aware retrieval) ───


def test_call_history(manager_with_tools):
    """호출 이력 기록 및 활용."""
    manager_with_tools.record_call("get_users")
    manager_with_tools.record_call("get_user_by_id")
    assert len(manager_with_tools._call_history) == 2

    # history가 검색에 영향을 줌 (에러 없이 실행)
    results = manager_with_tools.retrieve("삭제")
    assert len(results) > 0


def test_clear_history(manager_with_tools):
    """호출 이력 초기화."""
    manager_with_tools.record_call("get_users")
    manager_with_tools.clear_history()
    assert len(manager_with_tools._call_history) == 0


# ─── MCP 도구 ingest ───


def test_ingest_mcp_tools(manager):
    """MCP 도구 ingest."""
    mcp_tools = [
        {
            "name": "slack_send_message",
            "description": "Slack 메시지 전송",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "채널"},
                    "text": {"type": "string", "description": "메시지"},
                },
                "required": ["channel", "text"],
            },
        },
        {
            "name": "slack_list_channels",
            "description": "Slack 채널 목록 조회",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]
    count = manager.ingest_mcp_tools(mcp_tools, server_name="slack")
    assert count == 2
    assert manager.has_tool("slack_send_message")
    assert manager.has_tool("slack_list_channels")


# ─── auto_threshold ───


def test_should_use_search_always_active(manager_with_tools):
    """threshold=0이므로 도구 수와 무관하게 항상 검색 모드 활성."""
    assert GRAPH_SEARCH_THRESHOLD == 0
    assert manager_with_tools.should_use_search


def test_should_use_search_above_threshold(sample_openai_tools):
    """도구 수가 임계값 이상이면 검색 모드 활성."""
    config = GraphToolConfig(auto_threshold=5)  # 낮은 임계값
    manager = GraphToolManager(config)
    manager._tool_graph.add_tools(sample_openai_tools)
    assert manager.tool_count >= 5
    assert manager.should_use_search


# ─── 통계 ───


def test_get_stats(manager_with_tools):
    """그래프 통계."""
    stats = manager_with_tools.get_stats()
    assert stats["total_tools"] == 8
    assert "domains" in stats
    assert "search_mode" in stats
    assert stats["search_mode"] == "basic"


# ─── ToolSchema → OpenAI 변환 ───


def test_tool_schema_to_openai():
    """ToolSchema → OpenAI 형식 변환."""
    from graph_tool_call.core.tool import ToolParameter

    ts = ToolSchema(
        name="test_tool",
        description="테스트 도구",
        parameters=[
            ToolParameter(name="param1", type="string", description="파라미터1", required=True),
            ToolParameter(name="param2", type="integer", description="파라미터2", required=False),
        ],
    )
    result = GraphToolManager._tool_schema_to_openai(ts)
    assert result["type"] == "function"
    assert result["function"]["name"] == "test_tool"
    props = result["function"]["parameters"]["properties"]
    assert "param1" in props
    assert "param2" in props
    assert "param1" in result["function"]["parameters"]["required"]
    assert "param2" not in result["function"]["parameters"]["required"]


# ─── ToolSchema → ToolSpec 변환 ───


def test_tool_schema_to_tool_spec():
    """ToolSchema → ToolSpec 변환."""
    from graph_tool_call.core.tool import ToolParameter

    ts = ToolSchema(
        name="api_get_data",
        description="데이터 조회 API",
        parameters=[
            ToolParameter(name="id", type="string", description="ID", required=True),
        ],
    )
    spec = GraphToolManager._tool_schema_to_tool_spec(ts)
    assert spec is not None
    assert spec.name == "api_get_data"
    assert "[graph]" in spec.description
    assert "id" in spec.parameters
    assert spec.is_async is True


# ─── register_retrieved_tools ───


def test_register_retrieved_tools(manager_with_tools):
    """검색된 도구를 Registry에 등록."""
    registry = ToolRegistry()
    registered = manager_with_tools.register_retrieved_tools("사용자", registry, top_k=3)
    assert len(registered) > 0
    # 등록된 도구가 registry에 존재
    for name in registered:
        assert registry.get(name) is not None


# ─── GraphToolConfig 기본값 ───


def test_config_defaults():
    """GraphToolConfig 기본값."""
    config = GraphToolConfig()
    assert config.max_results == 10
    assert config.max_graph_depth == 2
    assert config.search_mode == "basic"
    assert config.auto_threshold == GRAPH_SEARCH_THRESHOLD
    assert config.embedding is None
    assert config.detect_dependencies is True
    assert config.min_confidence == 0.7
