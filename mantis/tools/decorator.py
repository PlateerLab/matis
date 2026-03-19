"""@tool 데코레이터 — 유일한 도구 인터페이스."""

from __future__ import annotations

import inspect
import functools
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolSpec:
    """도구 메타데이터. LLM의 tools 파라미터 형식으로 변환 가능."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable
    is_async: bool = False

    def to_openai_schema(self) -> dict:
        """OpenAI function-calling 호환 JSON Schema로 변환."""
        properties = {}
        required = []

        for param_name, param_spec in self.parameters.items():
            prop: dict[str, Any] = {
                "type": param_spec.get("type", "string"),
                "description": param_spec.get("description", ""),
            }
            # OpenAI는 array 타입에 items 필수
            if prop["type"] == "array" and "items" not in prop:
                prop["items"] = param_spec.get("items", {"type": "string"})
            if param_spec.get("enum"):
                prop["enum"] = param_spec["enum"]
            properties[param_name] = prop
            if not param_spec.get("optional", False):
                required.append(param_name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    async def execute(self, **kwargs) -> Any:
        """도구 실행. sync/async 모두 지원."""
        if self.is_async:
            return await self.fn(**kwargs)
        return self.fn(**kwargs)


# 전역 도구 저장소 — import 시 자동 수집용
_tool_registry: list[ToolSpec] = []


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
) -> Callable:
    """@tool 데코레이터.

    사용 예:
        @tool(
            name="query_customer",
            description="고객 정보를 DB에서 조회한다.",
            parameters={"customer_id": {"type": "string", "description": "고객 ID"}}
        )
        async def query_customer(customer_id: str) -> dict:
            ...
    """

    def decorator(fn: Callable) -> Callable:
        spec = ToolSpec(
            name=name,
            description=description,
            parameters=parameters or {},
            fn=fn,
            is_async=inspect.iscoroutinefunction(fn),
        )

        # 전역 레지스트리에 등록
        _tool_registry.append(spec)

        # 원본 함수에 메타데이터 부착
        fn._tool_spec = spec

        @functools.wraps(fn)
        async def wrapper(**kwargs):
            return await spec.execute(**kwargs)

        wrapper._tool_spec = spec
        return wrapper

    return decorator


def get_registered_tools() -> list[ToolSpec]:
    """import 시 자동 등록된 도구 목록 반환."""
    return list(_tool_registry)
