"""python -m mantis CLI 진입점."""

from __future__ import annotations

import argparse
import sys

from mantis import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mantis",
        description="XTool — AI Agent 실행 엔진 라이브러리",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"mantis {__version__}",
    )

    sub = parser.add_subparsers(dest="command")

    # mantis info
    sub.add_parser("info", help="설치된 모듈 정보 출력")

    args = parser.parse_args(argv)

    if args.command == "info":
        _print_info()
        return 0

    parser.print_help()
    return 0


def _print_info() -> None:
    """설치된 선택 모듈 상태 출력."""
    print(f"mantis {__version__}")
    print()

    checks = [
        ("httpx", "httpx", "필수 — HTTP 클라이언트"),
        ("graph-tool-call", "graph_tool_call", "선택 — 도구 검색 (pip install mantis[search])"),
        ("docker", "docker", "선택 — 샌드박스 (pip install mantis[sandbox])"),
        ("asyncpg", "asyncpg", "선택 — 상태 저장 (pip install mantis[state])"),
    ]

    for name, module, desc in checks:
        try:
            mod = __import__(module)
            ver = getattr(mod, "__version__", "?")
            print(f"  [O] {name} {ver} — {desc}")
        except ImportError:
            print(f"  [X] {name} — {desc}")


if __name__ == "__main__":
    sys.exit(main())
