"""Sandbox 도구화 — DockerSandbox를 Agent가 호출할 수 있는 @tool로 변환.

v2: 모듈 레벨 전역 대신 팩토리 함수로 생성.
sandbox 인스턴스를 받아서 도구 2개를 반환.
ToolRegistry에 등록하면 Agent가 자유롭게 코드 실행 가능.
"""

from __future__ import annotations

from typing import Any

from mantis.sandbox.sandbox import DockerSandbox, SandboxConfig
from mantis.tools.decorator import tool, ToolSpec


def make_sandbox_tools(sandbox: DockerSandbox) -> list[ToolSpec]:
    """DockerSandbox를 Agent가 쓸 수 있는 도구 2개로 변환.

    Returns:
        [execute_code ToolSpec, execute_code_with_test ToolSpec]

    Usage:
        sandbox = DockerSandbox()
        for spec in make_sandbox_tools(sandbox):
            registry.register(spec, source="sandbox")
    """

    @tool(
        name="execute_code",
        description=(
            "Python 코드를 격리된 Docker 컨테이너에서 실행한다. "
            "데이터 분석, 계산, 파일 처리, API 테스트 등에 사용. "
            "타임아웃 최대 120초, 메모리 256MB 제한."
        ),
        parameters={
            "code": {
                "type": "string",
                "description": "실행할 Python 코드 (전체 스크립트)",
            },
            "pip_packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "사전 설치할 pip 패키지 목록 (선택)",
                "optional": True,
            },
            "timeout": {
                "type": "integer",
                "description": "타임아웃 초 (기본 30, 최대 120)",
                "optional": True,
            },
        },
    )
    async def execute_code(
        code: str, timeout: int = 30, pip_packages: list[str] | None = None,
    ) -> dict[str, Any]:
        config = SandboxConfig(
            timeout=min(timeout, 120),
            pip_packages=pip_packages or [],
        )
        sb = DockerSandbox(config)
        result = await sb.execute(code)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }

    @tool(
        name="execute_code_with_test",
        description=(
            "Python 코드와 테스트 코드를 함께 실행하여 검증한다. "
            "코드와 테스트를 하나의 스크립트로 합쳐 실행하며, "
            "테스트 통과 여부를 반환한다."
        ),
        parameters={
            "code": {
                "type": "string",
                "description": "검증할 Python 코드 (함수/클래스 정의)",
            },
            "test_code": {
                "type": "string",
                "description": "테스트 코드 (assert문 또는 검증 로직, 통과 시 print('ALL_TESTS_PASSED'))",
            },
        },
    )
    async def execute_code_with_test(code: str, test_code: str) -> dict[str, Any]:
        combined = code + "\n\n# --- Tests ---\n\n" + test_code
        sb = DockerSandbox(SandboxConfig(timeout=30))
        result = await sb.execute(combined)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "tests_passed": "ALL_TESTS_PASSED" in result.stdout,
        }

    return [execute_code._tool_spec, execute_code_with_test._tool_spec]
