"""파라미터 타입별 더미 값 생성."""

from __future__ import annotations

from typing import Any


# 타입별 기본 더미 값
_DEFAULTS: dict[str, Any] = {
    "string": "test_string",
    "number": 0,
    "integer": 0,
    "boolean": False,
    "array": [],
    "object": {},
}


def generate_dummy_args(parameters: dict[str, Any]) -> dict[str, Any]:
    """파라미터 스키마에서 더미 인자를 생성.

    Args:
        parameters: ToolSpec.parameters 형태의 딕셔너리.
            {"param_name": {"type": "string", "description": "..."}, ...}

    Returns:
        {"param_name": 더미값, ...}
    """
    dummy: dict[str, Any] = {}
    for name, spec in parameters.items():
        if spec.get("optional", False):
            continue
        param_type = spec.get("type", "string")

        # enum이 있으면 첫 번째 값 사용
        if spec.get("enum"):
            dummy[name] = spec["enum"][0]
        else:
            dummy[name] = _DEFAULTS.get(param_type, "test_string")

    return dummy
