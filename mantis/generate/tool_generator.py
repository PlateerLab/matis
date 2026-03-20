"""AI 도구 생성기 — LLM이 코드를 생성하고 샌드박스에서 테스트 후 등록."""

from __future__ import annotations

import logging
import re
import textwrap
import uuid
from pathlib import Path
from typing import Any

from mantis.llm.protocol import LLMProvider
from mantis.sandbox.sandbox import DockerSandbox, SandboxConfig
from mantis.tools.decorator import tool, ToolSpec
from mantis.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ToolGenerator 인스턴스 참조 (app.py에서 주입)
_generator_ref: "ToolGenerator | None" = None


def set_tool_generator(generator: "ToolGenerator") -> None:
    """app.py startup에서 ToolGenerator 인스턴스를 주입."""
    global _generator_ref
    _generator_ref = generator


TOOL_GENERATION_PROMPT = """\
사용자가 요청한 도구를 Python 코드로 작성하고, 테스트 코드도 함께 작성해줘.

**규칙:**
1. 반드시 `@tool` 데코레이터를 사용해야 한다
2. `from mantis.tools.decorator import tool` import 필수
3. async 함수여야 한다
4. description은 AI가 언제 이 도구를 사용할지 판단할 수 있도록 구체적으로 작성
5. 반환값은 dict 형태

**출력 형식 — 반드시 코드 블록 2개를 출력해:**

첫 번째 블록 — 도구 코드:
```python
from mantis.tools.decorator import tool

@tool(
    name="도구명",
    description="설명",
    parameters={{...}}
)
async def 함수명(...) -> dict:
    return {{...}}
```

두 번째 블록 — 테스트 코드 (assert로 검증):
```test
import asyncio

async def test():
    result = await 함수명(인자)
    assert "키" in result, "결과에 키가 없음"
    assert result["키"] == 기대값, f"기대: 기대값, 실제: {{result['키']}}"
    print("ALL_TESTS_PASSED")

asyncio.run(test())
```

**예시:**

도구 코드:
```python
from mantis.tools.decorator import tool

@tool(
    name="calculate_sum",
    description="두 숫자의 합을 계산한다. a와 b를 받아 합계를 반환한다.",
    parameters={{
        "a": {{"type": "number", "description": "첫 번째 숫자"}},
        "b": {{"type": "number", "description": "두 번째 숫자"}},
    }}
)
async def calculate_sum(a: float, b: float) -> dict:
    return {{"sum": a + b}}
```

테스트 코드:
```test
import asyncio

async def test():
    result = await calculate_sum(3, 5)
    assert "sum" in result, "결과에 sum 키 없음"
    assert result["sum"] == 8, f"기대: 8, 실제: {{result['sum']}}"
    result2 = await calculate_sum(-1, 1)
    assert result2["sum"] == 0, f"기대: 0, 실제: {{result2['sum']}}"
    print("ALL_TESTS_PASSED")

asyncio.run(test())
```

**사용자 요청:**
{request}
"""


class ToolGenerator:
    """AI가 도구를 생성 → 테스트 → 등록하는 파이프라인.

    1. LLM에게 코드 생성 요청
    2. 샌드박스에서 문법/실행 테스트
    3. 통과하면 Tool Registry에 등록
    4. 도구 파일을 tools/ 디렉토리에 저장
    """

    def __init__(
        self,
        model_client: LLMProvider,
        tool_registry: ToolRegistry,
        tools_dir: str | Path = "tools",
    ):
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    async def generate(self, request: str, session_id: str | None = None) -> dict[str, Any]:
        """사용자 요청에서 도구 생성.

        파이프라인: LLM 코드생성 → 문법검증 → 기능테스트 → 파일저장 → Registry 등록

        v2: session_id가 있으면 해당 세션에 등록 (즉시 사용 가능).

        Returns:
            {"status": "success|failed", "name": "도구명", "code": "코드", ...}
        """
        logger.info("[ToolGenerator] 도구 생성 시작: %s", request[:80])

        # Step 1: LLM에게 코드 + 테스트 코드 생성 요청
        gen_result = await self._generate_code(request)
        if not gen_result:
            logger.error("[ToolGenerator] LLM 코드 생성 실패")
            return {"status": "failed", "error": "코드 생성 실패"}

        code = gen_result["code"]
        test_code = gen_result.get("test_code", "")
        logger.info("[ToolGenerator] 코드 생성 완료 (code=%d자, test=%d자)", len(code), len(test_code))

        # Step 2: 코드에서 도구 이름 추출
        tool_name = self._extract_tool_name(code)
        if not tool_name:
            logger.error("[ToolGenerator] 도구 이름 추출 실패")
            return {"status": "failed", "error": "도구 이름을 추출할 수 없음", "code": code}

        logger.info("[ToolGenerator] 도구명: %s", tool_name)

        # Step 3: 샌드박스에서 문법 검증
        syntax_result = await self._test_syntax(code)
        if not syntax_result["passed"]:
            logger.error("[ToolGenerator] 문법 검증 실패: stderr=%s", syntax_result.get("stderr", ""))
            return {
                "status": "failed",
                "error": "문법 검증 실패",
                "code": code,
                "test_output": syntax_result,
            }
        logger.info("[ToolGenerator] 문법 검증 통과 ✓")

        # Step 4: 샌드박스에서 기능 테스트 (테스트 코드가 있을 때)
        if test_code:
            func_result = await self._test_functional(code, test_code)
            if not func_result["passed"]:
                logger.error(
                    "[ToolGenerator] 기능 테스트 실패: stdout=%s, stderr=%s",
                    func_result.get("stdout", "")[:200],
                    func_result.get("stderr", "")[:200],
                )
                return {
                    "status": "failed",
                    "error": "기능 테스트 실패",
                    "code": code,
                    "test_code": test_code,
                    "test_output": func_result,
                }
            logger.info("[ToolGenerator] 기능 테스트 통과 ✓ — stdout: %s", func_result.get("stdout", "").strip()[:100])
        else:
            logger.warning("[ToolGenerator] 테스트 코드 없음 — 문법 검증만 수행")

        # Step 5: 파일 저장
        file_path = self.tools_dir / f"{tool_name}.py"
        file_path.write_text(code, encoding="utf-8")
        logger.info("[ToolGenerator] 파일 저장: %s", file_path)

        # Step 6: Registry에 등록 (v2: source + session_id)
        try:
            loaded = self.tool_registry.load_from_file(
                file_path, source="generated", session_id=session_id,
            )
            logger.info("[ToolGenerator] Registry 등록 완료: %s (loaded=%d, session=%s)", tool_name, loaded, session_id)
            return {
                "status": "success",
                "name": tool_name,
                "code": code,
                "test_code": test_code,
                "file": str(file_path),
                "loaded": loaded,
            }
        except Exception as e:
            logger.error("[ToolGenerator] Registry 등록 실패: %s", e)
            return {
                "status": "failed",
                "error": f"레지스트리 등록 실패: {e}",
                "code": code,
                "file": str(file_path),
            }

    async def _generate_code(self, request: str) -> dict | None:
        """LLM에게 코드 + 테스트 코드 생성 요청.

        Returns:
            {"code": "도구코드", "test_code": "테스트코드"} 또는 None
        """
        prompt = TOOL_GENERATION_PROMPT.format(request=request)

        response = await self.model_client.generate(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

        if not response.text:
            return None

        code, test_code = self._extract_code_and_test(response.text)
        if not code:
            return None

        return {"code": code, "test_code": test_code}

    # ── 샌드박스 공통 ──

    _MOCK_PREAMBLE = (
        "# @tool 데코레이터 모킹 (샌드박스에는 src 패키지가 없으므로)\n"
        "def tool(**kwargs):\n"
        "    def decorator(fn):\n"
        "        fn._tool_spec = kwargs\n"
        "        return fn\n"
        "    return decorator\n"
        "\n"
        "import types, sys\n"
        "mock_module = types.ModuleType('src.tools.decorator')\n"
        "mock_module.tool = tool\n"
        "sys.modules['mantis'] = types.ModuleType('mantis')\n"
        "sys.modules['mantis.tools'] = types.ModuleType('mantis.tools')\n"
        "sys.modules['mantis.tools.decorator'] = mock_module\n"
        "\n"
    )

    async def _test_syntax(self, code: str) -> dict:
        """샌드박스에서 코드 문법/import 검증."""
        script = self._MOCK_PREAMBLE + code + "\n\nprint('SYNTAX_OK')\n"

        sandbox = DockerSandbox(SandboxConfig(timeout=15))
        result = await sandbox.execute(script)

        return {
            "passed": result.success and "SYNTAX_OK" in result.stdout,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    async def _test_functional(self, code: str, test_code: str) -> dict:
        """샌드박스에서 도구 코드 + 테스트 코드를 함께 실행하여 기능 검증.

        테스트 코드에서 ALL_TESTS_PASSED를 출력해야 통과.
        """
        script = self._MOCK_PREAMBLE + code + "\n\n" + test_code + "\n"

        sandbox = DockerSandbox(SandboxConfig(timeout=30))
        result = await sandbox.execute(script)

        return {
            "passed": result.success and "ALL_TESTS_PASSED" in result.stdout,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    # ── 파싱 유틸 ──

    def _extract_code_and_test(self, text: str) -> tuple[str, str]:
        """LLM 응답에서 도구 코드와 테스트 코드를 분리 추출.

        Returns:
            (tool_code, test_code) — test_code는 없을 수 있음
        """
        # ```python ... ``` 블록 = 도구 코드
        # ```test ... ``` 블록 = 테스트 코드
        tool_code = ""
        test_code = ""

        # 모든 코드 블록 추출
        blocks = re.findall(r"```(\w*)\s*\n(.*?)```", text, re.DOTALL)

        for lang, content in blocks:
            content = content.strip()
            if lang == "test":
                test_code = content
            elif lang in ("python", "") and not tool_code:
                # 첫 번째 python 블록이 도구 코드
                tool_code = content

        # 코드 블록이 하나도 없으면 전체 텍스트를 코드로 간주
        if not tool_code and not blocks:
            tool_code = text.strip()

        return tool_code, test_code

    def _extract_tool_name(self, code: str) -> str | None:
        """코드에서 @tool(name="...") 추출."""
        match = re.search(r'@tool\([^)]*name\s*=\s*["\']([^"\']+)["\']', code)
        if match:
            return match.group(1)
        return None



# ─── v1 호환: 모듈 레벨 create_tool (기존 코드 유지) ───

@tool(
    name="create_tool",
    description="사용자의 자연어 설명을 바탕으로 새로운 도구(@tool)를 AI가 자동 생성한다. 코드를 생성하고 샌드박스에서 테스트한 뒤 Tool Registry에 등록한다.",
    parameters={
        "description": {
            "type": "string",
            "description": "만들고 싶은 도구에 대한 자연어 설명",
        },
    },
)
async def create_tool(description: str) -> dict:
    """AI가 코드 생성 → 샌드박스 테스트 → Registry 등록까지 e2e 실행."""
    if not _generator_ref:
        return {
            "status": "error",
            "error": "ToolGenerator 미초기화 — set_tool_generator() 필요",
        }

    result = await _generator_ref.generate(description)
    return result


# ─── v2: 팩토리 함수 — ToolGenerator 인스턴스를 받아서 도구로 변환 ───

def make_create_tool(generator: ToolGenerator) -> ToolSpec:
    """ToolGenerator를 Agent가 호출할 수 있는 @tool로 변환.

    v2 패턴: 모듈 전역 대신 팩토리 함수로 생성.
    session_id는 Agent가 _session_id로 관리하므로, 여기서는 None으로 등록.

    Usage:
        generator = ToolGenerator(llm=llm, registry=registry, sandbox=sandbox)
        spec = make_create_tool(generator)
        registry.register(spec, source="builtin")
    """

    @tool(
        name="create_tool",
        description=(
            "새로운 도구를 만든다. 설명을 받아 Python 코드를 생성하고, "
            "Docker 컨테이너에서 테스트한 후 등록한다. "
            "등록 즉시 다음 호출부터 사용 가능."
        ),
        parameters={
            "description": {
                "type": "string",
                "description": "만들 도구의 기능 설명",
            },
        },
    )
    async def _create_tool(description: str) -> dict:
        result = await generator.generate(description)
        return result

    return _create_tool._tool_spec
