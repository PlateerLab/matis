"""Tool Registry — 모든 도구를 통합 관리."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Any

from mantis.tools.decorator import ToolSpec

logger = logging.getLogger(__name__)


class ToolRegistry:
    """도구 등록/조회/실행을 관리하는 중앙 레지스트리.

    v2: 세션 스코프 + 소스 추적.
    - session_id를 안 쓰면 v1과 동일하게 동작 (하위 호환).
    - session_id를 쓰면 해당 세션에서만 보이는 도구 관리.
    """

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._sources: dict[str, str] = {}
        self._session_tools: dict[str, dict[str, ToolSpec]] = {}

    def register(
        self,
        spec: ToolSpec,
        source: str = "manual",
        session_id: str | None = None,
    ) -> None:
        """도구 하나 등록.

        session_id가 있으면 해당 세션에서만 보이는 도구.
        없으면 글로벌 (모든 세션에서 사용).
        """
        if session_id:
            self._session_tools.setdefault(session_id, {})[spec.name] = spec
        else:
            if spec.name in self._tools:
                logger.warning("도구 '%s' 덮어씀", spec.name)
            self._tools[spec.name] = spec
        self._sources[spec.name] = source
        logger.info("도구 등록: %s (source=%s, session=%s)", spec.name, source, session_id)

    def unregister(self, name: str) -> bool:
        """도구 제거. 성공 시 True."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str, session_id: str | None = None) -> ToolSpec | None:
        """이름으로 도구 조회. 세션 도구 우선."""
        if session_id and session_id in self._session_tools:
            spec = self._session_tools[session_id].get(name)
            if spec:
                return spec
        return self._tools.get(name)

    def get_source(self, name: str) -> str | None:
        """도구의 소스 반환."""
        return self._sources.get(name)

    def list_tools(self, session_id: str | None = None) -> list[ToolSpec]:
        """등록된 전체 도구 목록. 글로벌 + 세션 도구 합산."""
        tools = dict(self._tools)
        if session_id and session_id in self._session_tools:
            tools.update(self._session_tools[session_id])
        return list(tools.values())

    def list_names(self, session_id: str | None = None) -> list[str]:
        """등록된 도구 이름 목록."""
        tools = dict(self._tools)
        if session_id and session_id in self._session_tools:
            tools.update(self._session_tools[session_id])
        return list(tools.keys())

    def to_openai_tools(
        self,
        names: list[str] | None = None,
        session_id: str | None = None,
    ) -> list[dict]:
        """LLM에 넘길 tools 파라미터 생성.

        매 iteration마다 호출하면 최신 도구 목록 반환.
        create_tool로 등록된 도구가 즉시 포함됨.
        """
        tools = dict(self._tools)
        if session_id and session_id in self._session_tools:
            tools.update(self._session_tools[session_id])
        specs = tools.values()
        if names:
            specs = [t for t in specs if t.name in names]
        return [t.to_openai_schema() for t in specs]

    def cleanup_session(self, session_id: str) -> int:
        """세션 종료 시 세션 스코프 도구 정리. 제거된 도구 수 반환."""
        removed = self._session_tools.pop(session_id, {})
        if removed:
            logger.info("세션 '%s' 도구 %d개 정리: %s", session_id, len(removed), list(removed.keys()))
        return len(removed)

    async def execute(self, tool_call: dict, session_id: str | None = None) -> dict:
        """도구 호출 실행.

        Args:
            tool_call: {"name": "도구명", "arguments": {파라미터}}
            session_id: 세션 ID (세션 도구 포함 조회)

        Returns:
            {"name": "도구명", "result": 결과} 또는 {"name": "도구명", "error": 에러}
        """
        name = tool_call.get("name", "")
        arguments = tool_call.get("arguments", {})

        spec = self.get(name, session_id=session_id)
        if not spec:
            return {"name": name, "error": f"도구 '{name}'을 찾을 수 없습니다."}

        try:
            result = await spec.execute(**arguments)
            return {"name": name, "result": result}
        except Exception as e:
            logger.exception("도구 '%s' 실행 실패", name)
            return {"name": name, "error": str(e)}

    def _collect_tools_from_module(self, module: Any) -> list[ToolSpec]:
        """모듈에서 _tool_spec이 부착된 함수를 수집."""
        specs = []
        for obj in vars(module).values():
            spec = getattr(obj, '_tool_spec', None)
            if isinstance(spec, ToolSpec):
                specs.append(spec)
        return specs

    def load_from_module(self, module_path: str) -> int:
        """Python 모듈에서 @tool이 붙은 도구를 자동 로드.

        Returns:
            로드된 도구 수.
        """
        before = set(self._tools.keys())
        module = importlib.import_module(module_path)
        for spec in self._collect_tools_from_module(module):
            if spec.name not in before:
                self.register(spec)
        return len(self._tools) - len(before)

    def load_from_file(
        self,
        file_path: str | Path,
        source: str = "file",
        session_id: str | None = None,
    ) -> int:
        """파일 경로에서 @tool이 붙은 도구를 자동 로드.

        Returns:
            로드된 도구 수.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"파일 없음: {file_path}")

        before = set(self.list_names(session_id=session_id))

        spec_obj = importlib.util.spec_from_file_location(
            f"xgen_tool_{file_path.stem}", file_path
        )
        count = 0
        if spec_obj and spec_obj.loader:
            module = importlib.util.module_from_spec(spec_obj)
            spec_obj.loader.exec_module(module)
            for tool_spec in self._collect_tools_from_module(module):
                if tool_spec.name not in before:
                    self.register(tool_spec, source=source, session_id=session_id)
                    count += 1

        return count

    def load_from_directory(self, dir_path: str | Path) -> int:
        """디렉토리 내 모든 .py 파일에서 도구 로드.

        Returns:
            총 로드된 도구 수.
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"디렉토리 아님: {dir_path}")

        total = 0
        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                total += self.load_from_file(py_file)
            except Exception:
                logger.exception("도구 파일 로드 실패: %s", py_file)
        return total
