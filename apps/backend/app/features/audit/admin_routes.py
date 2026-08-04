import asyncio
import json
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_admin

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit"])

LOG_DIR = os.getenv("LOG_DIR", "logs")
AUDIT_LOG_PATH = os.path.join(LOG_DIR, "audit.log")

MAX_ENTRIES = 500
STREAM_TIMEOUT = 900


def _parse_log_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
        return entry
    except (json.JSONDecodeError, TypeError):
        return None


@router.get("")
@limiter.limit("5/minute")
async def get_audit_logs(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
):
    entries = []
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    if not os.path.exists(AUDIT_LOG_PATH):
        return {"entries": []}

    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            entry = _parse_log_line(line)
            if not entry:
                continue
            metadata = entry.get("metadata") or {}
            if metadata.get("action") == "backchannel_logout":
                continue
            if action and metadata.get("action", "").lower() != action.lower():
                continue
            if since_dt or until_dt:
                try:
                    ts = entry.get("time", "")
                    if not ts:
                        continue
                    entry_time = datetime.fromisoformat(ts)
                    if since_dt and entry_time < since_dt:
                        continue
                    if until_dt and entry_time > until_dt:
                        continue
                except (ValueError, TypeError):
                    continue
            entries.append(entry)

    entries.reverse()

    return {"entries": entries[:MAX_ENTRIES]}


@router.get("/stream")
@limiter.limit("2/minute")
async def audit_logs_stream(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    async def event_generator():
        loop = asyncio.get_running_loop()
        dead_at = time.monotonic() + STREAM_TIMEOUT
        f = None
        try:
            if os.path.exists(AUDIT_LOG_PATH):
                f = open(AUDIT_LOG_PATH, "r", encoding="utf-8")
                f.seek(0, 2)
            while time.monotonic() < dead_at:
                if await request.is_disconnected():
                    break
                if f is None:
                    if os.path.exists(AUDIT_LOG_PATH):
                        f = open(AUDIT_LOG_PATH, "r", encoding="utf-8")
                        f.seek(0, 2)
                    else:
                        await asyncio.sleep(2)
                        continue
                line = await loop.run_in_executor(None, f.readline)
                if line:
                    entry = _parse_log_line(line)
                    if entry:
                        metadata = entry.get("metadata") or {}
                        if metadata.get("action") == "backchannel_logout":
                            continue
                        yield f"data: {json.dumps(entry, default=str)}\n\n"
                else:
                    await asyncio.sleep(0.5)
                    try:
                        if os.path.getsize(AUDIT_LOG_PATH) < f.tell():
                            f.close()
                            f = open(AUDIT_LOG_PATH, "r", encoding="utf-8")
                    except OSError:
                        pass
        finally:
            if f:
                f.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
