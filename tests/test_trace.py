"""Trace 수집기 테스트."""

from mantis.trace.collector import TraceCollector, StepType


def test_trace_lifecycle():
    """트레이스 시작 → 단계 추가 → 종료."""
    collector = TraceCollector()

    trace_id = collector.start_trace(session_id="sess_1", agent_name="test-agent")
    assert trace_id.startswith("tr_")

    collector.add_step(trace_id, StepType.THINK, {"model": "gpt-4", "duration_ms": 100})
    collector.add_step(trace_id, StepType.TOOL_CALL, {"tool": "echo", "duration_ms": 10})
    collector.add_step(trace_id, StepType.RESPONSE, {"text": "완료"})

    trace = collector.end_trace(trace_id)
    assert trace is not None
    assert trace.duration_ms is not None
    assert trace.duration_ms >= 0
    assert len(trace.steps) == 3


def test_trace_to_dict():
    """트레이스 → dict 변환."""
    collector = TraceCollector()
    trace_id = collector.start_trace(session_id="s1", agent_name="agent1")
    collector.add_step(trace_id, StepType.THINK, {"model": "test"})
    collector.end_trace(trace_id)

    trace = collector.get_trace(trace_id)
    d = trace.to_dict()
    assert d["trace_id"] == trace_id
    assert d["session_id"] == "s1"
    assert d["agent"] == "agent1"
    assert len(d["steps"]) == 1
    assert d["steps"][0]["type"] == "think"


def test_trace_list_by_session():
    """세션별 트레이스 목록 필터링."""
    collector = TraceCollector()

    collector.start_trace(session_id="sess_a", agent_name="a")
    collector.start_trace(session_id="sess_b", agent_name="b")
    collector.start_trace(session_id="sess_a", agent_name="a2")

    all_traces = collector.list_traces()
    assert len(all_traces) == 3

    sess_a = collector.list_traces(session_id="sess_a")
    assert len(sess_a) == 2

    sess_b = collector.list_traces(session_id="sess_b")
    assert len(sess_b) == 1


def test_trace_nonexistent():
    """존재하지 않는 트레이스."""
    collector = TraceCollector()
    assert collector.get_trace("없는거") is None
    assert collector.end_trace("없는거") is None


def test_trace_add_step_invalid_id():
    """잘못된 trace_id로 단계 추가 — 무시."""
    collector = TraceCollector()
    collector.add_step("없는거", StepType.THINK, {})  # 에러 없이 무시
