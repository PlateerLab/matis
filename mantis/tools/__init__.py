"""도구 시스템 — @tool 데코레이터 + ToolRegistry + 메타 도구."""

from mantis.tools.decorator import tool, ToolSpec
from mantis.tools.registry import ToolRegistry
from mantis.tools.meta import make_registry_tools

__all__ = [
    "tool",
    "ToolSpec",
    "ToolRegistry",
    "make_registry_tools",
]
