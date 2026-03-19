"""Human-in-the-Loop 승인 테스트."""

import asyncio
import pytest
from mantis.safety.approval import ApprovalManager, ApprovalStatus


def test_requires_approval_tool_name():
    """도구 이름 패턴 매칭."""
    mgr = ApprovalManager(patterns=["mcp:slack:*", "dangerous_tool"])
    assert mgr.requires_approval("mcp:slack:send_message", {}) is True
    assert mgr.requires_approval("dangerous_tool", {}) is True
    assert mgr.requires_approval("safe_tool", {}) is False


def test_requires_approval_argument_value():
    """인자 값 패턴 매칭 (예: DELETE *)."""
    mgr = ApprovalManager(patterns=["DELETE *"])
    assert mgr.requires_approval("db_query", {"query": "DELETE FROM users WHERE id = 1"}) is True
    assert mgr.requires_approval("db_query", {"query": "SELECT * FROM users"}) is False


@pytest.mark.asyncio
async def test_approval_flow():
    """승인 요청 → 승인 처리 플로우."""
    mgr = ApprovalManager(patterns=["danger"])

    req = await mgr.request_approval(
        session_id="sess1",
        tool_name="danger",
        arguments={"x": 1},
        reason="위험한 도구",
    )
    assert req.status == ApprovalStatus.PENDING

    # 별도 태스크에서 승인
    async def approve_later():
        await asyncio.sleep(0.1)
        mgr.approve(req.request_id, "확인됨")

    asyncio.create_task(approve_later())
    result = await mgr.wait_for_approval(req.request_id, timeout=5)
    assert result.status == ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_approval_reject():
    """승인 거절 플로우."""
    mgr = ApprovalManager(patterns=["danger"])

    req = await mgr.request_approval(
        session_id="sess1",
        tool_name="danger",
        arguments={},
    )

    async def reject_later():
        await asyncio.sleep(0.1)
        mgr.reject(req.request_id, "거절 사유")

    asyncio.create_task(reject_later())
    result = await mgr.wait_for_approval(req.request_id, timeout=5)
    assert result.status == ApprovalStatus.REJECTED
