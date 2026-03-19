"""SSE 이벤트 어댑터 — xgen-agent 내부 이벤트를 xgen-workflow SSE 포맷으로 변환.

xgen-workflow SSE 이벤트 타입:
  - event: log        → 실행 로그
  - event: node_status → 노드 상태
  - event: tool       → 도구 호출/결과/에러
  - data: {"type": "data", "content": "..."} → 출력 데이터 청크
  - data: {"type": "summary", "data": {...}} → 최종 결과 (non-streaming)
  - data: {"type": "end", "message": "..."} → 스트림 종료
  - data: {"type": "error", "detail": "..."} → 에러
"""

from __future__ import annotations

import json
import time
from typing import Any


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000")


def agent_event_to_sse(
    event: dict,
    skip_detail_log: bool = False,
) -> list[dict[str, str]]:
    """Agent Core 이벤트를 xgen-workflow 호환 SSE 이벤트로 변환.

    Args:
        event: Agent.run_stream()이 yield하는 이벤트
        skip_detail_log: True이면 log/node_status 미전송 (deploy 모드)

    Returns:
        SSE 이벤트 리스트. 각 dict는 {"event": ..., "data": ...} 형식.
    """
    event_type = event.get("type", "")
    data = event.get("data", {})
    sse_events = []

    if event_type == "thinking":
        if not skip_detail_log:
            sse_events.append({
                "event": "log",
                "data": json.dumps({
                    "timestamp": _ts(),
                    "level": "info",
                    "message": f"Thinking... (iteration {data.get('iteration', '?')})",
                }, ensure_ascii=False),
            })

    elif event_type == "tool_call":
        if not skip_detail_log:
            sse_events.append({
                "event": "tool",
                "data": json.dumps({
                    "timestamp": _ts(),
                    "event_type": "tool_call",
                    "tool_name": data.get("name", ""),
                    "tool_input": data.get("arguments", {}),
                }, ensure_ascii=False),
            })

    elif event_type == "tool_result":
        if not skip_detail_log:
            result = data.get("result", data.get("error", ""))
            sse_events.append({
                "event": "tool",
                "data": json.dumps({
                    "timestamp": _ts(),
                    "event_type": "tool_result",
                    "tool_name": data.get("name", ""),
                    "result": result,
                    "result_length": len(str(result)),
                }, ensure_ascii=False),
            })

    elif event_type == "approval_required":
        if not skip_detail_log:
            sse_events.append({
                "event": "log",
                "data": json.dumps({
                    "timestamp": _ts(),
                    "level": "warning",
                    "message": f"승인 대기: {data.get('action', '')}",
                    "approval": data,
                }, ensure_ascii=False),
            })

    elif event_type == "approval_rejected":
        if not skip_detail_log:
            sse_events.append({
                "event": "log",
                "data": json.dumps({
                    "timestamp": _ts(),
                    "level": "warning",
                    "message": f"승인 거절됨: {data.get('request_id', '')}",
                }, ensure_ascii=False),
            })

    elif event_type == "resumed":
        if not skip_detail_log:
            sse_events.append({
                "event": "log",
                "data": json.dumps({
                    "timestamp": _ts(),
                    "level": "info",
                    "message": f"세션 재개: {data.get('session_id', '')}",
                }, ensure_ascii=False),
            })

    elif event_type == "done":
        text = data if isinstance(data, str) else str(data)
        # 데이터 청크로 전송
        sse_events.append({
            "data": json.dumps({
                "type": "data",
                "content": text,
            }, ensure_ascii=False),
        })
        # 종료 이벤트
        sse_events.append({
            "data": json.dumps({
                "type": "end",
                "message": "Stream finished",
            }, ensure_ascii=False),
        })

    elif event_type == "error":
        error_msg = data if isinstance(data, str) else str(data.get("error", data))
        sse_events.append({
            "data": json.dumps({
                "type": "error",
                "detail": error_msg,
            }, ensure_ascii=False),
        })

    return sse_events


def make_deploy_response(
    content: str, citations: list | None = None, error: str | None = None
) -> dict:
    """Deploy JSON 응답 생성."""
    return {
        "success": error is None,
        "content": content,
        "citations": citations or [],
        "error": error,
    }


def make_envelope_response(content: str, citations: list | None = None) -> dict:
    """Java Client Envelope 응답 생성."""
    return {
        "timestamp": _ts(),
        "code": "200",
        "message": "Success",
        "payload": {
            "content": content,
            "citations": citations or [],
        },
    }
