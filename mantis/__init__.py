"""mantis — AI Agent 실행 엔진 라이브러리.

v3: 엔진은 Generator + Executor 두 개뿐. 나머지는 전부 도구.

pip install mantis              # 기본 (Agent + @tool + LLM)
pip install mantis[search]      # + graph-tool-call 도구 검색
pip install mantis[sandbox]     # + Docker 샌드박스
pip install mantis[state]       # + PostgreSQL 상태 저장
pip install mantis[all]         # 전부
"""

__version__ = "0.3.0"

# ─── 핵심 공개 API ───
from mantis.tools.decorator import tool, ToolSpec
from mantis.tools.registry import ToolRegistry
from mantis.engine.runner import Agent
from mantis.llm.protocol import LLMProvider, ModelResponse, ToolCall
from mantis.exceptions import (
    MantisError,
    ToolError,
    ToolNotFoundError,
    ToolExecutionError,
    GenerationError,
    ToolGenerationError,
    WorkflowGenerationError,
    WorkflowError,
    SandboxError,
    LLMError,
)

__all__ = [
    "__version__",
    # 엔진
    "Agent",
    # 도구 시스템
    "tool",
    "ToolSpec",
    "ToolRegistry",
    # LLM
    "LLMProvider",
    "ModelResponse",
    "ToolCall",
    # 예외
    "MantisError",
    "ToolError",
    "ToolNotFoundError",
    "ToolExecutionError",
    "GenerationError",
    "ToolGenerationError",
    "WorkflowGenerationError",
    "WorkflowError",
    "SandboxError",
    "LLMError",
]
