"""Human-in-the-Loop — 위험 액션 승인 관리."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    """승인 요청."""

    request_id: str
    session_id: str
    action: str           # 실행하려는 도구/쿼리
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    result: Any = None    # 승인/거절 시 사유

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "action": self.action,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "reason": self.reason,
            "status": self.status.value,
        }


class ApprovalManager:
    """승인 대기/처리 관리.

    approval_required 패턴과 매칭되는 도구 호출은
    즉시 실행하지 않고 승인 대기 상태로 전환한다.
    """

    def __init__(self, patterns: list[str] | None = None):
        self._patterns = patterns or []
        self._pending: dict[str, ApprovalRequest] = {}
        self._events: dict[str, asyncio.Event] = {}

    def requires_approval(self, tool_name: str, arguments: dict) -> bool:
        """해당 도구 호출이 승인이 필요한지 확인."""
        for pattern in self._patterns:
            # 패턴 형식: "tool_name" 또는 "tool_name:action"
            if fnmatch.fnmatch(tool_name, pattern):
                return True
            # arguments 안의 특정 값 매칭 (예: "DELETE *")
            for value in arguments.values():
                if isinstance(value, str) and fnmatch.fnmatch(value.upper(), pattern.upper()):
                    return True
        return False

    async def request_approval(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str = "",
    ) -> ApprovalRequest:
        """승인 요청 생성 및 대기 상태 등록."""
        request_id = f"apr_{uuid.uuid4().hex[:8]}"
        action = f"{tool_name}({arguments})"

        req = ApprovalRequest(
            request_id=request_id,
            session_id=session_id,
            action=action,
            tool_name=tool_name,
            arguments=arguments,
            reason=reason or f"도구 '{tool_name}' 실행에 승인이 필요합니다.",
        )

        self._pending[request_id] = req
        self._events[request_id] = asyncio.Event()

        logger.info("승인 요청 생성: %s — %s", request_id, action)
        return req

    async def wait_for_approval(
        self, request_id: str, timeout: float = 300.0
    ) -> ApprovalRequest:
        """승인/거절될 때까지 대기.

        Args:
            timeout: 최대 대기 시간 (초). 초과 시 EXPIRED.
        """
        event = self._events.get(request_id)
        if not event:
            raise ValueError(f"승인 요청 없음: {request_id}")

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            req = self._pending[request_id]
            req.status = ApprovalStatus.EXPIRED
            logger.warning("승인 요청 만료: %s", request_id)

        return self._pending[request_id]

    def approve(self, request_id: str, reason: str = "") -> bool:
        """승인 처리."""
        req = self._pending.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False

        req.status = ApprovalStatus.APPROVED
        req.result = reason
        self._events[request_id].set()
        logger.info("승인됨: %s", request_id)
        return True

    def reject(self, request_id: str, reason: str = "") -> bool:
        """거절 처리."""
        req = self._pending.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False

        req.status = ApprovalStatus.REJECTED
        req.result = reason
        self._events[request_id].set()
        logger.info("거절됨: %s — %s", request_id, reason)
        return True

    def list_pending(self, session_id: str | None = None) -> list[ApprovalRequest]:
        """대기 중인 승인 요청 목록."""
        reqs = [r for r in self._pending.values() if r.status == ApprovalStatus.PENDING]
        if session_id:
            reqs = [r for r in reqs if r.session_id == session_id]
        return reqs

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        return self._pending.get(request_id)
