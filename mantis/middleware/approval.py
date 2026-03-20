"""승인 미들웨어 — ApprovalManager를 Agent 루프에 통합."""

from __future__ import annotations

import logging
from typing import Any

from mantis.middleware.base import BaseMiddleware, RunContext
from mantis.safety.approval import ApprovalManager, ApprovalStatus

logger = logging.getLogger(__name__)


class ApprovalMiddleware(BaseMiddleware):
    """위험 도구 호출에 대한 Human-in-the-Loop 승인 미들웨어.

    패턴에 매칭되는 도구 호출은 승인을 요청하고 대기한다.
    거절 시 block_reason을 반환하여 실행을 차단한다.

    사용법:
        mw = ApprovalMiddleware(patterns=["db_delete*", "DROP *"])
        runner.add_middleware(mw)
    """

    def __init__(
        self,
        patterns: list[str],
        *,
        timeout: float = 300.0,
        manager: ApprovalManager | None = None,
    ):
        self._manager = manager or ApprovalManager(patterns=patterns)
        self._patterns = patterns
        self._timeout = timeout

    @property
    def manager(self) -> ApprovalManager:
        """내부 ApprovalManager 접근 (외부에서 approve/reject 호출용)."""
        return self._manager

    async def on_before_tool(
        self, ctx: RunContext, tool_name: str, arguments: dict
    ) -> tuple[str, dict, str | None]:
        """도구 호출 전 승인 확인.

        패턴 매칭 시 승인 요청을 생성하고, 승인/거절/만료까지 대기한다.
        """
        if not self._manager.requires_approval(tool_name, arguments):
            return tool_name, arguments, None

        logger.info(
            "승인 필요: session=%s, tool=%s", ctx.session_id, tool_name
        )

        # 승인 요청 생성
        request = await self._manager.request_approval(
            session_id=ctx.session_id,
            tool_name=tool_name,
            arguments=arguments,
            reason=f"도구 '{tool_name}' 실행에 승인이 필요합니다.",
        )

        # 승인 대기
        result = await self._manager.wait_for_approval(
            request.request_id, timeout=self._timeout
        )

        if result.status == ApprovalStatus.APPROVED:
            logger.info("승인됨: tool=%s, request=%s", tool_name, request.request_id)
            return tool_name, arguments, None

        # 거절 또는 만료
        reason = (
            f"도구 '{tool_name}' 실행이 {result.status.value}되었습니다."
        )
        if result.result:
            reason += f" 사유: {result.result}"

        logger.warning(
            "차단: tool=%s, status=%s, reason=%s",
            tool_name, result.status.value, reason,
        )
        return tool_name, arguments, reason
