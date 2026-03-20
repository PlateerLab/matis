"""Workflow 모델 정의."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class WorkflowStep:
    """워크플로우의 단일 실행 단계.

    type에 따라 사용하는 필드가 다름:
    - "tool": tool, args, args_from
    - "condition": condition, then_step, else_step
    - "agent": prompt, tools
    - "parallel": parallel_steps
    """

    id: str
    type: str = "tool"  # "tool" | "condition" | "agent" | "parallel"

    # type="tool"
    tool: str | None = None
    args: dict[str, Any] | None = None
    args_from: str | None = None  # "step_id" or "step_id.key"

    # type="condition"
    condition: str | None = None
    then_step: str | None = None
    else_step: str | None = None

    # type="agent"
    prompt: str | None = None
    tools: list[str] | None = None

    # type="parallel"
    parallel_steps: list[str] | None = None


@dataclass
class WorkflowEdge:
    """워크플로우 단계 간 데이터 연결."""

    source_step: str
    source_key: str
    target_step: str
    target_key: str


@dataclass
class WorkflowDef:
    """워크플로우 정의. 이름, 설명, 단계 목록으로 구성."""

    name: str
    description: str
    steps: list[WorkflowStep]
    edges: list[WorkflowEdge] | None = None


@runtime_checkable
class StepExecutor(Protocol):
    """Agent 단계 실행 프로토콜.

    Agent 모듈과의 순환 의존을 방지하기 위한 Protocol.
    """

    async def execute(self, prompt: str, tools: list[str] | None = None) -> str: ...
