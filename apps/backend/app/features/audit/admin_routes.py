import asyncio
import glob
import json
import os
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from zoneinfo import ZoneInfo

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.limiter import limiter
from app.middleware.logging import _get_client_ip
from app.utils.audit import audit_logger
from app.features.auth.dependencies import get_current_user, require_admin

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit"])

LOG_DIR = os.getenv("LOG_DIR", "logs")
AUDIT_LOG_PATH = os.path.join(LOG_DIR, "audit.log")

STREAM_TIMEOUT = 900

DEFAULT_LOG_TZ = "Asia/Kolkata"


def _parse_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Parse an ISO datetime to a timezone-aware UTC datetime.

    Naive inputs are assumed to be in `tz` (e.g. '2026-08-24T18:00:00' = 6 PM IST).
    """
    clean = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _parse_log_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
        return entry
    except (json.JSONDecodeError, TypeError):
        return None


def _rotated_log_paths() -> list:
    """Return rotated audit log files (excluding the active one), oldest first."""
    active = os.path.abspath(AUDIT_LOG_PATH)
    paths = []
    for p in glob.glob(os.path.join(LOG_DIR, "audit.log*")):
        if os.path.abspath(p) == active:
            continue
        if os.path.isfile(p):
            paths.append(p)
    paths.sort(key=os.path.getmtime)
    return paths


def _read_lines(path: str):
    """Yield lines from a log file, transparently handling loguru .zip rotation archives."""
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                yield from zf.read(name).decode("utf-8").splitlines()
    else:
        with open(path, "r", encoding="utf-8") as fh:
            yield from fh


def _iter_log_lines():
    """Yield audit log lines in chronological order (rotated archives oldest-first, then active file)."""
    for p in _rotated_log_paths():
        yield from _read_lines(p)
    if os.path.exists(AUDIT_LOG_PATH):
        yield from _read_lines(AUDIT_LOG_PATH)


@router.get("")
@limiter.limit("5/minute")
async def get_audit_logs(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
    since: Optional[str] = Query(None, description="Start datetime (ISO, e.g. 2026-08-24T18:00:00)"),
    until: Optional[str] = Query(None, description="End datetime (ISO, e.g. 2026-08-24T19:00:00)"),
    tz: Optional[str] = Query(None, description="Timezone for naive since/until (default Asia/Kolkata)"),
    action: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search username, message, or client IP"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    page: int = Query(1, description="Page number"),
    page_size: int = Query(50, description="Results per page"),
):
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    search_term = (search or "").strip().lower()[:100]

    try:
        log_tz = ZoneInfo(tz or DEFAULT_LOG_TZ)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid timezone.")

    entries = []
    since_dt = None
    if since:
        try:
            since_dt = _parse_datetime(since, log_tz)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid 'since' format. Use ISO datetime, e.g. 2026-08-24T18:00:00")
    until_dt = None
    if until:
        try:
            until_dt = _parse_datetime(until, log_tz)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid 'until' format. Use ISO datetime, e.g. 2026-08-24T19:00:00")

    for line in _iter_log_lines():
        entry = _parse_log_line(line)
        if not entry:
            continue
        metadata = entry.get("metadata") or {}
        if metadata.get("action") == "backchannel_logout":
            continue
        if action and metadata.get("action", "").lower() != action.lower():
            continue
        if search_term:
            hay = " ".join(filter(None, [
                metadata.get("username"),
                entry.get("message"),
                metadata.get("client_ip"),
            ])).lower()
            if search_term not in hay:
                continue
        if since_dt or until_dt:
            try:
                ts = entry.get("time", "")
                if not ts:
                    continue
                entry_time = datetime.fromisoformat(ts)
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                entry_time = entry_time.astimezone(timezone.utc)
                if since_dt and entry_time < since_dt:
                    continue
                if until_dt and entry_time > until_dt:
                    continue
            except (ValueError, TypeError):
                continue
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
                metadata.get("status", ""),
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
    for line in _iter_log_lines():
        entry = _parse_log_line(line)
        if not entry:
            continue
        metadata = entry.get("metadata") or {}
        action = metadata.get("action")
        if action:
            actions.add(action)
    return {"actions": sorted(actions)}
