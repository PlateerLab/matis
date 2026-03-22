"""Agent (engine/runner.py) — Think→Act→Observe 루프 테스트."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mantis.engine.runner import Agent, MAX_ITERATIONS
from mantis.llm.openai_provider import ModelClient, ModelResponse, ToolCall
from mantis.tools.decorator import ToolSpec
from mantis.tools.registry import ToolRegistry
from mantis.context.conversation import ConversationContext
from mantis.trace.collector import TraceCollector
from mantis.safety.approval import ApprovalManager, ApprovalStatus


# ─── Fixtures ───


def _make_text_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=[], usage={})


def _make_tool_response(calls: list[tuple[str, dict]]) -> ModelResponse:
    return ModelResponse(
        text=None,
        tool_calls=[
            ToolCall(id=f"tc_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        usage={},
    )


def _make_agent(
    responses: list[ModelResponse],
    tools: dict[str, dict] | None = None,
    approval_patterns: list[str] | None = None,
) -> Agent:
    """Mock ModelClient로 Agent 생성."""
    client = MagicMock(spec=ModelClient)
    client.model = "test-model"
    client.generate = AsyncMock(side_effect=responses)

    registry = ToolRegistry()
    if tools:
        for name, ret_val in tools.items():
            spec = ToolSpec(
                name=name,
                description=f"{name} tool",
                parameters={},
                fn=AsyncMock(return_value=ret_val),
                is_async=True,
            )
            registry.register(spec)

    return Agent(
        name="test-agent",
        model_client=client,
        tool_registry=registry,
        system_prompt="You are a test agent.",
        approval_patterns=approval_patterns,
    )


# ─── Basic Run ───


@pytest.mark.asyncio
async def test_run_text_only():
    """LLM이 텍스트만 반환하면 바로 종료."""
    agent = _make_agent([_make_text_response("Hello!")])
    result = await agent.run("Hi")
    assert result == "Hello!"


@pytest.mark.asyncio
async def test_run_with_tool_call():
    """도구 호출 후 최종 텍스트 응답."""
    agent = _make_agent(
        responses=[
            _make_tool_response([("echo", {"msg": "test"})]),
            _make_text_response("Done!"),
        ],
        tools={"echo": {"echo": "test"}},
    )
    result = await agent.run("Echo test")
    assert result == "Done!"
    assert agent.model_client.generate.call_count == 2


@pytest.mark.asyncio
async def test_run_multiple_tool_calls():
    """한 번의 LLM 응답에 여러 도구 호출."""
    agent = _make_agent(
        responses=[
            _make_tool_response([
                ("tool_a", {"x": 1}),
                ("tool_b", {"y": 2}),
            ]),
            _make_text_response("Both done"),
        ],
        tools={
            "tool_a": {"a": 1},
            "tool_b": {"b": 2},
        },
    )
    result = await agent.run("Run both")
    assert result == "Both done"


@pytest.mark.asyncio
async def test_run_multi_iteration():
    """여러 iteration에 걸친 도구 호출."""
    agent = _make_agent(
        responses=[
            _make_tool_response([("step1", {})]),
            _make_tool_response([("step2", {})]),
            _make_text_response("All steps complete"),
        ],
        tools={
            "step1": {"ok": True},
            "step2": {"ok": True},
        },
    )
    result = await agent.run("Multi-step")
    assert result == "All steps complete"
    assert agent.model_client.generate.call_count == 3


# ─── Streaming ───


@pytest.mark.asyncio
async def test_run_stream_text_only():
    """스트리밍: 텍스트만 반환."""
    agent = _make_agent([_make_text_response("Stream hello")])
    events = [e async for e in agent.run_stream("Hi")]

    types = [e["type"] for e in events]
    assert "thinking" in types
    assert "done" in types
    assert events[-1]["data"] == "Stream hello"


@pytest.mark.asyncio
async def test_run_stream_with_tool():
    """스트리밍: 도구 호출 이벤트 포함."""
    agent = _make_agent(
        responses=[
            _make_tool_response([("echo", {"msg": "hi"})]),
            _make_text_response("Done"),
        ],
        tools={"echo": {"echo": "hi"}},
    )
    events = [e async for e in agent.run_stream("Echo")]
    types = [e["type"] for e in events]

    assert "thinking" in types
    assert "tool_call" in types
    assert "tool_result" in types
    assert "done" in types


# ─── Error Handling ───


@pytest.mark.asyncio
async def test_run_llm_error():
    """LLM 호출 실패 시 예외 전파."""
    client = MagicMock(spec=ModelClient)
    client.model = "test"
    client.generate = AsyncMock(side_effect=RuntimeError("API down"))

    agent = Agent(
        name="err-agent",
        model_client=client,
        tool_registry=ToolRegistry(),
    )

    from mantis.exceptions import LLMError
    with pytest.raises(LLMError, match="API down"):
        await agent.run("fail")


@pytest.mark.asyncio
async def test_run_stream_llm_error():
    """스트리밍: LLM 실패 시 error 이벤트."""
    client = MagicMock(spec=ModelClient)
    client.model = "test"
    client.generate = AsyncMock(side_effect=RuntimeError("API down"))

    agent = Agent(
        name="err-agent",
        model_client=client,
        tool_registry=ToolRegistry(),
    )
    events = [e async for e in agent.run_stream("fail")]
    assert any(e["type"] == "error" for e in events)


@pytest.mark.asyncio
async def test_run_tool_execution_error():
    """도구 실행 실패 시 에러를 컨텍스트에 피드백하고 계속 진행."""
    failing_fn = AsyncMock(side_effect=RuntimeError("tool broke"))
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="broken",
        description="broken tool",
        parameters={},
        fn=failing_fn,
        is_async=True,
    ))

    client = MagicMock(spec=ModelClient)
    client.model = "test"
    client.generate = AsyncMock(side_effect=[
        _make_tool_response([("broken", {})]),
        _make_text_response("Recovered"),
    ])

    agent = Agent(
        name="test",
        model_client=client,
        tool_registry=registry,
    )
    result = await agent.run("Try broken tool")
    assert result == "Recovered"


# ─── Max Iterations ───


@pytest.mark.asyncio
async def test_run_max_iterations():
    """최대 반복 횟수 초과 시 오류 메시지 반환."""
    # 항상 도구를 호출하는 응답
    infinite_tool = _make_tool_response([("loop", {})])

    client = MagicMock(spec=ModelClient)
    client.model = "test"
    client.generate = AsyncMock(return_value=infinite_tool)

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="loop",
        description="loop tool",
        parameters={},
        fn=AsyncMock(return_value={"ok": True}),
        is_async=True,
    ))

    agent = Agent(
        name="loop-agent",
        model_client=client,
        tool_registry=registry,
    )
    result = await agent.run("infinite loop")
    assert "최대 실행 횟수" in result


# ─── Context Management ───


@pytest.mark.asyncio
async def test_conversation_context_preserved():
    """실행 후 대화 컨텍스트에 메시지가 올바르게 누적."""
    agent = _make_agent([_make_text_response("Hi there")])
    await agent.run("Hello")

    messages = agent.context.to_messages()
    roles = [m["role"] for m in messages]
    assert "system" in roles
    assert "user" in roles
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_session_id_auto_generated():
    """session_id를 안 주면 자동 생성."""
    agent = _make_agent([_make_text_response("ok")])
    await agent.run("test")
    # 예외 없이 실행되면 OK


@pytest.mark.asyncio
async def test_session_id_passed():
    """session_id를 직접 전달."""
    agent = _make_agent([_make_text_response("ok")])
    await agent.run("test", session_id="my-session-123")


# ─── Trace ───


@pytest.mark.asyncio
async def test_trace_recorded_via_middleware():
    """v3: TraceMiddleware를 통해 trace가 기록됨."""
    from mantis.trace.collector import TraceCollector
    from mantis.middleware.trace import TraceMiddleware

    collector = TraceCollector()
    trace_mw = TraceMiddleware(collector=collector)

    client = MagicMock(spec=ModelClient)
    client.model = "test"
    client.generate = AsyncMock(side_effect=[
        _make_tool_response([("echo", {})]),
        _make_text_response("done"),
    ])

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="echo", description="echo", parameters={},
        fn=AsyncMock(return_value={"ok": True}), is_async=True,
    ))

    agent = Agent(
        name="test-agent",
        model_client=client,
        tool_registry=registry,
        middlewares=[trace_mw],
    )
    await agent.run("trace test")

    traces = collector.list_traces()
    assert len(traces) == 1
    trace = traces[0]
    assert len(trace.steps) > 0


# ─── Approval ───


@pytest.mark.asyncio
async def test_approval_blocks_tool_via_middleware():
    """v3: ApprovalMiddleware를 통해 도구가 차단됨."""
    from mantis.middleware.approval import ApprovalMiddleware
    from mantis.safety.approval import ApprovalManager

    import asyncio

    manager = ApprovalManager(patterns=["dangerous"])
    approval_mw = ApprovalMiddleware(patterns=["dangerous"], manager=manager)

    client = MagicMock(spec=ModelClient)
    client.model = "test"
    client.generate = AsyncMock(side_effect=[
        _make_tool_response([("dangerous", {"action": "delete"})]),
        _make_text_response("Skipped dangerous action"),
    ])

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="dangerous", description="dangerous", parameters={},
        fn=AsyncMock(return_value={"deleted": True}), is_async=True,
    ))

    agent = Agent(
        name="test-agent",
        model_client=client,
        tool_registry=registry,
        middlewares=[approval_mw],
    )

    async def reject_soon():
        await asyncio.sleep(0.05)
        pending = manager.list_pending()
        if pending:
            manager.reject(pending[0].request_id, "Not allowed")

    asyncio.create_task(reject_soon())
    result = await agent.run("Do dangerous thing")
    assert result == "Skipped dangerous action"
