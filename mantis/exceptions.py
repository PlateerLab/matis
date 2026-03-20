"""mantis 예외 계층."""

from __future__ import annotations


class MantisError(Exception):
    """mantis 기본 예외."""


class ToolError(MantisError):
    """도구 관련 에러."""


class ToolNotFoundError(ToolError):
    """도구를 찾을 수 없음."""


class ToolExecutionError(ToolError):
    """도구 실행 중 에러."""


class GenerationError(MantisError):
    """AI 생성 실패."""


class ToolGenerationError(GenerationError):
    """도구 코드 생성/검증 실패."""


class WorkflowGenerationError(GenerationError):
    """워크플로우 설계 실패."""


class WorkflowError(MantisError):
    """워크플로우 실행 실패."""


class SandboxError(MantisError):
    """샌드박스 실행 실패."""


class LLMError(MantisError):
    """LLM 호출 실패."""
