"""Sandbox 단위 테스트 — Docker 없이 테스트 가능한 부분."""

import pytest
from mantis.sandbox.sandbox import SandboxConfig, SandboxResult


def test_sandbox_config_defaults():
    """기본 설정 값."""
    config = SandboxConfig()
    assert config.image == "python:3.12-slim"
    assert config.timeout == 30
    assert config.memory_limit == "256m"
    assert config.network == "none"


def test_sandbox_result_success():
    """성공 결과."""
    result = SandboxResult(stdout="hello", exit_code=0, duration_ms=100)
    assert result.success is True
    d = result.to_dict()
    assert d["exit_code"] == 0
    assert d["stdout"] == "hello"


def test_sandbox_result_failure():
    """실패 결과."""
    result = SandboxResult(stderr="error", exit_code=1, duration_ms=50)
    assert result.success is False


def test_sandbox_result_timeout():
    """타임아웃 결과."""
    result = SandboxResult(timed_out=True, exit_code=-1)
    assert result.success is False
