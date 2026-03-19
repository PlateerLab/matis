"""State Store — Checkpointer 패턴으로 세션 상태 저장/복구."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

try:
    import asyncpg
except ImportError as _exc:
    raise ImportError(
        "asyncpg 패키지가 필요합니다: pip install mantis[state]"
    ) from _exc

logger = logging.getLogger(__name__)

# 테이블 생성 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_checkpoints (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    state JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON agent_checkpoints(session_id);
"""


class StateStore:
    """PostgreSQL 기반 상태 저장소.

    - checkpoint(): 세션 상태 저장 (매 도구 호출마다)
    - resume(): 마지막 체크포인트에서 재개
    - pause_for_approval(): Human-in-the-Loop 승인 대기
    """

    def __init__(self, database_url: str):
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        """DB 연결 풀 생성 및 테이블 초기화."""
        self._pool = await asyncpg.create_pool(self._database_url, min_size=2, max_size=10)
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
        logger.info("StateStore 초기화 완료")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def checkpoint(self, session_id: str, state: dict[str, Any]) -> None:
        """세션 상태를 저장 (upsert)."""
        if not self._pool:
            raise RuntimeError("StateStore가 초기화되지 않았습니다.")

        state_json = json.dumps(state, ensure_ascii=False, default=str)
        async with self._pool.acquire() as conn:
            # session_id가 있으면 업데이트, 없으면 삽입
            existing = await conn.fetchval(
                "SELECT id FROM agent_checkpoints WHERE session_id = $1", session_id
            )
            if existing:
                await conn.execute(
                    "UPDATE agent_checkpoints SET state = $1, updated_at = NOW() WHERE session_id = $2",
                    state_json,
                    session_id,
                )
            else:
                await conn.execute(
                    "INSERT INTO agent_checkpoints (session_id, state) VALUES ($1, $2)",
                    session_id,
                    state_json,
                )
        logger.debug("Checkpoint 저장: %s", session_id)

    async def resume(self, session_id: str) -> dict[str, Any] | None:
        """마지막 체크포인트에서 상태 복구."""
        if not self._pool:
            raise RuntimeError("StateStore가 초기화되지 않았습니다.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state FROM agent_checkpoints WHERE session_id = $1", session_id
            )
        if row:
            return json.loads(row["state"])
        return None

    async def delete(self, session_id: str) -> bool:
        """세션 체크포인트 삭제."""
        if not self._pool:
            raise RuntimeError("StateStore가 초기화되지 않았습니다.")

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM agent_checkpoints WHERE session_id = $1", session_id
            )
        return result != "DELETE 0"

    async def list_sessions(self) -> list[dict]:
        """저장된 세션 목록."""
        if not self._pool:
            raise RuntimeError("StateStore가 초기화되지 않았습니다.")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT session_id, created_at, updated_at FROM agent_checkpoints ORDER BY updated_at DESC"
            )
        return [dict(row) for row in rows]
