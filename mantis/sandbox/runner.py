"""코드 실행 엔진 — Sandbox를 @tool로 노출."""

from __future__ import annotations

import logging
from typing import Any

from mantis.sandbox.sandbox import DockerSandbox, SandboxConfig, SandboxResult
from mantis.tools.decorator import tool

logger = logging.getLogger(__name__)

# 모듈 레벨 샌드박스 인스턴스 (lazy init)
_sandbox: DockerSandbox | None = None


def get_sandbox(config: SandboxConfig | None = None) -> DockerSandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = DockerSandbox(config)
    return _sandbox


@tool(
    name="execute_code",
    description="Python 코드를 Docker 샌드박스에서 격리 실행한다. 코드 문자열을 받아 stdout/stderr/exit_code를 반환한다. 타임아웃 30초, 메모리 256MB 제한. 안전한 환경에서 실행되므로 자유롭게 테스트할 수 있다.",
    parameters={
        "code": {
            "type": "string",
            "description": "실행할 Python 코드 (전체 스크립트)",
        },
        "timeout": {
            "type": "number",
            "description": "타임아웃 초 (기본 30초, 최대 120초)",
            "optional": True,
        },
        "pip_packages": {
            "type": "array",
            "description": "사전 설치할 pip 패키지 목록 (예: ['pandas', 'requests'])",
            "optional": True,
        },
    },
)
async def execute_code(
    code: str,
    timeout: int = 30,
    pip_packages: list[str] | None = None,
) -> dict[str, Any]:
    # 타임아웃 상한
    timeout = min(timeout, 120)

    config = SandboxConfig(
        timeout=timeout,
        pip_packages=pip_packages or [],
    )
    sandbox = DockerSandbox(config)

    result = await sandbox.execute(code)
    return result.to_dict()


@tool(
    name="execute_code_with_test",
    description="Python 코드를 샌드박스에서 실행하고 테스트 코드도 함께 실행한다. 코드와 테스트를 하나의 스크립트로 합쳐서 실행하며, 테스트 통과 여부를 반환한다.",
    parameters={
        "code": {
            "type": "string",
            "description": "테스트할 Python 코드 (함수/클래스 정의)",
        },
        "test_code": {
            "type": "string",
            "description": "테스트 코드 (assert문 또는 검증 로직)",
        },
    },
)
async def execute_code_with_test(code: str, test_code: str) -> dict[str, Any]:
    combined = f"""{code}

# ── 테스트 ──
{test_code}
print("\\n✅ 모든 테스트 통과")
"""
    sandbox = DockerSandbox(SandboxConfig(timeout=30))
    result = await sandbox.execute(combined)

    return {
        **result.to_dict(),
        "tests_passed": result.success and "모든 테스트 통과" in result.stdout,
    }
