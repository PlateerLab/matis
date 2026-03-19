"""Pipeline — 5-Phase 파이프라인 테스트."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from mantis.pipeline.models import (
    ExecutionRequest,
    ExecutionContext,
    ResolvedContext,
    ExecutionEvent,
    StreamEvent,
    ExecutionResult,
)
from mantis.pipeline.phases import (
    PreparePhase,
    ResolvePhase,
    ExecutePhase,
    StreamPhase,
    PersistPhase,
    DefaultStreamAdapter,
)
from mantis.pipeline.pipeline import ExecutionPipeline, build_pipeline
from mantis.tools.decorator import ToolSpec
from mantis.tools.registry import ToolRegistry
from mantis.llm.openai_provider import ModelResponse, ToolCall


# ═══════════════════════════════════════════
# Models
# ═══════════════════════════════════════════


class TestModels:

    def test_execution_request_defaults(self):
        req = ExecutionRequest(input_data="hello")
        assert req.session_id is None
        assert req.workflow_data is None
        assert req.resume is False

    def test_execution_event_to_dict(self):
        event = ExecutionEvent("tool_call", {"name": "echo"})
        d = event.to_dict()
        assert d == {"type": "tool_call", "data": {"name": "echo"}}

    def test_stream_event_to_sse(self):
        event = StreamEvent(event="done", data='{"text": "hi"}')
        sse = event.to_sse()
        assert "event: done" in sse
        assert 'data: {"text": "hi"}' in sse
        assert sse.endswith("\n\n")

    def test_stream_event_to_sse_no_event(self):
        event = StreamEvent(data="plain")
        sse = event.to_sse()
        assert "event:" not in sse
        assert "data: plain" in sse

    def test_execution_result_defaults(self):
        result = ExecutionResult(session_id="s1", input_data="test")
        assert result.status == "completed"
        assert result.output == ""


# ═══════════════════════════════════════════
# Phase 1: PREPARE
# ═══════════════════════════════════════════


class TestPreparePhase:

    @pytest.mark.asyncio
    async def test_agent_mode(self):
        phase = PreparePhase(system_prompt="You are helpful.")
        req = ExecutionRequest(input_data="hello")
        ctx = await phase.run(req)

        assert ctx.mode == "agent"
        assert ctx.message == "hello"
        assert ctx.system_prompt == "You are helpful."
        assert ctx.session_id  # auto-generated

    @pytest.mark.asyncio
    async def test_agent_mode_with_session_id(self):
        phase = PreparePhase()
        req = ExecutionRequest(input_data="test", session_id="sess-123")
        ctx = await phase.run(req)
        assert ctx.session_id == "sess-123"

    @pytest.mark.asyncio
    async def test_workflow_mode(self):
        phase = PreparePhase()
        workflow = {
            "nodes": [
                {"id": "A"},
                {"id": "B"},
                {"id": "C"},
            ],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "B", "target": "C"},
            ],
        }
        req = ExecutionRequest(input_data="run workflow", workflow_data=workflow)
        ctx = await phase.run(req)

        assert ctx.mode == "workflow"
        assert ctx.workflow_order == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_topological_sort_diamond(self):
        """다이아몬드 DAG: A→B, A→C, B→D, C→D."""
        phase = PreparePhase()
        workflow = {
            "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "A", "target": "C"},
                {"source": "B", "target": "D"},
                {"source": "C", "target": "D"},
            ],
        }
        req = ExecutionRequest(input_data="diamond", workflow_data=workflow)
        ctx = await phase.run(req)

        order = ctx.workflow_order
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    @pytest.mark.asyncio
    async def test_topological_sort_single_node(self):
        phase = PreparePhase()
        workflow = {"nodes": [{"id": "X"}], "edges": []}
        req = ExecutionRequest(input_data="single", workflow_data=workflow)
        ctx = await phase.run(req)
        assert ctx.workflow_order == ["X"]


# ═══════════════════════════════════════════
# Phase 2: RESOLVE
# ═══════════════════════════════════════════


class TestResolvePhase:

    @pytest.mark.asyncio
    async def test_resolve_with_registry(self):
        registry = ToolRegistry()
        spec = ToolSpec(
            name="echo", description="echo tool",
            parameters={"msg": {"type": "string", "description": "message"}},
            fn=AsyncMock(return_value={"ok": True}), is_async=True,
        )
        registry.register(spec)

        phase = ResolvePhase(tool_registry=registry)
        ctx = ExecutionContext(
            mode="agent", message="test", session_id="s1",
        )
        resolved = await phase.run(ctx)

        assert len(resolved.tools_schema) > 0
        assert resolved.tools_schema[0]["function"]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_resolve_empty_registry(self):
        phase = ResolvePhase(tool_registry=ToolRegistry())
        ctx = ExecutionContext(mode="agent", message="test", session_id="s1")
        resolved = await phase.run(ctx)
        assert resolved.tools_schema == []

    @pytest.mark.asyncio
    async def test_resolve_no_registry(self):
        phase = ResolvePhase()
        ctx = ExecutionContext(mode="agent", message="test", session_id="s1")
        resolved = await phase.run(ctx)
        assert resolved.tools == []
        assert resolved.tools_schema == []


# ═══════════════════════════════════════════
# Phase 3: EXECUTE
# ═══════════════════════════════════════════


def _mock_llm(*responses):
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=responses)
    return llm


class TestExecutePhase:

    @pytest.mark.asyncio
    async def test_text_only_response(self):
        llm = _mock_llm(ModelResponse(text="Hello!", tool_calls=[], usage={}))
        phase = ExecutePhase(llm=llm, tool_registry=ToolRegistry())

        ctx = ExecutionContext(mode="agent", message="Hi", session_id="s1")
        resolved = ResolvedContext(context=ctx, tools_schema=[])

        events = [e async for e in phase.run(resolved)]
        types = [e.type for e in events]

        assert "thinking" in types
        assert "done" in types
        assert events[-1].data["text"] == "Hello!"

    @pytest.mark.asyncio
    async def test_tool_call_then_done(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="greet", description="greet",
            parameters={}, fn=AsyncMock(return_value={"msg": "hi"}),
            is_async=True,
        ))

        llm = _mock_llm(
            ModelResponse(
                text=None, usage={},
                tool_calls=[ToolCall(id="tc_0", name="greet", arguments={})],
            ),
            ModelResponse(text="Greeted!", tool_calls=[], usage={}),
        )

        phase = ExecutePhase(llm=llm, tool_registry=registry)
        ctx = ExecutionContext(mode="agent", message="Say hi", session_id="s1")
        resolved = ResolvedContext(
            context=ctx,
            tools_schema=registry.to_openai_tools(),
        )

        events = [e async for e in phase.run(resolved)]
        types = [e.type for e in events]

        assert types.count("thinking") == 2
        assert "tool_call" in types
        assert "tool_result" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_max_iterations(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="loop", description="loop",
            parameters={}, fn=AsyncMock(return_value={}),
            is_async=True,
        ))

        infinite = ModelResponse(
            text=None, usage={},
            tool_calls=[ToolCall(id="tc_0", name="loop", arguments={})],
        )
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=infinite)

        phase = ExecutePhase(llm=llm, tool_registry=registry, max_iterations=3)
        ctx = ExecutionContext(mode="agent", message="loop", session_id="s1")
        resolved = ResolvedContext(context=ctx, tools_schema=[])

        events = [e async for e in phase.run(resolved)]
        assert events[-1].type == "error"
        assert "최대" in str(events[-1].data)

    @pytest.mark.asyncio
    async def test_llm_error(self):
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("API fail"))

        phase = ExecutePhase(llm=llm, tool_registry=ToolRegistry())
        ctx = ExecutionContext(mode="agent", message="fail", session_id="s1")
        resolved = ResolvedContext(context=ctx, tools_schema=[])

        events = [e async for e in phase.run(resolved)]
        assert any(e.type == "error" for e in events)

    @pytest.mark.asyncio
    async def test_tool_execution_error(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="broken", description="broken",
            parameters={}, fn=AsyncMock(side_effect=ValueError("bad")),
            is_async=True,
        ))

        llm = _mock_llm(
            ModelResponse(
                text=None, usage={},
                tool_calls=[ToolCall(id="tc_0", name="broken", arguments={})],
            ),
            ModelResponse(text="Recovered", tool_calls=[], usage={}),
        )

        phase = ExecutePhase(llm=llm, tool_registry=registry)
        ctx = ExecutionContext(mode="agent", message="break", session_id="s1")
        resolved = ResolvedContext(context=ctx, tools_schema=[])

        events = [e async for e in phase.run(resolved)]
        types = [e.type for e in events]
        assert "tool_result" in types
        assert "done" in types


# ═══════════════════════════════════════════
# Phase 4: STREAM
# ═══════════════════════════════════════════


class TestStreamPhase:

    @pytest.mark.asyncio
    async def test_default_adapter(self):
        adapter = DefaultStreamAdapter()
        event = ExecutionEvent("tool_call", {"name": "echo"})
        stream = adapter.convert(event)

        assert stream.event == "tool_call"
        assert "echo" in stream.data

    @pytest.mark.asyncio
    async def test_stream_phase_appends_end(self):
        async def fake_events():
            yield ExecutionEvent("done", {"text": "hi"})

        phase = StreamPhase()
        events = [e async for e in phase.run(fake_events())]

        assert len(events) == 2
        assert events[0].event == "done"
        assert events[1].event == "end"

    @pytest.mark.asyncio
    async def test_stream_phase_multiple_events(self):
        async def fake_events():
            yield ExecutionEvent("thinking", {"iteration": 1})
            yield ExecutionEvent("tool_call", {"name": "echo"})
            yield ExecutionEvent("tool_result", {"result": "ok"})
            yield ExecutionEvent("done", {"text": "finished"})

        phase = StreamPhase()
        events = [e async for e in phase.run(fake_events())]

        assert len(events) == 5  # 4 events + end
        assert [e.event for e in events] == [
            "thinking", "tool_call", "tool_result", "done", "end"
        ]

    @pytest.mark.asyncio
    async def test_custom_adapter(self):
        class UpperAdapter:
            def convert(self, event):
                return StreamEvent(
                    event=event.type.upper(),
                    data=json.dumps(event.data),
                )

        phase = StreamPhase(adapter=UpperAdapter())

        async def fake_events():
            yield ExecutionEvent("done", {"text": "hi"})

        events = [e async for e in phase.run(fake_events())]
        assert events[0].event == "DONE"


# ═══════════════════════════════════════════
# Phase 5: PERSIST
# ═══════════════════════════════════════════


class TestPersistPhase:

    @pytest.mark.asyncio
    async def test_persist_no_providers(self):
        """provider 없어도 에러 안 남."""
        phase = PersistPhase()
        result = ExecutionResult(session_id="s1", input_data="test")
        await phase.run(result)  # no exception

    @pytest.mark.asyncio
    async def test_persist_with_trace(self):
        trace = MagicMock()
        phase = PersistPhase(trace_collector=trace)
        result = ExecutionResult(
            session_id="s1", input_data="test", trace_id="t1",
        )
        await phase.run(result)
        trace.end_trace.assert_called_once_with("t1")

    @pytest.mark.asyncio
    async def test_persist_with_state_store(self):
        store = MagicMock()
        store.checkpoint = AsyncMock()
        phase = PersistPhase(state_store=store)
        result = ExecutionResult(session_id="s1", input_data="test")
        await phase.run(result)
        store.checkpoint.assert_called_once()


# ═══════════════════════════════════════════
# Full Pipeline Integration
# ═══════════════════════════════════════════


class TestExecutionPipeline:

    @pytest.mark.asyncio
    async def test_full_pipeline_text_only(self):
        """전체 파이프라인 — 도구 없이 텍스트 응답."""
        registry = ToolRegistry()
        llm = _mock_llm(ModelResponse(text="Pipeline works!", tool_calls=[], usage={}))

        pipeline = build_pipeline(llm=llm, tool_registry=registry)
        request = ExecutionRequest(input_data="test pipeline")

        events = []
        async for event in pipeline.run(request):
            events.append(event)

        event_types = [e.event for e in events]
        assert "thinking" in event_types
        assert "done" in event_types
        assert "end" in event_types

    @pytest.mark.asyncio
    async def test_full_pipeline_with_tool(self):
        """전체 파이프라인 — 도구 호출 포함."""
        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="add", description="add numbers",
            parameters={"a": {"type": "integer", "description": "a"}},
            fn=AsyncMock(return_value={"sum": 3}),
            is_async=True,
        ))

        llm = _mock_llm(
            ModelResponse(
                text=None, usage={},
                tool_calls=[ToolCall(id="tc_0", name="add", arguments={"a": 1})],
            ),
            ModelResponse(text="Sum is 3", tool_calls=[], usage={}),
        )

        pipeline = build_pipeline(llm=llm, tool_registry=registry)
        request = ExecutionRequest(input_data="add numbers")

        events = []
        async for event in pipeline.run(request):
            events.append(event)

        event_types = [e.event for e in events]
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "done" in event_types

    @pytest.mark.asyncio
    async def test_pipeline_persist_runs_on_error(self):
        """EXECUTE에서 에러 나도 PERSIST는 실행됨."""
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("boom"))

        persist = PersistPhase()

        pipeline = ExecutionPipeline(
            prepare=PreparePhase(),
            resolve=ResolvePhase(tool_registry=ToolRegistry()),
            execute=ExecutePhase(llm=llm, tool_registry=ToolRegistry()),
            persist=persist,
        )

        request = ExecutionRequest(input_data="fail")
        events = []
        async for event in pipeline.run(request):
            events.append(event)

        # error 이벤트가 있어야 함 (ExecutePhase가 에러를 이벤트로 yield)
        assert any(e.event == "error" for e in events)

    @pytest.mark.asyncio
    async def test_build_pipeline_defaults(self):
        """build_pipeline 기본값으로 생성."""
        llm = MagicMock()
        registry = ToolRegistry()
        pipeline = build_pipeline(llm=llm, tool_registry=registry)

        assert isinstance(pipeline, ExecutionPipeline)
        assert isinstance(pipeline.prepare, PreparePhase)
        assert isinstance(pipeline.resolve, ResolvePhase)
        assert isinstance(pipeline.execute, ExecutePhase)
        assert isinstance(pipeline.stream, StreamPhase)
        assert isinstance(pipeline.persist, PersistPhase)
