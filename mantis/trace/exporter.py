"""Trace Exporter — 외부 시스템 연동 (Phase 4에서 Langfuse/OTel 추가)."""

from __future__ import annotations

import json
import logging
from typing import Any

from mantis.trace.collector import Trace

logger = logging.getLogger(__name__)


class TraceExporter:
    """기본 exporter — 로그 출력."""

    async def export(self, trace: Trace) -> None:
        logger.info(
            "Trace [%s] agent=%s duration=%.0fms steps=%d",
            trace.trace_id,
            trace.agent_name,
            trace.duration_ms or 0,
            len(trace.steps),
        )

    async def export_json(self, trace: Trace) -> str:
        return json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
