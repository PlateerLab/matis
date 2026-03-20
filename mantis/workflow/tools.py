"""Workflow Tools — 워크플로우 관련 도구를 ToolSpec으로 생성."""

from __future__ import annotations

import logging
from typing import Any

from mantis.tools.decorator import tool, ToolSpec
from mantis.workflow.models import WorkflowDef, WorkflowStep, WorkflowEdge
from mantis.workflow.store import WorkflowStore
from mantis.workflow.runner import WorkflowRunner
from mantis.workflow.generator import WorkflowGenerator

logger = logging.getLogger(__name__)


def make_workflow_tools(
    store: WorkflowStore,
    runner: WorkflowRunner,
    generator: WorkflowGenerator,
) -> list[ToolSpec]:
    """워크플로우 관련 도구 4개를 생성하여 반환.

    - generate_workflow: LLM으로 워크플로우 자동 생성
    - create_workflow: 수동으로 워크플로우 생성
    - run_workflow: 워크플로우 실행
    - list_workflows: 저장된 워크플로우 목록 조회

    Usage:
        specs = make_workflow_tools(store, runner, generator)
        for spec in specs:
            registry.register(spec, source="builtin")
    """

    @tool(
        name="generate_workflow",
        description=(
            "자연어 설명으로부터 워크플로우를 자동 생성한다. "
            "사용 가능한 도구를 참고하여 LLM이 단계별 워크플로우를 설계한다."
        ),
        parameters={
            "description": {
                "type": "string",
                "description": "생성할 워크플로우에 대한 자연어 설명",
            },
        },
    )
    async def generate_workflow(description: str) -> dict:
        try:
            workflow = await generator.generate(description)
            return {
                "status": "success",
                "name": workflow.name,
                "description": workflow.description,
                "steps_count": len(workflow.steps),
                "steps": [
                    {"id": s.id, "type": s.type, "tool": s.tool}
                    for s in workflow.steps
                ],
            }
        except Exception as e:
            logger.exception("워크플로우 생성 실패")
            return {"status": "error", "error": str(e)}

    @tool(
        name="create_workflow",
        description=(
            "워크플로우를 수동으로 생성한다. "
            "이름, 설명, 단계 목록을 직접 지정하여 저장한다."
        ),
        parameters={
            "name": {
                "type": "string",
                "description": "워크플로우 이름",
            },
            "description": {
                "type": "string",
                "description": "워크플로우 설명",
            },
            "steps": {
                "type": "array",
                "description": "단계 목록. 각 단계는 id, type 등을 포함하는 dict",
                "items": {"type": "object"},
            },
            "edges": {
                "type": "array",
                "description": "단계 간 데이터 연결 (선택)",
                "items": {"type": "object"},
                "optional": True,
            },
        },
    )
    async def create_workflow(
        name: str,
        description: str,
        steps: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> dict:
        try:
            workflow_steps = [
                WorkflowStep(
                    id=s["id"],
                    type=s.get("type", "tool"),
                    tool=s.get("tool"),
                    args=s.get("args"),
                    args_from=s.get("args_from"),
                    condition=s.get("condition"),
                    then_step=s.get("then_step"),
                    else_step=s.get("else_step"),
                    prompt=s.get("prompt"),
                    tools=s.get("tools"),
                    parallel_steps=s.get("parallel_steps"),
                )
                for s in steps
            ]

            workflow_edges: list[WorkflowEdge] | None = None
            if edges:
                workflow_edges = [
                    WorkflowEdge(
                        source_step=e["source_step"],
                        source_key=e["source_key"],
                        target_step=e["target_step"],
                        target_key=e["target_key"],
                    )
                    for e in edges
                ]

            workflow_def = WorkflowDef(
                name=name,
                description=description,
                steps=workflow_steps,
                edges=workflow_edges,
            )
            store.save(name, workflow_def)

            return {
                "status": "success",
                "name": name,
                "steps_count": len(workflow_steps),
            }
        except Exception as e:
            logger.exception("워크플로우 생성 실패")
            return {"status": "error", "error": str(e)}

    @tool(
        name="run_workflow",
        description=(
            "저장된 워크플로우를 실행한다. "
            "워크플로우 이름과 입력 데이터를 지정하여 단계별로 실행한다."
        ),
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "실행할 워크플로우 이름",
            },
            "input_data": {
                "type": "object",
                "description": "워크플로우에 전달할 입력 데이터 (선택)",
                "optional": True,
            },
        },
    )
    async def run_workflow(
        workflow_name: str,
        input_data: dict[str, Any] | None = None,
    ) -> dict:
        try:
            workflow = store.get(workflow_name)
            if workflow is None:
                return {
                    "status": "error",
                    "error": f"워크플로우 '{workflow_name}'을 찾을 수 없음",
                }

            result = await runner.run(workflow, input_data or {})
            return {"status": "success", "workflow": workflow_name, "result": result}
        except Exception as e:
            logger.exception("워크플로우 실행 실패")
            return {"status": "error", "error": str(e)}

    @tool(
        name="list_workflows",
        description="저장된 모든 워크플로우 목록을 조회한다.",
        parameters={},
    )
    async def list_workflows() -> dict:
        try:
            workflows = store.list_all()
            return {
                "status": "success",
                "count": len(workflows),
                "workflows": [
                    {
                        "name": w.name,
                        "description": w.description,
                        "steps_count": len(w.steps),
                    }
                    for w in workflows
                ],
            }
        except Exception as e:
            logger.exception("워크플로우 목록 조회 실패")
            return {"status": "error", "error": str(e)}

    return [
        generate_workflow._tool_spec,
        create_workflow._tool_spec,
        run_workflow._tool_spec,
        list_workflows._tool_spec,
    ]
