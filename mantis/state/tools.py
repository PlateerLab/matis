"""State 도구화 — StateStore를 Agent가 호출할 수 있는 @tool로 변환.

v3: manage_session 도구. Agent가 세션 상태를 직접 조회/삭제할 수 있다.
미들웨어(StateMiddleware)는 자동 체크포인트를 담당하고,
이 도구는 Agent가 명시적으로 세션을 관리할 때 사용한다.
"""

from __future__ import annotations

from typing import Any

from mantis.tools.decorator import tool, ToolSpec


def make_state_tools(store: Any) -> list[ToolSpec]:
    """StateStore를 Agent가 사용할 수 있는 도구로 변환.

    Returns:
        [manage_session ToolSpec]

    Usage:
        store = StateStore("postgresql://...")
        await store.initialize()
        for spec in make_state_tools(store):
            registry.register(spec, source="builtin")
    """

    @tool(
        name="manage_session",
        description=(
            "세션 상태를 관리한다. 세션 복구(resume), 삭제(delete), "
            "목록 조회(list)를 수행할 수 있다. "
            "이전 대화를 이어가거나, 세션을 정리할 때 사용."
        ),
        parameters={
            "action": {
                "type": "string",
                "description": "수행할 작업: resume, delete, list",
                "enum": ["resume", "delete", "list"],
            },
            "session_id": {
                "type": "string",
                "description": "대상 세션 ID (resume, delete 시 필수)",
                "optional": True,
            },
        },
    )
    async def manage_session(
        action: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if action == "resume":
            if not session_id:
                return {"status": "error", "error": "session_id가 필요합니다"}
            state = await store.resume(session_id)
            if state:
                return {"status": "success", "session_id": session_id, "state": state}
            return {"status": "not_found", "session_id": session_id}

        elif action == "delete":
            if not session_id:
                return {"status": "error", "error": "session_id가 필요합니다"}
            deleted = await store.delete(session_id)
            return {
                "status": "success" if deleted else "not_found",
                "session_id": session_id,
                "deleted": deleted,
            }

        elif action == "list":
            sessions = await store.list_sessions()
            return {
                "status": "success",
                "count": len(sessions),
                "sessions": [
                    {
                        "session_id": s.get("session_id", s.get("id")),
                        "updated_at": str(s.get("updated_at", "")),
                    }
                    for s in sessions
                ],
            }

        else:
            return {"status": "error", "error": f"알 수 없는 action: {action}"}

    return [manage_session._tool_spec]
