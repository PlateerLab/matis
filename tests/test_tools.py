"""도구 데코레이터 및 레지스트리 테스트."""

import pytest
from mantis.tools.decorator import tool, ToolSpec
from mantis.tools.registry import ToolRegistry


def test_tool_decorator():
    """@tool 데코레이터가 메타데이터를 올바르게 부착하는지."""

    @tool(
        name="test_tool",
        description="테스트 도구",
        parameters={"value": {"type": "string", "description": "값"}},
    )
    async def test_tool_fn(value: str) -> dict:
        return {"echo": value}

    assert hasattr(test_tool_fn, "_tool_spec")
    spec = test_tool_fn._tool_spec
    assert spec.name == "test_tool"
    assert spec.description == "테스트 도구"
    assert spec.is_async is True


def test_tool_openai_schema():
    """OpenAI 호환 스키마 변환."""
    spec = ToolSpec(
        name="greet",
        description="인사 도구",
        parameters={"name": {"type": "string", "description": "이름"}},
        fn=lambda: None,
    )
    schema = spec.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "greet"
    assert "name" in schema["function"]["parameters"]["properties"]
    assert "name" in schema["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_registry_execute():
    """레지스트리를 통한 도구 실행."""
    registry = ToolRegistry()

    async def echo_fn(msg: str) -> dict:
        return {"echo": msg}

    spec = ToolSpec(
        name="echo",
        description="에코",
        parameters={"msg": {"type": "string", "description": "메시지"}},
        fn=echo_fn,
        is_async=True,
    )
    registry.register(spec)

    result = await registry.execute({"name": "echo", "arguments": {"msg": "hello"}})
    assert result["name"] == "echo"
    assert result["result"]["echo"] == "hello"


@pytest.mark.asyncio
async def test_registry_unknown_tool():
    """존재하지 않는 도구 실행 시 에러."""
    registry = ToolRegistry()
    result = await registry.execute({"name": "없는도구", "arguments": {}})
    assert "error" in result
