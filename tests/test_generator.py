"""AI 도구 생성기 단위 테스트 (네트워크 없이)."""

from mantis.generate.tool_generator import ToolGenerator
from mantis.tools.registry import ToolRegistry
from mantis.llm.openai_provider import ModelClient


def _make_generator():
    client = ModelClient(base_url="http://fake", model="test")
    registry = ToolRegistry()
    return ToolGenerator(client, registry, tools_dir="tools")


# ── _extract_code_and_test ──


def test_extract_code_and_test_python_block():
    """python 코드 블록 추출."""
    gen = _make_generator()

    text = '''여기 코드입니다:
```python
print("hello")
```
끝.'''
    code, test = gen._extract_code_and_test(text)
    assert code == 'print("hello")'
    assert test == ""


def test_extract_code_and_test_no_language():
    """언어 표시 없는 코드 블록."""
    gen = _make_generator()

    text = '''```
x = 1 + 2
```'''
    code, test = gen._extract_code_and_test(text)
    assert code == "x = 1 + 2"
    assert test == ""


def test_extract_code_and_test_plain_text():
    """코드 블록이 없으면 전체 텍스트 반환."""
    gen = _make_generator()
    code, test = gen._extract_code_and_test("just plain code")
    assert code == "just plain code"
    assert test == ""


def test_extract_code_and_test_with_test_block():
    """python + test 블록 분리 추출."""
    gen = _make_generator()

    text = '''도구 코드:
```python
from src.tools.decorator import tool

@tool(name="add", description="더하기", parameters={})
async def add(a: float, b: float) -> dict:
    return {"sum": a + b}
```

테스트 코드:
```test
import asyncio

async def test():
    result = await add(1, 2)
    assert result["sum"] == 3
    print("ALL_TESTS_PASSED")

asyncio.run(test())
```'''
    code, test = gen._extract_code_and_test(text)
    assert "@tool" in code
    assert "async def add" in code
    assert "ALL_TESTS_PASSED" in test
    assert "asyncio.run" in test


# ── _extract_tool_name ──


def test_extract_tool_name():
    """@tool(name=...) 추출."""
    gen = _make_generator()

    code = '''
from src.tools.decorator import tool

@tool(
    name="my_tool",
    description="테스트",
    parameters={}
)
async def my_tool():
    pass
'''
    assert gen._extract_tool_name(code) == "my_tool"


def test_extract_tool_name_single_quotes():
    """작은따옴표 name."""
    gen = _make_generator()
    code = "@tool(name='another_tool', description='설명')"
    assert gen._extract_tool_name(code) == "another_tool"


def test_extract_tool_name_not_found():
    """@tool이 없는 코드."""
    gen = _make_generator()
    assert gen._extract_tool_name("def hello(): pass") is None
