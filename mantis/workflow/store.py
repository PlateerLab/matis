"""Workflow Store — 워크플로우 정의를 메모리에 저장/조회."""

from __future__ import annotations

import logging

from mantis.workflow.models import WorkflowDef

logger = logging.getLogger(__name__)


class WorkflowStore:
    """워크플로우 정의를 인메모리로 관리하는 저장소."""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDef] = {}

    def save(self, name: str, workflow_def: WorkflowDef) -> None:
        """워크플로우 저장. 동일 이름이면 덮어씀."""
        self._workflows[name] = workflow_def
        logger.info("워크플로우 저장: %s", name)

    def get(self, name: str) -> WorkflowDef | None:
        """이름으로 워크플로우 조회."""
        return self._workflows.get(name)

    def list_all(self) -> list[WorkflowDef]:
        """저장된 모든 워크플로우 목록 반환."""
        return list(self._workflows.values())

    def delete(self, name: str) -> bool:
        """워크플로우 삭제. 성공 시 True."""
        removed = self._workflows.pop(name, None)
        if removed:
            logger.info("워크플로우 삭제: %s", name)
            return True
        return False
