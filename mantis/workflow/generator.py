"""Workflow Generator — LLM을 이용한 워크플로우 자동 생성."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mantis.exceptions import WorkflowGenerationError
from mantis.llm.protocol import LLMProvider
from mantis.tools.registry import ToolRegistry
from mantis.workflow.models import WorkflowDef, WorkflowStep, WorkflowEdge
from mantis.workflow.store import WorkflowStore

logger = logging.getLogger(__name__)

WORKFLOW_GENERATION_PROMPT = """\
사용 가능한 도구 목록을 참고하여 사용자의 요청에 맞는 워크플로우를 JSON으로 설계해줘.

**사용 가능한 도구:**
{tools}

**규칙:**
1. 각 단계(step)에는 고유한 id가 필요하다
2. type은 "tool", "condition", "agent", "parallel" 중 하나
3. type="tool"이면 tool(도구명)과 args(인자) 필수
4. type="condition"이면 condition(조건식), then_step, else_step 필수
5. type="agent"이면 prompt 필수, tools는 선택
6. type="parallel"이면 parallel_steps(단계 id 목록) 필수
7. args_from을 사용하면 이전 단계 결과를 인자로 전달 가능 ("step_id" 또는 "step_id.key")
8. 워크플로우 이름과 설명을 포함해야 한다

**출력 형식 — 반드시 아래 JSON 형식으로 출력:**

```json
{{
  "name": "워크플로우_이름",
  "description": "워크플로우 설명",
  "steps": [
    {{
      "id": "step1",
      "type": "tool",
      "tool": "도구명",
      "args": {{"key": "value"}}
    }},
    {{
      "id": "step2",
      "type": "condition",
      "condition": "steps.step1.key > 0.8",
      "then_step": "step3",
      "else_step": "step4"
    }}
  ]
}}
```

**사용자 요청:**
{description}
"""


class WorkflowGenerator:
    """LLM을 사용하여 자연어 설명으로부터 워크플로우를 자동 생성."""

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        store: WorkflowStore,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.store = store

    async def generate(
        self,
        description: str,
        session_id: str | None = None,
    ) -> WorkflowDef:
        """자연어 설명으로부터 워크플로우를 생성.

        1. 사용 가능한 도구 목록 조회
        2. LLM에 프롬프트 전달
        3. JSON 응답 파싱
        4. WorkflowDef로 변환
        5. 도구 참조 검증
        6. Store에 저장

        Args:
            description: 워크플로우에 대한 자연어 설명
            session_id: 세션 ID

        Returns:
            생성된 WorkflowDef

        Raises:
            WorkflowGenerationError: 생성 실패 시
        """
        logger.info("[WorkflowGenerator] 워크플로우 생성 시작: %s", description[:80])

        # Step 1: 사용 가능한 도구 목록 조회
        available_tools = self.registry.list_tools(session_id=session_id)
        tools_desc = "\n".join(
            f"- {t.name}: {t.description}" for t in available_tools
        )
        if not tools_desc:
            tools_desc = "(등록된 도구 없음)"

        # Step 2: 프롬프트 생성 및 LLM 호출
        prompt = WORKFLOW_GENERATION_PROMPT.format(
            tools=tools_desc, description=description
        )

        try:
            response = await self.llm.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        except Exception as e:
            raise WorkflowGenerationError(f"LLM 호출 실패: {e}") from e

        if not response.text:
            raise WorkflowGenerationError("LLM 응답이 비어 있음")

        # Step 3: JSON 추출
        json_data = self._extract_json(response.text)
        if json_data is None:
            raise WorkflowGenerationError(
                f"LLM 응답에서 JSON을 추출할 수 없음: {response.text[:200]}"
            )

        # Step 4: WorkflowDef로 파싱
        try:
            workflow = self._parse_workflow(json_data)
        except Exception as e:
            raise WorkflowGenerationError(f"워크플로우 파싱 실패: {e}") from e

        # Step 5: 도구 참조 검증
        self._validate_tool_references(workflow, session_id)

        # Step 6: Store에 저장
        self.store.save(workflow.name, workflow)
        logger.info(
            "[WorkflowGenerator] 워크플로우 생성 완료: %s (steps=%d)",
            workflow.name,
            len(workflow.steps),
        )

        return workflow

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        """LLM 응답에서 ```json ... ``` 블록을 추출하여 파싱."""
        # ```json ... ``` 블록 찾기
        match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 블록이 없으면 전체 텍스트에서 JSON 객체 추출 시도
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _parse_workflow(self, data: dict[str, Any]) -> WorkflowDef:
        """JSON dict를 WorkflowDef로 변환."""
        steps: list[WorkflowStep] = []
        for step_data in data.get("steps", []):
            steps.append(
                WorkflowStep(
                    id=step_data["id"],
                    type=step_data.get("type", "tool"),
                    tool=step_data.get("tool"),
                    args=step_data.get("args"),
                    args_from=step_data.get("args_from"),
                    condition=step_data.get("condition"),
                    then_step=step_data.get("then_step"),
                    else_step=step_data.get("else_step"),
                    prompt=step_data.get("prompt"),
                    tools=step_data.get("tools"),
                    parallel_steps=step_data.get("parallel_steps"),
                )
            )

        edges: list[WorkflowEdge] | None = None
        if "edges" in data and data["edges"]:
            edges = [
                WorkflowEdge(
                    source_step=e["source_step"],
                    source_key=e["source_key"],
                    target_step=e["target_step"],
                    target_key=e["target_key"],
                )
                for e in data["edges"]
            ]

        return WorkflowDef(
            name=data["name"],
            description=data.get("description", ""),
            steps=steps,
            edges=edges,
        )

    def _validate_tool_references(
        self,
        workflow: WorkflowDef,
        session_id: str | None,
    ) -> None:
        """워크플로우에서 참조하는 도구가 Registry에 존재하는지 검증."""
        available_names = set(self.registry.list_names(session_id=session_id))

        for step in workflow.steps:
            if step.type == "tool" and step.tool:
                if step.tool not in available_names:
                    raise WorkflowGenerationError(
                        f"단계 '{step.id}'가 참조하는 도구 '{step.tool}'이 "
                        "Registry에 존재하지 않음"
                    )
