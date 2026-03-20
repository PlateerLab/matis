"""Workflow Runner — 워크플로우 정의를 실제 실행."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from mantis.exceptions import WorkflowError
from mantis.tools.registry import ToolRegistry
from mantis.workflow.models import WorkflowDef, WorkflowStep, StepExecutor

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """워크플로우를 단계별로 실행하는 러너.

    - type="tool": ToolRegistry를 통해 도구 호출
    - type="condition": 조건 평가 후 분기
    - type="agent": StepExecutor를 통해 에이전트 호출
    - type="parallel": 여러 단계를 동시 실행
    """

    def __init__(
        self,
        registry: ToolRegistry,
        agent_executor: StepExecutor | None = None,
    ) -> None:
        self.registry = registry
        self.agent_executor = agent_executor

    async def run(
        self,
        workflow: WorkflowDef,
        input_data: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """워크플로우 실행.

        Args:
            workflow: 실행할 워크플로우 정의
            input_data: 초기 입력 데이터
            session_id: 세션 ID (도구 조회 시 사용)

        Returns:
            각 단계의 결과를 담은 dict. {"input": ..., "step_id": result, ...}
        """
        step_results: dict[str, Any] = {"input": input_data}
        step_map: dict[str, WorkflowStep] = {s.id: s for s in workflow.steps}

        # 실행 순서: steps 리스트 순서를 기본으로 사용
        execution_order = [s.id for s in workflow.steps]

        logger.info(
            "워크플로우 '%s' 실행 시작 (steps=%d)",
            workflow.name,
            len(execution_order),
        )

        i = 0
        while i < len(execution_order):
            step_id = execution_order[i]
            step = step_map.get(step_id)
            if step is None:
                logger.warning("단계 '%s'를 찾을 수 없음 — 건너뜀", step_id)
                i += 1
                continue

            logger.info("단계 실행: %s (type=%s)", step.id, step.type)

            try:
                if step.type == "tool":
                    result = await self._execute_tool_step(
                        step, step_results, session_id
                    )
                    step_results[step.id] = result

                elif step.type == "condition":
                    branch = self._evaluate_condition(
                        step.condition or "", step_results
                    )
                    target = step.then_step if branch else step.else_step
                    step_results[step.id] = {
                        "condition": step.condition,
                        "result": branch,
                        "next": target,
                    }
                    # 분기 대상이 있으면 해당 단계로 점프
                    if target and target in step_map:
                        target_idx = None
                        for idx, sid in enumerate(execution_order):
                            if sid == target:
                                target_idx = idx
                                break
                        if target_idx is not None:
                            i = target_idx
                            continue

                elif step.type == "agent":
                    if self.agent_executor is None:
                        raise WorkflowError(
                            f"단계 '{step.id}'는 agent 타입이지만 "
                            "agent_executor가 설정되지 않음"
                        )
                    result = await self.agent_executor.execute(
                        step.prompt or "", step.tools
                    )
                    step_results[step.id] = result

                elif step.type == "parallel":
                    if step.parallel_steps:
                        tasks = []
                        for ps_id in step.parallel_steps:
                            ps = step_map.get(ps_id)
                            if ps and ps.type == "tool":
                                tasks.append(
                                    self._execute_tool_step(
                                        ps, step_results, session_id
                                    )
                                )
                            elif ps and ps.type == "agent":
                                if self.agent_executor is None:
                                    raise WorkflowError(
                                        f"병렬 단계 '{ps_id}'는 agent 타입이지만 "
                                        "agent_executor가 설정되지 않음"
                                    )
                                tasks.append(
                                    self.agent_executor.execute(
                                        ps.prompt or "", ps.tools
                                    )
                                )
                            else:
                                tasks.append(asyncio.coroutine(lambda: None)())

                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        parallel_results: dict[str, Any] = {}
                        for idx, ps_id in enumerate(step.parallel_steps):
                            r = results[idx]
                            if isinstance(r, Exception):
                                parallel_results[ps_id] = {"error": str(r)}
                            else:
                                parallel_results[ps_id] = r
                            step_results[ps_id] = parallel_results[ps_id]
                        step_results[step.id] = parallel_results

                else:
                    logger.warning("알 수 없는 단계 타입: %s", step.type)
                    step_results[step.id] = {"error": f"unknown type: {step.type}"}

            except WorkflowError:
                raise
            except Exception as e:
                logger.exception("단계 '%s' 실행 실패", step.id)
                step_results[step.id] = {"error": str(e)}

            i += 1

        logger.info("워크플로우 '%s' 실행 완료", workflow.name)
        return step_results

    async def _execute_tool_step(
        self,
        step: WorkflowStep,
        step_results: dict[str, Any],
        session_id: str | None,
    ) -> Any:
        """tool 타입 단계 실행."""
        args = self._resolve_args(step, step_results)
        tool_call = {"name": step.tool, "arguments": args}
        result = await self.registry.execute(tool_call, session_id=session_id)
        return result.get("result", result)

    def _resolve_args(
        self,
        step: WorkflowStep,
        step_results: dict[str, Any],
    ) -> dict[str, Any]:
        """단계의 인자를 해석.

        - step.args가 있으면 그대로 사용
        - step.args_from이 있으면 이전 단계 결과에서 추출
          - "step_id": 해당 단계의 전체 결과
          - "step_id.key": 해당 단계 결과의 특정 키
        """
        if step.args_from:
            parts = step.args_from.split(".", 1)
            source_id = parts[0]
            source_result = step_results.get(source_id)

            if source_result is None:
                logger.warning(
                    "args_from 소스 '%s'의 결과가 없음", source_id
                )
                return step.args or {}

            if len(parts) == 2:
                key = parts[1]
                if isinstance(source_result, dict):
                    return {key: source_result.get(key, source_result)}
                return {key: source_result}
            else:
                if isinstance(source_result, dict):
                    return source_result
                return {"value": source_result}

        return step.args or {}

    def _evaluate_condition(
        self,
        condition_str: str,
        step_results: dict[str, Any],
    ) -> bool:
        """조건 문자열을 안전하게 평가.

        지원 패턴:
        - steps.step_id.key > 0.8
        - steps.step_id.key == "value"

        평가 실패 시 False 반환.
        """
        if not condition_str.strip():
            return False

        try:
            # "steps.step_id.key" 패턴을 실제 값으로 치환
            def replace_ref(match: re.Match) -> str:
                path = match.group(0)
                # "steps." 접두사 제거
                parts = path.split(".")
                if parts[0] == "steps":
                    parts = parts[1:]

                current: Any = step_results
                for part in parts:
                    if isinstance(current, dict):
                        current = current.get(part)
                    else:
                        return "None"
                    if current is None:
                        return "None"

                return repr(current)

            # steps.xxx.yyy 패턴 매칭
            resolved = re.sub(
                r"steps\.\w+(?:\.\w+)*",
                replace_ref,
                condition_str,
            )

            # 안전한 eval: 내장 함수 차단
            result = eval(resolved, {"__builtins__": {}}, {})
            return bool(result)

        except Exception:
            logger.warning("조건 평가 실패: %s", condition_str)
            return False
