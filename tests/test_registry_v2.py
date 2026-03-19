"""ToolRegistry v2 — 세션 스코프 + 소스 추적 테스트."""

import pytest
from unittest.mock import AsyncMock

from mantis.tools.decorator import ToolSpec
from mantis.tools.registry import ToolRegistry


def _make_spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool",
        parameters={},
        fn=AsyncMock(return_value={"ok": True}),
        is_async=True,
    )


# ─── 소스 추적 ───


def test_register_with_source():
    registry = ToolRegistry()
    spec = _make_spec("echo")
    registry.register(spec, source="builtin")
    assert registry.get_source("echo") == "builtin"


def test_register_default_source():
    registry = ToolRegistry()
    spec = _make_spec("echo")
    registry.register(spec)
    assert registry.get_source("echo") == "manual"


# ─── 세션 스코프 ───


def test_session_tool_not_visible_globally():
    registry = ToolRegistry()
    spec = _make_spec("session_tool")
    registry.register(spec, source="generated", session_id="s1")

    # 글로벌에서 안 보임
    assert registry.get("session_tool") is None
    assert "session_tool" not in registry.list_names()
    assert len(registry.to_openai_tools()) == 0


def test_session_tool_visible_in_session():
    registry = ToolRegistry()
    spec = _make_spec("session_tool")
    registry.register(spec, source="generated", session_id="s1")

    # 해당 세션에서 보임
    assert registry.get("session_tool", session_id="s1") is not None
    assert "session_tool" in registry.list_names(session_id="s1")
    tools = registry.to_openai_tools(session_id="s1")
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "session_tool"


def test_session_tool_not_visible_in_other_session():
    registry = ToolRegistry()
    spec = _make_spec("session_tool")
    registry.register(spec, source="generated", session_id="s1")

    # 다른 세션에서 안 보임
    assert registry.get("session_tool", session_id="s2") is None
    assert "session_tool" not in registry.list_names(session_id="s2")


def test_global_and_session_tools_merged():
    registry = ToolRegistry()
    registry.register(_make_spec("global_tool"), source="builtin")
    registry.register(_make_spec("session_tool"), source="generated", session_id="s1")

    # 세션 조회 시 글로벌 + 세션 합산
    names = registry.list_names(session_id="s1")
    assert "global_tool" in names
    assert "session_tool" in names

    tools = registry.to_openai_tools(session_id="s1")
    assert len(tools) == 2


def test_session_tool_overrides_global():
    """세션 도구가 글로벌 도구와 이름 충돌 시 세션 도구 우선."""
    registry = ToolRegistry()
    global_spec = _make_spec("echo")
    global_spec.description = "global echo"
    session_spec = _make_spec("echo")
    session_spec.description = "session echo"

    registry.register(global_spec, source="builtin")
    registry.register(session_spec, source="generated", session_id="s1")

    # 세션에서 조회하면 세션 도구
    spec = registry.get("echo", session_id="s1")
    assert spec.description == "session echo"

    # 글로벌에서 조회하면 글로벌 도구
    spec = registry.get("echo")
    assert spec.description == "global echo"


# ─── cleanup_session ───


def test_cleanup_session():
    registry = ToolRegistry()
    registry.register(_make_spec("t1"), source="generated", session_id="s1")
    registry.register(_make_spec("t2"), source="generated", session_id="s1")
    registry.register(_make_spec("global"), source="builtin")

    removed = registry.cleanup_session("s1")
    assert removed == 2

    # 세션 도구 사라짐
    assert "t1" not in registry.list_names(session_id="s1")
    assert "t2" not in registry.list_names(session_id="s1")

    # 글로벌 도구 유지
    assert "global" in registry.list_names()


def test_cleanup_nonexistent_session():
    registry = ToolRegistry()
    removed = registry.cleanup_session("nonexistent")
    assert removed == 0


# ─── execute with session ───


@pytest.mark.asyncio
async def test_execute_session_tool():
    registry = ToolRegistry()
    spec = _make_spec("session_echo")
    registry.register(spec, source="generated", session_id="s1")

    result = await registry.execute(
        {"name": "session_echo", "arguments": {}},
        session_id="s1",
    )
    assert result["name"] == "session_echo"
    assert "result" in result


@pytest.mark.asyncio
async def test_execute_session_tool_without_session():
    """세션 도구를 session_id 없이 실행하면 찾을 수 없음."""
    registry = ToolRegistry()
    spec = _make_spec("session_echo")
    registry.register(spec, source="generated", session_id="s1")

    result = await registry.execute({"name": "session_echo", "arguments": {}})
    assert "error" in result


# ─── to_openai_tools with names filter + session ───


def test_to_openai_tools_with_names_and_session():
    registry = ToolRegistry()
    registry.register(_make_spec("a"), source="builtin")
    registry.register(_make_spec("b"), source="builtin")
    registry.register(_make_spec("c"), source="generated", session_id="s1")

    tools = registry.to_openai_tools(names=["a", "c"], session_id="s1")
    names = [t["function"]["name"] for t in tools]
    assert "a" in names
    assert "c" in names
    assert "b" not in names
