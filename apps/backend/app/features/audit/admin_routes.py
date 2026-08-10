import asyncio
import json
import os
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

import openpyxl
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.limiter import limiter
from app.middleware.logging import _get_client_ip
from app.utils.audit import audit_logger
from app.features.auth.dependencies import get_current_user, require_admin

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit"])

LOG_DIR = os.getenv("LOG_DIR", "logs")
AUDIT_LOG_PATH = os.path.join(LOG_DIR, "audit.log")

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
    search: Optional[str] = Query(None, description="Search by username"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    page: int = Query(1, description="Page number"),
    page_size: int = Query(50, description="Results per page"),
):
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    search_term = (search or "").strip().lower()[:100]

    entries = []
    since_dt = None
    if since:
        try:
            clean = since.replace("Z", "+00:00")
            since_dt = datetime.fromisoformat(clean)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    until_dt = None
    if until:
        try:
            clean = until.replace("Z", "+00:00")
            until_dt = datetime.fromisoformat(clean)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    if os.path.exists(AUDIT_LOG_PATH):
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
                if search_term:
                    username = (metadata.get("username") or "").lower()
                    if search_term not in username:
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
                        pass
                entries.append(entry)

    entries.reverse()

    if export:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Audit Logs"
        ws.append(["Time", "Level", "Action", "User", "Status", "Message", "IP", "Correlation ID"])

        for entry in entries:
            metadata = entry.get("metadata", {})
            ws.append([
                entry.get("time", ""),
                entry.get("level", ""),
                metadata.get("action", ""),
                metadata.get("username", ""),
                entry.get("status", ""),
                entry.get("message", ""),
                metadata.get("client_ip", ""),
                entry.get("correlation_id", ""),
            ])

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        filters = []
        if search_term:
            filters.append(f"search={search_term}")
        if action:
            filters.append(f"action={action}")
        if since:
            filters.append(f"since={since}")
        if until:
            filters.append(f"until={until}")
        filter_str = f" ({', '.join(filters)})" if filters else ""

        audit_logger.info(
            f"Audit logs exported by {current_user.get('username')}{filter_str}",
            extra={
                "correlation_id": request.state.correlation_id,
                "extra_data": {
                    "action": "export_excel",
                    "username": current_user.get("username"),
                    "status": "success",
                    "client_ip": _get_client_ip(request),
                    "exported_rows": len(entries),
                },
            },
        )

        filename = f"audit_logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    total = len(entries)
    offset = (page - 1) * page_size
    paged = entries[offset:offset + page_size]

    return {
        "entries": paged,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


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


@router.get("/actions")
async def get_audit_actions(
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    actions = set()
    if os.path.exists(AUDIT_LOG_PATH):
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                entry = _parse_log_line(line)
                if not entry:
                    continue
                metadata = entry.get("metadata") or {}
                action = metadata.get("action")
                if action:
                    actions.add(action)
    return {"actions": sorted(actions)}
