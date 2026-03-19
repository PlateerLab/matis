"""ToolTester — 도구 품질 게이트.

3단계 검증:
  Level 1: validate_schema — 스키마 유효성 (빠름, 샌드박스 불필요)
  Level 2: smoke_test — 더미 값 호출 (중간, 샌드박스 선택)
  Level 3: run_assert_tests / run_pytest — 기능 검증 (느림, 샌드박스 필수)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from mantis.testing.dummy_args import generate_dummy_args
from mantis.testing.pytest_runner import parse_pytest_output, build_pytest_script

logger = logging.getLogger(__name__)

# @tool 데코레이터 모킹 프리앰블 (샌드박스에는 mantis 패키지가 없으므로)
MOCK_PREAMBLE = (
    "def tool(**kwargs):\n"
    "    def decorator(fn):\n"
    "        fn._tool_spec = kwargs\n"
    "        return fn\n"
    "    return decorator\n"
    "\n"
    "import types, sys\n"
    "mock_module = types.ModuleType('mantis.tools.decorator')\n"
    "mock_module.tool = tool\n"
    "sys.modules['mantis'] = types.ModuleType('mantis')\n"
    "sys.modules['mantis.tools'] = types.ModuleType('mantis.tools')\n"
    "sys.modules['mantis.tools.decorator'] = mock_module\n"
    "\n"
)


@dataclass
class TestResult:
    """테스트 결과."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    report: Any = None

    def __bool__(self) -> bool:
        return self.passed


class ToolTester:
    """도구 품질 게이트 — 깨진 도구가 LLM에 전달되는 걸 차단.

    Args:
        sandbox: DockerSandbox 인스턴스 (선택). 없으면 직접 호출.
    """

    def __init__(self, sandbox: Any | None = None):
        self.sandbox = sandbox

    def validate_schema(self, spec: Any) -> TestResult:
        """Level 1: 스키마 검증 — 파라미터 타입, 필수값, description 체크.

        OpenAPI/MCP 도구에 사용. 샌드박스 불필요.
        """
        errors: list[str] = []
        if not spec.description:
            errors.append("도구 description 없음")

        for name, param in spec.parameters.items():
            if not param.get("type"):
                errors.append(f"파라미터 '{name}'에 type 없음")
            if not param.get("description"):
                errors.append(f"파라미터 '{name}'에 description 없음")

        return TestResult(passed=len(errors) == 0, errors=errors)

    async def smoke_test(self, spec: Any) -> TestResult:
        """Level 2: 스모크 테스트 — 더미 값으로 호출, dict 반환 확인.

        @tool 도구 등록 시 자동 실행.
        샌드박스 있으면 격리 실행, 없으면 직접 호출.
        """
        dummy_args = generate_dummy_args(spec.parameters)

        if self.sandbox:
            script = (
                MOCK_PREAMBLE
                + getattr(spec, "source_code", f"# source unavailable for {spec.name}")
                + "\nimport asyncio\n"
                + f"result = asyncio.run({spec.name}(**{dummy_args!r}))\n"
                + 'assert isinstance(result, dict), f"dict 아님: {type(result)}"\n'
                + 'print("SMOKE_OK")\n'
            )
            result = await self.sandbox.execute(script)
            return TestResult(
                passed=result.success and "SMOKE_OK" in result.stdout,
                stdout=result.stdout,
                stderr=result.stderr,
            )

        # 샌드박스 없이 직접 호출
        try:
            result = await spec.execute(**dummy_args)
            return TestResult(passed=isinstance(result, dict))
        except Exception as e:
            return TestResult(
                passed=False,
                errors=[f"스모크 테스트 실패: {e}"],
            )

    async def run_assert_tests(self, code: str, test_code: str) -> TestResult:
        """Level 3a: assert 기반 테스트 — AI가 생성한 테스트 코드 실행.

        create_tool 파이프라인에서 사용. 샌드박스 필수.
        """
        if not self.sandbox:
            return TestResult(passed=False, errors=["샌드박스 없음"])

        script = MOCK_PREAMBLE + code + "\n\n" + test_code + "\n"
        result = await self.sandbox.execute(script)
        return TestResult(
            passed=result.success and "ALL_TESTS_PASSED" in result.stdout,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def run_pytest(self, code: str, test_code: str) -> TestResult:
        """Level 3b: pytest 실행 — 상세 리포트 포함.

        fixture, parametrize 등 고급 테스트가 필요할 때 사용.
        샌드박스 필수.
        """
        if not self.sandbox:
            return TestResult(passed=False, errors=["샌드박스 없음"])

        script = build_pytest_script(code, test_code, MOCK_PREAMBLE)

        # sandbox.execute에 pip_packages와 command를 지원하는 경우
        from mantis.sandbox.sandbox import SandboxConfig

        sandbox_cfg = SandboxConfig(timeout=60, pip_packages=["pytest"])
        from mantis.sandbox.sandbox import DockerSandbox

        sandbox = DockerSandbox(sandbox_cfg)
        result = await sandbox.execute(script)

        report = parse_pytest_output(result.stdout)
        return TestResult(
            passed=result.exit_code == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            report=report,
        )
