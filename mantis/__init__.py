"""mantis — AI Agent 실행 엔진 라이브러리.

pip install mantis              # 기본 (Agent + @tool + LLM)
pip install mantis[search]      # + graph-tool-call 도구 검색
pip install mantis[sandbox]     # + Docker 샌드박스
pip install mantis[state]       # + PostgreSQL 상태 저장
pip install mantis[all]         # 전부
"""

__version__ = "0.1.0"

from mantis.tools.decorator import tool, ToolSpec
from mantis.tools.registry import ToolRegistry
from mantis.engine.runner import Agent

__all__ = [
    "__version__",
    "Agent",
    "tool",
    "ToolSpec",
    "ToolRegistry",
]
