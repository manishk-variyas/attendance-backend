import asyncio
import logging
from datetime import date, timedelta, datetime, timezone
from io import BytesIO
from typing import Optional
from zoneinfo import ZoneInfo

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_active
from app.features.wfh.schemas import WfhRequestCreate, WfhRequestUpdate, WfhRequestReject
from app.features.wfh.service import wfh_service
from app.models.wfh_request import WfhRequest
from app.models.employee_master import EmployeeMaster
from app.models.holiday import Holiday
from app.models.shift import Shift
from app.models.leave import Leave
from app.models.attendance import Attendance
from app.middleware.logging import _get_client_ip
from app.utils.audit import audit_logger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wfh", tags=["wfh"])


def _can_approve(db: Session, current_user: dict, target_email: str) -> bool:
    roles = current_user.get("roles", [])
    if "Admin" in roles:
        return True

    reviewer_email = current_user.get("email")
    reviewer_emp = db.query(EmployeeMaster).filter(
        EmployeeMaster.user_email == reviewer_email
    ).first()
    if not reviewer_emp or not reviewer_emp.redmine_user_id:
        return False

    team = db.query(EmployeeMaster).filter(
        EmployeeMaster.reports_to == reviewer_emp.redmine_user_id
    ).all()
    if any(e.user_email == target_email for e in team):
        return True

    if "Project Manager" in roles or "Project Coordinator" in roles:
        requester_emp = db.query(EmployeeMaster).filter(
            EmployeeMaster.user_email == target_email
        ).first()
        if requester_emp and requester_emp.redmine_user_id:
            from app.features.redmine.service import redmine_service
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            reviewer = loop.run_until_complete(redmine_service.get_user_by_email(reviewer_email))
            requester = loop.run_until_complete(redmine_service.get_user_by_email(target_email))
            if reviewer and requester:
                rp = loop.run_until_complete(redmine_service.get_projects_for_user(reviewer["id"]))
                tp = loop.run_until_complete(redmine_service.get_projects_for_user(requester["id"]))
                if {p.id for p in rp} & {p.id for p in tp}:
                    return True
    return False


from app.services.email_service import email_service


def _batch_user_names(db: Session, emails: list[str]) -> dict:
    if not emails:
        return {}
    emps = db.query(EmployeeMaster).filter(EmployeeMaster.user_email.in_(emails)).all()
    return {e.user_email: f"{e.first_name or ''} {e.last_name or ''}".strip() or e.user_email for e in emps}


def _enrich_response(db: Session, requests: list) -> list:
    emails = {r.user_email for r in requests}
    names = _batch_user_names(db, emails)
    result = []
    for r in requests:
        d = r.to_dict()
        d["userName"] = names.get(r.user_email, r.user_email)
        result.append(d)
    return result


def _get_managed_emails(db: Session, current_user: dict) -> tuple[list[str] | None, bool]:
    roles = current_user.get("roles", [])
    if "Admin" in roles:
        return None, True
    email = current_user.get("email")
    emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == email).first()
    if not emp or not emp.redmine_user_id:
        return [], True
    team = db.query(EmployeeMaster).filter(EmployeeMaster.reports_to == emp.redmine_user_id).all()
    return [e.user_email for e in team if e.user_email], True


def _respond_paginated(db: Session, results: list, total: int,
                        page: int, page_size: int) -> dict:
    return {
        "requests": _enrich_response(db, results),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


def _parse_date_range(from_date: str | None, to_date: str | None) -> tuple[date | None, date | None]:
    from_dt = None
    to_dt = None
    if from_date:
        try:
            from_dt = date.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD.")
    if to_date:
        try:
            to_dt = date.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD.")
    if from_dt and to_dt and from_dt > to_dt:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date.")
    return from_dt, to_dt


def _apply_date_filter(query, from_dt: date | None, to_dt: date | None):
    if from_dt:
        query = query.filter(WfhRequest.start_date >= from_dt)
    if to_dt:
        query = query.filter(WfhRequest.start_date <= to_dt)
    return query


def _export_wfh_requests(db: Session, query, request: Request,
                         current_user: dict, label: str) -> StreamingResponse:
    requests = query.order_by(WfhRequest.created_at.desc()).all()
    names = _batch_user_names(db, {r.user_email for r in requests})

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "WFH Requests"
    ws.append(["Employee", "Email", "Start Date", "End Date", "Resuming Date",
               "Reason", "Status", "Reviewed By", "Created At"])

    for r in requests:
        ws.append([
            names.get(r.user_email, r.user_email),
            r.user_email,
            r.start_date.isoformat() if r.start_date else "",
            r.end_date.isoformat() if r.end_date else "",
            r.resuming_date.isoformat() if r.resuming_date else "",
            r.reason or "",
            r.status,
            r.reviewed_by or "",
            r.created_at.isoformat() if r.created_at else "",
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    audit_logger.info(
        f"WFH {label} exported by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "export_excel",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "exported_rows": len(requests),
            },
        },
    )

    filename = f"wfh_{label}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/request", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def request_wfh(
    request: Request,
    payload: WfhRequestCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    try:
        start_date = date.fromisoformat(payload.start_date)
        end_date = date.fromisoformat(payload.end_date) if payload.end_date else start_date
        resuming_date = date.fromisoformat(payload.resuming_date) if payload.resuming_date else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date.")

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    if end_date < today:
        raise HTTPException(status_code=400, detail="Cannot request WFH for past dates.")

    max_days = 30
    if (end_date - start_date).days >= max_days:
        raise HTTPException(status_code=400, detail=f"Cannot request WFH for more than {max_days} days at a time.")

    user_email = current_user.get("email")
    keycloak_user_id = current_user.get("sub")

    current = start_date
    has_shift = False
    while current <= end_date:
        if payload.skip_weekends and current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        if payload.skip_holidays:
            holiday = db.query(Holiday).filter(Holiday.holiday_date == current).first()
            if holiday:
                current += timedelta(days=1)
                continue

        shift = db.query(Shift).filter(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date <= current,
            Shift.end_date >= current,
        ).first()
        if not shift:
            current += timedelta(days=1)
            continue
        has_shift = True

        leave = db.query(Leave).filter(
            Leave.user_email == user_email,
            Leave.approval_status == "approved",
            Leave.start_date <= current,
            Leave.end_date >= current,
        ).first()
        if leave:
            raise HTTPException(status_code=400, detail=f"You already have approved leave on {current.isoformat()}.")

        att = db.query(Attendance).filter(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == current,
            Attendance.check_in_time.isnot(None),
        ).first()
        if att:
            raise HTTPException(status_code=400, detail=f"You have already checked in on {current.isoformat()}.")
        current += timedelta(days=1)

    if not has_shift:
        raise HTTPException(status_code=400, detail="No shift assigned for any of the requested dates. WFH can only be requested for dates with an existing shift.")

    request_ids, created, skipped = wfh_service.create(
        db, keycloak_user_id, user_email,
        start_date, end_date, resuming_date,
        payload.reason, payload.comment, payload.contact_number,
        payload.approver_id, payload.project_id, payload.project_name,
        payload.skip_weekends, payload.skip_holidays,
    )

    if created == 0:
        raise HTTPException(status_code=409, detail="A pending or approved WFH request already exists for all selected dates.")

    audit_logger.info(
        f"WFH requested by {user_email} for {start_date} to {end_date}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "wfh_request",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "created": created,
            },
        },
    )

    applicant_name = _batch_user_names(db, [user_email]).get(user_email, user_email)

    approver_email = None
    if payload.approver_id:
        approver = db.query(EmployeeMaster).filter(
            EmployeeMaster.redmine_user_id == payload.approver_id
        ).first()
        if approver:
            approver_email = approver.user_email
    if not approver_email:
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == user_email).first()
        if emp and emp.reports_to:
            manager = db.query(EmployeeMaster).filter(
                EmployeeMaster.redmine_user_id == emp.reports_to
            ).first()
            if manager:
                approver_email = manager.user_email

    if approver_email:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, email_service.send_wfh_requested,
            approver_email, applicant_name,
            start_date.isoformat(), end_date.isoformat(),
            payload.reason or "No reason provided",
            resuming_date.isoformat() if resuming_date else "",
        )

    response = {"message": "WFH request submitted successfully", "created": created}
    if payload.skip_weekends or payload.skip_holidays:
        response["skipped"] = skipped
    return response


@router.get("/my-requests")
async def get_my_wfh_requests(
    request: Request,
    status: Optional[str] = Query(None, description="pending, approved, rejected"),
    search: Optional[str] = Query(None, description="Search by reason"),
    from_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    export: bool = Query(False, description="Export as Excel"),
    sort_by: str = Query("created_at", description="created_at, start_date, status"),
    sort_dir: str = Query("desc", description="asc or desc"),
    page: int = Query(1),
    page_size: int = Query(50, description="Max 100"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    search_term = (search or "").strip()[:100]
    from_dt, to_dt = _parse_date_range(from_date, to_date)
    keycloak_user_id = current_user.get("sub")

    query = db.query(WfhRequest).filter(WfhRequest.keycloak_user_id == keycloak_user_id)
    if status:
        query = query.filter(WfhRequest.status == status)
    if search_term:
        query = query.filter(WfhRequest.reason.ilike(f"%{search_term}%"))
    query = _apply_date_filter(query, from_dt, to_dt)

    if export:
        return _export_wfh_requests(db, query, request, current_user, "my-requests")

    results, total = wfh_service._apply_pagination(query, sort_by, sort_dir, page, page_size)
    return _respond_paginated(db, results, total, page, page_size)


@router.get("/pending")
async def get_pending_wfh_requests(
    request: Request,
    search: Optional[str] = Query(None, description="Search by name or email"),
    from_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    export: bool = Query(False, description="Export as Excel"),
    sort_by: str = Query("created_at", description="created_at, start_date, user_email"),
    sort_dir: str = Query("desc", description="asc or desc"),
    page: int = Query(1),
    page_size: int = Query(50, description="Max 100"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    search_term = (search or "").strip()[:100]
    from_dt, to_dt = _parse_date_range(from_date, to_date)

    emails, _ = _get_managed_emails(db, current_user)
    if export:
        query = db.query(WfhRequest).filter(WfhRequest.status == "pending")
        if emails is not None:
            query = query.filter(WfhRequest.user_email.in_(emails))
        query = _apply_date_filter(query, from_dt, to_dt)
        if search_term:
            matching = db.query(EmployeeMaster.user_email).filter(
                EmployeeMaster.user_email.ilike(f"%{search_term}%") |
                EmployeeMaster.first_name.ilike(f"%{search_term}%") |
                EmployeeMaster.last_name.ilike(f"%{search_term}%")
            ).all()
            mails = {e[0] for e in matching}
            if mails:
                query = query.filter(WfhRequest.user_email.in_(mails))
            else:
                query = query.filter(WfhRequest.user_email == "__no_match__")
        return _export_wfh_requests(db, query, request, current_user, "pending")

    results, total = wfh_service.get_managed_requests_paginated(
        db, emails, "pending", search_term or None,
        from_dt, to_dt,
        sort_by, sort_dir, page, page_size,
    )
    return _respond_paginated(db, results, total, page, page_size)


@router.get("/team-requests")
async def get_team_wfh_requests(
    request: Request,
    status: Optional[str] = Query(None, description="pending, approved, rejected"),
    search: Optional[str] = Query(None, description="Search by name or email"),
    from_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    export: bool = Query(False, description="Export as Excel"),
    sort_by: str = Query("created_at", description="created_at, start_date, user_email"),
    sort_dir: str = Query("desc", description="asc or desc"),
    page: int = Query(1),
    page_size: int = Query(50, description="Max 100"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    search_term = (search or "").strip()[:100]
    from_dt, to_dt = _parse_date_range(from_date, to_date)

    emails, _ = _get_managed_emails(db, current_user)
    if export:
        query = db.query(WfhRequest)
        if emails is not None:
            query = query.filter(WfhRequest.user_email.in_(emails))
        if status:
            query = query.filter(WfhRequest.status == status)
        query = _apply_date_filter(query, from_dt, to_dt)
        if search_term:
            matching = db.query(EmployeeMaster.user_email).filter(
                EmployeeMaster.user_email.ilike(f"%{search_term}%") |
                EmployeeMaster.first_name.ilike(f"%{search_term}%") |
                EmployeeMaster.last_name.ilike(f"%{search_term}%")
            ).all()
            mails = {e[0] for e in matching}
            if mails:
                query = query.filter(WfhRequest.user_email.in_(mails))
            else:
                query = query.filter(WfhRequest.user_email == "__no_match__")
        return _export_wfh_requests(db, query, request, current_user, "team-requests")

    results, total = wfh_service.get_managed_requests_paginated(
        db, emails, status, search_term or None,
        from_dt, to_dt,
        sort_by, sort_dir, page, page_size,
    )
    return _respond_paginated(db, results, total, page, page_size)


@router.get("/stats")
async def get_wfh_stats(
    month: Optional[int] = Query(None, ge=1, le=12, description="Filter by month"),
    year: Optional[int] = Query(None, ge=2000, description="Filter by year"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return wfh_service.get_stats(db, current_user.get("sub"), month, year)


@router.put("/{request_id}/approve")
async def approve_wfh_request(
    request: Request,
    request_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    wfh = db.query(WfhRequest).filter(WfhRequest.id == request_id).first()
    if not wfh:
        raise HTTPException(status_code=404, detail="WFH request not found.")
    if wfh.status != "pending":
        raise HTTPException(status_code=400, detail="This request is no longer pending.")
    if not _can_approve(db, current_user, wfh.user_email):
        raise HTTPException(status_code=403, detail="You are not authorized to approve this request.")

    reviewer_email = current_user.get("email")
    result = wfh_service.approve(db, request_id, reviewer_email)
    if not result:
        raise HTTPException(status_code=404, detail="WFH request not found.")

    audit_logger.info(
        f"WFH approved for {result.user_email} on {result.start_date} by {reviewer_email}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "wfh_approved",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "requester": result.user_email,
                "request_date": result.start_date.isoformat(),
            },
        },
    )

    applicant_name = _batch_user_names(db, [result.user_email]).get(result.user_email, result.user_email)
    logger.info(f"Dispatching WFH approval email to {result.user_email}")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, email_service.send_wfh_approved,
        result.user_email, applicant_name,
        result.start_date.isoformat(), result.end_date.isoformat(),
        result.resuming_date.isoformat() if result.resuming_date else "")

    return result.to_dict()


@router.put("/{request_id}/reject")
async def reject_wfh_request(
    request: Request,
    request_id: str,
    payload: WfhRequestReject = WfhRequestReject(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    wfh = db.query(WfhRequest).filter(WfhRequest.id == request_id).first()
    if not wfh:
        raise HTTPException(status_code=404, detail="WFH request not found.")
    if wfh.status != "pending":
        raise HTTPException(status_code=400, detail="This request is no longer pending.")
    if not _can_approve(db, current_user, wfh.user_email):
        raise HTTPException(status_code=403, detail="You are not authorized to approve this request.")

    reviewer_email = current_user.get("email")
    result = wfh_service.reject(db, request_id, reviewer_email, payload.reason)
    if not result:
        raise HTTPException(status_code=404, detail="WFH request not found.")

    audit_logger.info(
        f"WFH rejected for {result.user_email} on {result.start_date} by {reviewer_email}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "wfh_rejected",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "requester": result.user_email,
                "request_date": result.start_date.isoformat(),
                "reject_reason": payload.reason or "",
            },
        },
    )

    applicant_name = _batch_user_names(db, [result.user_email]).get(result.user_email, result.user_email)
    logger.info(f"Dispatching WFH rejection email to {result.user_email}")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, email_service.send_wfh_rejected,
        result.user_email, applicant_name,
        result.start_date.isoformat(), result.end_date.isoformat(),
        result.resuming_date.isoformat() if result.resuming_date else "")

    return result.to_dict()


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_wfh_request(
    request: Request,
    request_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    wfh = db.query(WfhRequest).filter(WfhRequest.id == request_id).first()
    if not wfh:
        raise HTTPException(status_code=404, detail="WFH request not found.")
    if wfh.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending requests can be deleted.")

    user_email = current_user.get("email")
    is_own = wfh.user_email == user_email
    if not is_own and not _can_approve(db, current_user, wfh.user_email):
        raise HTTPException(status_code=403, detail="You are not authorized to delete this request.")

    deleted = wfh_service.delete(db, request_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="WFH request not found.")

    audit_logger.info(
        f"WFH request deleted for {wfh.user_email} on {wfh.start_date} by {user_email}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "wfh_deleted",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "requester": wfh.user_email,
                "request_date": wfh.start_date.isoformat(),
            },
        },
    )


@router.get("/{request_id}")
async def get_wfh_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    wfh = db.query(WfhRequest).filter(WfhRequest.id == request_id).first()
    if not wfh:
        raise HTTPException(status_code=404, detail="WFH request not found.")
    if wfh.user_email != current_user.get("email") and not _can_approve(db, current_user, wfh.user_email):
        raise HTTPException(status_code=403, detail="You are not authorized to view this request.")
    return wfh.to_dict()


@router.put("/{request_id}")
@limiter.limit("5/minute")
async def update_wfh_request(
    request: Request,
    request_id: str,
    payload: WfhRequestUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    wfh = db.query(WfhRequest).filter(WfhRequest.id == request_id).first()
    if not wfh:
        raise HTTPException(status_code=404, detail="WFH request not found.")
    if wfh.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending requests can be edited.")
    user_email = current_user.get("email")
    is_own = wfh.user_email == user_email
    if not is_own and not _can_approve(db, current_user, wfh.user_email):
        raise HTTPException(status_code=403, detail="You are not authorized to edit this request.")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update.")

    if "start_date" in update_data:
        try:
            new_start = date.fromisoformat(update_data["start_date"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD.")
        if new_start < date.today():
            raise HTTPException(status_code=400, detail="Cannot set start_date to a past date.")

    if "end_date" in update_data:
        try:
            new_end = date.fromisoformat(update_data["end_date"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD.")
        start = date.fromisoformat(update_data.get("start_date", wfh.start_date.isoformat()))
        if start > new_end:
            raise HTTPException(status_code=400, detail="end_date must be on or after start_date.")
        if new_end < date.today():
            raise HTTPException(status_code=400, detail="Cannot set end_date to a past date.")

    updated = wfh_service.update(db, request_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="WFH request not found.")

    audit_logger.info(
        f"WFH request updated for {updated.user_email} by {current_user.get('email')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "wfh_updated",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "requester": updated.user_email,
                "request_date": updated.start_date.isoformat(),
            },
        },
    )

    return updated.to_dict()
