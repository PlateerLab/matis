"""v3 통합 테스트 — Everything is a Tool 아키텍처 검증.

Mock LLM으로 전체 파이프라인을 테스트:
- Agent + ToolRegistry + 미들웨어 + 도구 팩토리가 올바르게 조립되는지
- make_*_tools() 팩토리가 모두 동작하는지
- Agent 루프에서 도구가 실제로 호출되는지
- 워크플로우 생성/실행 도구가 동작하는지
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from mantis import Agent, tool, ToolSpec, ToolRegistry
from mantis.llm.protocol import LLMProvider, ModelResponse, ToolCall
from mantis.tools.meta import make_registry_tools
from mantis.generate.tool_generator import ToolGenerator, make_create_tool
from mantis.workflow import (
    WorkflowStore,
    WorkflowRunner,
    WorkflowGenerator,
    make_workflow_tools,
)
from mantis.middleware.base import RunContext
from mantis.middleware.trace import TraceMiddleware
from mantis.trace.collector import TraceCollector


# ─── Mock LLM ───


class MockLLM:
    """테스트용 Mock LLM. 미리 정한 응답 시퀀스를 순서대로 반환."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self._call_count = 0

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> ModelResponse:
        if self._call_count >= len(self._responses):
            return ModelResponse(text="[응답 소진]", tool_calls=[], usage={})
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


def _text(t: str) -> ModelResponse:
    return ModelResponse(text=t, tool_calls=[], usage={})


def _tool_calls(*calls: tuple[str, dict]) -> ModelResponse:
    return ModelResponse(
        text=None,
        tool_calls=[
            ToolCall(id=f"tc_{i}", name=n, arguments=a)
            for i, (n, a) in enumerate(calls)
        ],
        usage={},
    )


# ─── 1. 기본 조립 테스트 ───


@pytest.mark.asyncio
async def test_basic_agent_with_user_tool():
    """사용자 @tool + Agent 기본 동작."""

    @tool(
        name="add",
        description="두 수를 더한다",
        parameters={
            "a": {"type": "number", "description": "첫째"},
            "b": {"type": "number", "description": "둘째"},
        },
    )
    async def add(a: float, b: float) -> dict:
        return {"sum": a + b}

    registry = ToolRegistry()
    registry.register(add._tool_spec)

    llm = MockLLM([
        _tool_calls(("add", {"a": 3, "b": 5})),
        _text("3 + 5 = 8입니다."),
    ])

    agent = Agent(
        name="calc",
        model_client=llm,
        tool_registry=registry,
    )
    result = await agent.run("3 더하기 5는?")
    assert "8" in result


# ─── 2. Registry 메타 도구 ───


@pytest.mark.asyncio
async def test_registry_meta_tools():
    """make_registry_tools()로 search_tools, list_tools 등록 및 동작."""

    @tool(name="greet", description="인사한다", parameters={})
    async def greet() -> dict:
        return {"msg": "hello"}

    registry = ToolRegistry()
    registry.register(greet._tool_spec, source="builtin")

    # meta 도구 등록
    for spec in make_registry_tools(registry):
        registry.register(spec, source="builtin")

    # list_tools 도구가 등록되어 있는지
    assert registry.get("list_tools") is not None
    assert registry.get("search_tools") is not None

    # list_tools 직접 호출
    list_spec = registry.get("list_tools")
    result = await list_spec.execute()
    assert result["count"] >= 3  # greet + list_tools + search_tools

    # search_tools 직접 호출
    search_spec = registry.get("search_tools")
    result = await search_spec.execute(query="인사")
    assert result["count"] >= 1

    # Agent가 list_tools 도구를 호출하는 시나리오
    llm = MockLLM([
        _tool_calls(("list_tools", {})),
        _text("총 3개 도구가 있습니다."),
    ])
    agent = Agent(name="meta", model_client=llm, tool_registry=registry)
    result = await agent.run("어떤 도구 있어?")
    assert "3" in result


# ─── 3. Workflow 도구 ───


@pytest.mark.asyncio
async def test_workflow_create_and_run():
    """create_workflow → run_workflow 도구 연동."""

    @tool(
        name="double",
        description="숫자를 2배로",
        parameters={"n": {"type": "number", "description": "숫자"}},
    )
    async def double(n: float) -> dict:
        return {"result": n * 2}

    registry = ToolRegistry()
    registry.register(double._tool_spec)

    wf_store = WorkflowStore()
    wf_runner = WorkflowRunner(registry=registry)

    # WorkflowGenerator는 LLM이 필요하지만, create_workflow는 수동이라 generator 없이도 OK
    # generator를 None으로 두면 generate_workflow만 실패
    wf_generator_llm = MockLLM([])  # 사용 안 함
    wf_gen = WorkflowGenerator(llm=wf_generator_llm, registry=registry, store=wf_store)

    for spec in make_workflow_tools(wf_store, wf_runner, wf_gen):
        registry.register(spec, source="builtin")

    # 1. create_workflow 도구로 워크플로우 생성
    create_spec = registry.get("create_workflow")
    result = await create_spec.execute(
        name="double_pipeline",
        description="숫자를 2번 2배하는 파이프라인",
        steps=[
            {"id": "step1", "type": "tool", "tool": "double", "args": {"n": 5}},
            {"id": "step2", "type": "tool", "tool": "double", "args": {"n": 10}},
        ],
    )
    assert result["status"] == "success"
    assert result["name"] == "double_pipeline"

    # 2. list_workflows 도구
    list_spec = registry.get("list_workflows")
    result = await list_spec.execute()
    assert result["count"] == 1
    assert result["workflows"][0]["name"] == "double_pipeline"

    # 3. run_workflow 도구
    run_spec = registry.get("run_workflow")
    result = await run_spec.execute(
        workflow_name="double_pipeline", input_data={}
    )
    assert result["status"] == "success"
    # step1: double(5) = 10, step2: double(10) = 20
    assert result["result"]["step1"]["result"] == 10
    assert result["result"]["step2"]["result"] == 20


# ─── 4. Agent가 Workflow 도구를 호출하는 e2e ───


@pytest.mark.asyncio
async def test_agent_creates_and_runs_workflow():
    """Agent가 create_workflow → run_workflow 순서로 호출."""

    @tool(
        name="multiply",
        description="곱셈",
        parameters={
            "a": {"type": "number", "description": "a"},
            "b": {"type": "number", "description": "b"},
        },
    )
    async def multiply(a: float, b: float) -> dict:
        return {"product": a * b}

    registry = ToolRegistry()
    registry.register(multiply._tool_spec)

    wf_store = WorkflowStore()
    wf_runner = WorkflowRunner(registry=registry)
    wf_gen = WorkflowGenerator(
        llm=MockLLM([]), registry=registry, store=wf_store
    )
    for spec in make_workflow_tools(wf_store, wf_runner, wf_gen):
        registry.register(spec, source="builtin")

    # Agent 시퀀스:
    # 1. create_workflow 호출
    # 2. run_workflow 호출
    # 3. 최종 응답
    llm = MockLLM([
        _tool_calls((
            "create_workflow",
            {
                "name": "mul_pipeline",
                "description": "곱셈 파이프라인",
                "steps": [
                    {"id": "s1", "type": "tool", "tool": "multiply",
                     "args": {"a": 3, "b": 7}},
                ],
            },
        )),
        _tool_calls(("run_workflow", {
            "workflow_name": "mul_pipeline",
            "input_data": {},
        })),
        _text("3 × 7 = 21입니다."),
    ])

    agent = Agent(name="wf-agent", model_client=llm, tool_registry=registry)
    result = await agent.run("3 곱하기 7을 워크플로우로 처리해줘")
    assert "21" in result


# ─── 5. Middleware 체인 통합 ───


@pytest.mark.asyncio
async def test_middleware_chain():
    """TraceMiddleware가 Agent 루프와 올바르게 연동."""
    collector = TraceCollector()
    trace_mw = TraceMiddleware(collector=collector)

    @tool(name="ping", description="핑", parameters={})
    async def ping() -> dict:
        return {"pong": True}

    registry = ToolRegistry()
    registry.register(ping._tool_spec)

    llm = MockLLM([
        _tool_calls(("ping", {})),
        _text("pong!"),
    ])

    agent = Agent(
        name="traced-agent",
        model_client=llm,
        tool_registry=registry,
        middlewares=[trace_mw],
    )
    result = await agent.run("핑", session_id="trace-session")
    assert result == "pong!"

    # trace 기록 확인
    traces = collector.list_traces(session_id="trace-session")
    assert len(traces) == 1
    trace = traces[0]
    assert trace.agent_name == "traced-agent"
    assert len(trace.steps) >= 1  # tool_call + response


# ─── 6. 풀옵션 조립 ───


@pytest.mark.asyncio
async def test_full_v3_assembly():
    """v3 풀옵션 — 모든 도구 팩토리 + 미들웨어가 하나의 Agent에 조립."""

    # 사용자 도구
    @tool(
        name="lookup_order",
        description="주문 조회",
        parameters={"order_id": {"type": "string", "description": "주문 ID"}},
    )
    async def lookup_order(order_id: str) -> dict:
        return {"order_id": order_id, "status": "shipped"}

    # 엔진 조립
    registry = ToolRegistry()
    registry.register(lookup_order._tool_spec, source="builtin")

    # 레지스트리 메타 도구
    for spec in make_registry_tools(registry):
        registry.register(spec, source="builtin")

    # 워크플로우 도구
    wf_store = WorkflowStore()
    wf_runner = WorkflowRunner(registry=registry)
    wf_gen = WorkflowGenerator(
        llm=MockLLM([]), registry=registry, store=wf_store
    )
    for spec in make_workflow_tools(wf_store, wf_runner, wf_gen):
        registry.register(spec, source="builtin")

    # 미들웨어
    collector = TraceCollector()
    middlewares = [TraceMiddleware(collector=collector)]

    # Agent
    llm = MockLLM([
        _tool_calls(("search_tools", {"query": "주문"})),
        _tool_calls(("lookup_order", {"order_id": "ORD-123"})),
        _text("주문 ORD-123은 배송 중입니다."),
    ])

    agent = Agent(
        name="full-v3",
        model_client=llm,
        tool_registry=registry,
        system_prompt="주문 관련 질문에 답하는 에이전트.",
        middlewares=middlewares,
    )

    result = await agent.run("ORD-123 주문 어디있어?")
    assert "배송" in result

    # 등록된 도구 수 확인: lookup_order + search_tools + list_tools + 4 workflow tools = 7
    all_tools = registry.list_tools()
    assert len(all_tools) == 7

    # trace 기록 확인
    traces = collector.list_traces()
    assert len(traces) == 1


# ─── 7. make_create_tool 팩토리 ───


@pytest.mark.asyncio
async def test_make_create_tool_factory():
    """make_create_tool이 ToolSpec을 올바르게 생성."""
    mock_llm = MockLLM([])
    registry = ToolRegistry()
    generator = ToolGenerator(
        model_client=mock_llm,
        tool_registry=registry,
        tools_dir="/tmp/mantis_test_tools",
    )

    spec = make_create_tool(generator)
    assert spec.name == "create_tool"
    assert spec.description
    assert "description" in spec.parameters

    # 레지스트리에 등록 가능
    registry.register(spec, source="builtin")
    assert registry.get("create_tool") is not None


# ─── 8. LLMProvider Protocol 호환성 ───


@pytest.mark.asyncio
async def test_custom_llm_provider():
    """사용자 정의 LLMProvider가 Agent와 호환되는지."""

    class MyCustomLLM:
        """사용자가 직접 구현한 LLM."""

        async def generate(
            self,
            messages: list[dict],
            tools: list[dict] | None = None,
            temperature: float = 0.7,
        ) -> ModelResponse:
            return ModelResponse(
                text="커스텀 LLM 응답입니다.",
                tool_calls=[],
                usage={"total_tokens": 10},
            )

    registry = ToolRegistry()
    agent = Agent(
        name="custom-llm",
        model_client=MyCustomLLM(),
        tool_registry=registry,
    )
    result = await agent.run("테스트")
    assert "커스텀 LLM" in result


# ─── 9. 세션 격리 ───


@pytest.mark.asyncio
async def test_session_tool_isolation():
    """세션 A에서 등록한 도구가 세션 B에서 보이지 않는지."""

    @tool(name="global_tool", description="글로벌", parameters={})
    async def global_tool() -> dict:
        return {"scope": "global"}

    @tool(name="session_only", description="세션 전용", parameters={})
    async def session_only() -> dict:
        return {"scope": "session_a"}

    registry = ToolRegistry()
    registry.register(global_tool._tool_spec, source="builtin")
    registry.register(
        session_only._tool_spec, source="generated", session_id="session-a"
    )

    # session-a: 글로벌 + 세션 도구 둘 다 보임
    tools_a = registry.list_tools(session_id="session-a")
    names_a = {t.name for t in tools_a}
    assert "global_tool" in names_a
    assert "session_only" in names_a

    # session-b: 글로벌만 보임
    tools_b = registry.list_tools(session_id="session-b")
    names_b = {t.name for t in tools_b}
    assert "global_tool" in names_b
    assert "session_only" not in names_b

    # 세션 정리
    removed = registry.cleanup_session("session-a")
    assert removed == 1


# ─── 10. 워크플로우 조건 분기 ───


@pytest.mark.asyncio
async def test_workflow_condition_branching():
    """워크플로우 condition step이 올바르게 분기하는지."""

    @tool(
        name="check_score",
        description="점수 반환",
        parameters={},
    )
    async def check_score() -> dict:
        return {"score": 0.9, "label": "high"}

    @tool(
        name="report_high",
        description="높은 점수 리포트",
        parameters={},
    )
    async def report_high() -> dict:
        return {"report": "excellent"}

    @tool(
        name="report_low",
        description="낮은 점수 리포트",
        parameters={},
    )
    async def report_low() -> dict:
        return {"report": "needs improvement"}

    registry = ToolRegistry()
    for t in [check_score, report_high, report_low]:
        registry.register(t._tool_spec)

    wf_store = WorkflowStore()
    wf_runner = WorkflowRunner(registry=registry)

    from mantis.workflow.models import WorkflowDef, WorkflowStep

    workflow = WorkflowDef(
        name="score_check",
        description="점수 확인 후 분기",
        steps=[
            WorkflowStep(id="check", type="tool", tool="check_score"),
            WorkflowStep(
                id="decide",
                type="condition",
                condition="steps.check.score > 0.8",
                then_step="high",
                else_step="low",
            ),
            WorkflowStep(id="high", type="tool", tool="report_high"),
            WorkflowStep(id="low", type="tool", tool="report_low"),
        ],
    )
    wf_store.save("score_check", workflow)

    result = await wf_runner.run(workflow, {})
    # score 0.9 > 0.8 → then_step "high"로 분기
    assert result["check"]["score"] == 0.9
    assert result["decide"]["result"] is True
    assert result["high"]["report"] == "excellent"
