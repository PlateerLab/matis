"""파이프라인 데이터 모델 — Phase 간 전달되는 구조체."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionRequest:
    """파이프라인 입력 요청."""

    input_data: str
    session_id: str | None = None
    workflow_data: dict[str, Any] | None = None
    resume: bool = False
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionContext:
    """Phase 1 (PREPARE) 출력 — 실행에 필요한 모든 정보."""

    mode: str  # "agent" | "workflow"
    message: str
    session_id: str
    system_prompt: str = ""
    tools: list[Any] = field(default_factory=list)
    workflow_order: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedContext:
    """Phase 2 (RESOLVE) 출력 — 도구/RAG/메모리가 결정된 상태."""

    context: ExecutionContext
    tools: list[Any] = field(default_factory=list)
    tools_schema: list[dict[str, Any]] = field(default_factory=list)
    rag_context: list[str] = field(default_factory=list)
    memory: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""


@dataclass
class ExecutionEvent:
    """Phase 3 (EXECUTE) 이벤트 — 실행 중 발생하는 이벤트."""

    type: str  # thinking, tool_call, tool_result, approval_required, done, error
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}


@dataclass
class StreamEvent:
    """Phase 4 (STREAM) 이벤트 — 클라이언트 전달용."""

    event: str | None = None
    data: str = ""

    def to_sse(self) -> str:
        """SSE 포맷 문자열 반환."""
        parts = []
        if self.event:
            parts.append(f"event: {self.event}")
        parts.append(f"data: {self.data}")
        parts.append("")
        return "\n".join(parts) + "\n"


@dataclass
class ExecutionResult:
    """Phase 5 (PERSIST) 입력 — 실행 결과."""

    session_id: str
    input_data: str
    output: str = ""
    trace_id: str | None = None
    status: str = "completed"  # completed | error
    metadata: dict[str, Any] = field(default_factory=dict)
