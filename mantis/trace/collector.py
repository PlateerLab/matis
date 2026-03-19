"""Trace 수집 — 모든 도구 호출을 자동 추적."""

from __future__ import annotations

import time
import uuid
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    THINK = "think"
    TOOL_CALL = "tool_call"
    RESPONSE = "response"
    ERROR = "error"


@dataclass
class TraceStep:
    """실행 단계 하나."""

    step_type: StepType
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass
class Trace:
    """하나의 실행 트레이스."""

    trace_id: str
    session_id: str
    agent_name: str
    steps: list[TraceStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "agent": self.agent_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "steps": [
                {
                    "type": step.step_type.value,
                    "timestamp": step.timestamp,
                    **step.data,
                }
                for step in self.steps
            ],
        }


class TraceCollector:
    """실행 로그 수집기. 메모리에 저장하며, exporter로 내보내기 가능."""

    def __init__(self):
        self._traces: dict[str, Trace] = {}

    def start_trace(self, session_id: str, agent_name: str) -> str:
        """새 트레이스 시작. trace_id 반환."""
        trace_id = f"tr_{uuid.uuid4().hex[:12]}"
        self._traces[trace_id] = Trace(
            trace_id=trace_id,
            session_id=session_id,
            agent_name=agent_name,
        )
        logger.debug("Trace 시작: %s", trace_id)
        return trace_id

    def add_step(self, trace_id: str, step_type: StepType, data: dict[str, Any]) -> None:
        """트레이스에 단계 추가."""
        trace = self._traces.get(trace_id)
        if not trace:
            logger.warning("존재하지 않는 trace: %s", trace_id)
            return
        trace.steps.append(TraceStep(step_type=step_type, data=data))

    def end_trace(self, trace_id: str) -> Trace | None:
        """트레이스 종료."""
        trace = self._traces.get(trace_id)
        if trace:
            trace.end_time = time.time()
            logger.debug("Trace 종료: %s (%.0fms)", trace_id, trace.duration_ms)
        return trace

    def get_trace(self, trace_id: str) -> Trace | None:
        return self._traces.get(trace_id)

    def list_traces(self, session_id: str | None = None) -> list[Trace]:
        traces = list(self._traces.values())
        if session_id:
            traces = [t for t in traces if t.session_id == session_id]
        return traces
