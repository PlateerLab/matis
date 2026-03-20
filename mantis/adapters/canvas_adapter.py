"""Canvas Adapter — xgen-workflow 캔버스 JSON을 워크플로우 도구 호출로 변환.

xgen-workflow의 캔버스 JSON 형식:
{
    "nodes": [
        {"id": "node1", "type": "agents", "params": {"prompt": "...", "model": "..."}},
        {"id": "node2", "type": "router", "params": {"conditions": {...}}},
        {"id": "node3", "type": "api_tool", "params": {"tool_name": "...", ...}},
        {"id": "node4", "type": "qdrant_search", "params": {"collection": "..."}},
        {"id": "node5", "type": "schema_input", "params": {"fields": [...]}},
        {"id": "node6", "type": "end", "params": {}},
    ],
    "edges": [
        {"source": {"nodeId": "node1", "portId": "output"},
         "target": {"nodeId": "node2", "portId": "input"}},
    ]
}

→ create_workflow 도구의 steps/edges 형식으로 변환.
"""

from __future__ import annotations

import logging
from typing import Any

from mantis.workflow.models import WorkflowDef, WorkflowStep, WorkflowEdge

logger = logging.getLogger(__name__)

# xgen-workflow 노드 타입 → WorkflowStep 타입 매핑
_NODE_TYPE_MAP = {
    "agents": "agent",
    "xgen": "agent",
    "router": "condition",
    "api_tool": "tool",
    "qdrant_search": "tool",
    "db_memory": "tool",
    "schema_input": "tool",
    "schema_output": "tool",
    "end": "tool",
}


def canvas_to_workflow(
    workflow_data: dict[str, Any],
    name: str = "canvas_workflow",
    description: str = "",
) -> WorkflowDef:
    """xgen-workflow 캔버스 JSON을 WorkflowDef로 변환.

    Args:
        workflow_data: 캔버스 JSON (nodes + edges).
        name: 워크플로우 이름.
        description: 워크플로우 설명.

    Returns:
        WorkflowDef
    """
    nodes = workflow_data.get("nodes", [])
    edges = workflow_data.get("edges", [])

    steps: list[WorkflowStep] = []
    for node in nodes:
        step = _convert_node(node)
        if step:
            steps.append(step)

    wf_edges: list[WorkflowEdge] = []
    for edge in edges:
        wf_edge = _convert_edge(edge)
        if wf_edge:
            wf_edges.append(wf_edge)

    return WorkflowDef(
        name=name,
        description=description or f"캔버스에서 변환된 워크플로우 ({len(steps)} steps)",
        steps=steps,
        edges=wf_edges if wf_edges else None,
    )


def canvas_to_create_workflow_args(
    workflow_data: dict[str, Any],
    name: str = "canvas_workflow",
    description: str = "",
) -> dict[str, Any]:
    """캔버스 JSON을 create_workflow 도구의 인자로 변환.

    xgen-workflow에서 mantis로 이식할 때 사용:
        args = canvas_to_create_workflow_args(workflow_data)
        await registry.execute({"name": "create_workflow", "arguments": args})
    """
    wf = canvas_to_workflow(workflow_data, name, description)
    return {
        "name": wf.name,
        "description": wf.description,
        "steps": [_step_to_dict(s) for s in wf.steps],
        "edges": [_edge_to_dict(e) for e in wf.edges] if wf.edges else None,
    }


def _convert_node(node: dict[str, Any]) -> WorkflowStep | None:
    """캔버스 노드 → WorkflowStep 변환."""
    node_id = node.get("id", "")
    node_type = node.get("type", "")
    params = node.get("params", {})

    step_type = _NODE_TYPE_MAP.get(node_type)
    if not step_type:
        logger.warning("알 수 없는 노드 타입 '%s' (id=%s), 건너뜀", node_type, node_id)
        return None

    if step_type == "agent":
        return WorkflowStep(
            id=node_id,
            type="agent",
            prompt=params.get("prompt", params.get("system_prompt", "")),
            tools=params.get("tools"),
        )

    if step_type == "condition":
        # router 노드의 conditions를 condition 문자열로 변환
        conditions = params.get("conditions", {})
        condition_str = ""
        then_step = None
        else_step = None

        if isinstance(conditions, dict):
            # 첫 번째 조건을 기본으로 사용
            for key, value in conditions.items():
                condition_str = str(value) if value else key
                break

        return WorkflowStep(
            id=node_id,
            type="condition",
            condition=condition_str,
            then_step=then_step,
            else_step=else_step,
        )

    # type="tool" (api_tool, qdrant_search, db_memory, schema_*, end)
    tool_name = params.get("tool_name", f"canvas_{node_type}")
    args = {k: v for k, v in params.items() if k != "tool_name"}

    return WorkflowStep(
        id=node_id,
        type="tool",
        tool=tool_name,
        args=args if args else None,
    )


def _convert_edge(edge: dict[str, Any]) -> WorkflowEdge | None:
    """캔버스 엣지 → WorkflowEdge 변환."""
    source = edge.get("source", {})
    target = edge.get("target", {})

    source_node = source.get("nodeId", "")
    source_port = source.get("portId", "output")
    target_node = target.get("nodeId", "")
    target_port = target.get("portId", "input")

    if not source_node or not target_node:
        return None

    return WorkflowEdge(
        source_step=source_node,
        source_key=source_port,
        target_step=target_node,
        target_key=target_port,
    )


def _step_to_dict(step: WorkflowStep) -> dict[str, Any]:
    """WorkflowStep → create_workflow steps 인자 dict."""
    d: dict[str, Any] = {"id": step.id, "type": step.type}
    if step.tool is not None:
        d["tool"] = step.tool
    if step.args is not None:
        d["args"] = step.args
    if step.args_from is not None:
        d["args_from"] = step.args_from
    if step.condition is not None:
        d["condition"] = step.condition
    if step.then_step is not None:
        d["then_step"] = step.then_step
    if step.else_step is not None:
        d["else_step"] = step.else_step
    if step.prompt is not None:
        d["prompt"] = step.prompt
    if step.tools is not None:
        d["tools"] = step.tools
    if step.parallel_steps is not None:
        d["parallel_steps"] = step.parallel_steps
    return d


def _edge_to_dict(edge: WorkflowEdge) -> dict[str, Any]:
    """WorkflowEdge → create_workflow edges 인자 dict."""
    return {
        "source_step": edge.source_step,
        "source_key": edge.source_key,
        "target_step": edge.target_step,
        "target_key": edge.target_key,
    }
