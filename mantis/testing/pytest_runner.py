"""샌드박스 안에서 pytest 실행 + 출력 파싱."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PytestReport:
    """pytest 실행 결과 파싱 리포트."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    warnings: int = 0
    test_results: list[dict[str, Any]] = field(default_factory=list)


def parse_pytest_output(stdout: str) -> PytestReport:
    """pytest -v 출력을 파싱하여 구조화된 리포트 반환.

    Args:
        stdout: pytest -v 실행 stdout.

    Returns:
        PytestReport 인스턴스.
    """
    report = PytestReport()

    # 개별 테스트 결과 파싱: test_name PASSED/FAILED
    for match in re.finditer(r"(\S+::?\S+)\s+(PASSED|FAILED|ERROR)", stdout):
        name, status = match.group(1), match.group(2)
        report.test_results.append({"name": name, "status": status.lower()})
        if status == "PASSED":
            report.passed += 1
        elif status == "FAILED":
            report.failed += 1
        elif status == "ERROR":
            report.errors += 1

    report.total = report.passed + report.failed + report.errors

    # 요약 줄 파싱: "= 3 passed, 1 failed in 0.5s ="
    summary = re.search(
        r"=+\s*(.*?)\s*=+\s*$",
        stdout,
        re.MULTILINE,
    )
    if summary:
        summary_text = summary.group(1)
        warn_match = re.search(r"(\d+)\s+warning", summary_text)
        if warn_match:
            report.warnings = int(warn_match.group(1))

    return report


def build_pytest_script(code: str, test_code: str, mock_preamble: str = "") -> str:
    """pytest 실행용 스크립트 조립.

    Args:
        code: 도구 코드.
        test_code: 테스트 코드.
        mock_preamble: @tool 데코레이터 모킹 코드.

    Returns:
        완전한 Python 스크립트.
    """
    return f"{mock_preamble}\n{code}\n\n{test_code}\n"
