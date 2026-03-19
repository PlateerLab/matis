"""Sandbox 도구화 (make_sandbox_tools) 테스트."""

from mantis.sandbox.sandbox import DockerSandbox
from mantis.sandbox.tools import make_sandbox_tools
from mantis.tools.registry import ToolRegistry


def test_make_sandbox_tools_returns_two_specs():
    sandbox = DockerSandbox()
    specs = make_sandbox_tools(sandbox)
    assert len(specs) == 2
    names = [s.name for s in specs]
    assert "execute_code" in names
    assert "execute_code_with_test" in names


def test_sandbox_tools_have_correct_schema():
    sandbox = DockerSandbox()
    specs = make_sandbox_tools(sandbox)

    execute_code = next(s for s in specs if s.name == "execute_code")
    schema = execute_code.to_openai_schema()
    assert schema["type"] == "function"
    assert "code" in schema["function"]["parameters"]["properties"]
    assert "pip_packages" in schema["function"]["parameters"]["properties"]
    assert "timeout" in schema["function"]["parameters"]["properties"]

    execute_test = next(s for s in specs if s.name == "execute_code_with_test")
    schema = execute_test.to_openai_schema()
    assert "code" in schema["function"]["parameters"]["properties"]
    assert "test_code" in schema["function"]["parameters"]["properties"]


def test_sandbox_tools_register_to_registry():
    sandbox = DockerSandbox()
    registry = ToolRegistry()

    for spec in make_sandbox_tools(sandbox):
        registry.register(spec, source="sandbox")

    assert registry.get("execute_code") is not None
    assert registry.get("execute_code_with_test") is not None
    assert registry.get_source("execute_code") == "sandbox"
    assert registry.get_source("execute_code_with_test") == "sandbox"

    tools = registry.to_openai_tools()
    assert len(tools) == 2


def test_sandbox_tools_are_async():
    sandbox = DockerSandbox()
    specs = make_sandbox_tools(sandbox)
    for spec in specs:
        assert spec.is_async is True
