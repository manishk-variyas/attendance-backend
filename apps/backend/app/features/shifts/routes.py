import logging
from io import BytesIO

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from datetime import datetime, timezone, date, timedelta, time
from zoneinfo import ZoneInfo
from typing import Annotated, List, Optional
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_active
from app.features.shifts.schemas import ShiftCreate, ShiftBulkCreate, ShiftUpdate, ShiftStats, ShiftDefinitionCreate, ShiftDefinitionResponse
from app.features.shifts.service import shift_service
from app.features.redmine.service import redmine_service
from app.middleware.logging import _get_client_ip
from app.models.leave import Leave
from app.models.leave_type import LeaveTypeConfig
from app.models.shift import Shift
from app.models.employee_master import EmployeeMaster
from app.models.holiday import Holiday
from app.models.shift_definition import ShiftDefinition
from app.models.attendance import Attendance
from app.utils.audit import audit_logger
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shifts", tags=["shifts"])

async def authorize_shift_access(current_user: dict, target_email: str = None, target_project_id: int = None, is_write: bool = False):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if "Admin" in roles:
        return True

    if "Project Manager" not in roles and "Project Coordinator" not in roles:
        if is_write:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Admin, Project Manager, or Project Coordinator can create or update shifts.",
            )
        if target_email and email == target_email:
            return True
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this shift.",
        )

    if target_project_id:
        user = await redmine_service.get_user_by_email(email)
        if user:
            projects = await redmine_service.get_projects_for_user(user["id"])
            project_ids = {p.id for p in projects}
            if target_project_id in project_ids:
                return True
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't manage this project.",
        )

    if target_email and email == target_email:
        return True

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have access to this shift.",
    )



async def check_and_resolve_tr_availability(db: Session, userId: int, userEmail: str, date_str: str, end_date_str: str = None):
    try:
        shift_date = date.fromisoformat(date_str)
        shift_end_date = date.fromisoformat(end_date_str) if end_date_str else shift_date
        
        stmt = select(Leave).where(
            Leave.user_email == userEmail,
            Leave.approval_status == "approved",
            Leave.start_date <= shift_end_date,
            Leave.end_date >= shift_date,
        )
        existing_leave = db.execute(stmt).scalars().first()
        if existing_leave:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User has an approved leave during this period."
            )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error checking leave status for {userEmail}: {e}")

    from app.services.database.shift_service import ShiftService as PGShiftService
    svc = PGShiftService(db)
    svc.delete_by_user_and_date(userId, date_str)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_shift(
    payload: ShiftCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await authorize_shift_access(
        current_user=current_user,
        target_email=payload.userEmail,
        target_project_id=payload.projectId,
        is_write=True
    )

    try:
        start = date.fromisoformat(payload.date)
        end = date.fromisoformat(payload.endDate) if payload.endDate else start
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if start > end:
        raise HTTPException(status_code=400, detail="endDate must be on or after date.")

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    if end < today:
        raise HTTPException(status_code=400, detail="Cannot create shifts for past dates.")

    if start == today and "Admin" not in current_user.get("roles", []):
        tz_str = payload.timezone or "Asia/Kolkata"
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = ZoneInfo("Asia/Kolkata")
        try:
            shift_time = time.fromisoformat(payload.shiftStartTime[:5])
            now_local = datetime.now(tz).time()
            if shift_time <= now_local:
                raise HTTPException(status_code=400, detail=f"Shift start time ({payload.shiftStartTime[:5]}) has already passed for today.")
        except ValueError:
            pass

    shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == payload.shift).first()
    if not shift_def:
        raise HTTPException(status_code=400, detail=f"Shift code '{payload.shift}' does not exist.")

    emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == payload.userEmail).first()
    if emp and emp.keycloak_user_id:
        existing_att = db.query(Attendance).filter(
            and_(Attendance.keycloak_user_id == emp.keycloak_user_id, Attendance.attendance_date == start)
        ).first()
        if existing_att and existing_att.check_in_time:
            raise HTTPException(status_code=400, detail="Cannot create shift. User has already checked in for this date.")

    base = payload.model_dump()
    created = []
    from app.services.database.shift_service import ShiftService as PGShiftService
    svc = PGShiftService(db)
    current_date = start
    skipped_weekends = 0
    skipped_holidays = 0
    while current_date <= end:
        if payload.skipWeekends and current_date.weekday() >= 5:
            skipped_weekends += 1
            current_date += timedelta(days=1)
            continue
        if payload.skipHolidays:
            holiday_query = db.query(Holiday).filter(Holiday.holiday_date == current_date)
            if payload.country:
                holiday_query = holiday_query.filter(Holiday.country_code == payload.country)
            if holiday_query.first():
                skipped_holidays += 1
                current_date += timedelta(days=1)
                continue
        date_str = current_date.isoformat()
        await check_and_resolve_tr_availability(db, payload.userId, payload.userEmail, date_str, date_str)
        shift_data = {**base, "date": date_str, "endDate": date_str}
        result = await shift_service.create_shift(db, shift_data, current_user)
        created.append(result)
        current_date += timedelta(days=1)

    if len(created) == 1:
        return created[0]
    response = {"created": len(created), "shifts": created}
    skipped = {}
    if payload.skipWeekends:
        skipped["weekends"] = skipped_weekends
    if payload.skipHolidays:
        skipped["holidays"] = skipped_holidays
    if skipped:
        response["skipped"] = skipped
    return response


@router.post("/validate")
async def validate_shift(
    payload: ShiftCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await authorize_shift_access(
        current_user=current_user,
        target_email=payload.userEmail,
        target_project_id=payload.projectId,
        is_write=True
    )
    shift_data = payload.model_dump()
    if not shift_data.get("endDate"):
        shift_data["endDate"] = payload.date
    return await shift_service.validate_shift(db, shift_data, current_user)


@router.post("/bulk", status_code=status.HTTP_201_CREATED)
async def create_bulk_shifts(
    payload: ShiftBulkCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await authorize_shift_access(
        current_user=current_user,
        target_email=payload.userEmail,
        target_project_id=payload.projectId,
        is_write=True
    )

    try:
        start = date.fromisoformat(payload.startDate)
        end = date.fromisoformat(payload.endDate)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if start > end:
        raise HTTPException(status_code=400, detail="startDate must be on or before endDate.")

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    if start < today:
        raise HTTPException(status_code=400, detail="Cannot create shifts for past dates.")

    try:
        st = time.fromisoformat(payload.shiftStartTime[:5])
        et = time.fromisoformat(payload.shiftEndTime[:5])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid time format. Use HH:MM.")
    if st == et:
        raise HTTPException(status_code=400, detail="Shift start and end time cannot be the same.")

    shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == payload.shift).first()
    if not shift_def:
        raise HTTPException(status_code=400, detail=f"Shift code '{payload.shift}' does not exist.")

    base = payload.model_dump(exclude={"startDate", "endDate"})

    created = []
    skipped_dates = []
    leave_blocked = []

    from app.services.database.shift_service import ShiftService as PGShiftService
    svc = PGShiftService(db)
    current_date = start
    while current_date <= end:
        date_str = current_date.isoformat()

        on_leave = db.execute(
            select(Leave).where(
                Leave.user_email == payload.userEmail,
                Leave.approval_status.in_(["approved"]),
                Leave.start_date <= current_date,
                Leave.end_date >= current_date,
            )
        ).scalars().first()
        if on_leave:
            leave_blocked.append(date_str)
            current_date += timedelta(days=1)
            continue

        svc.delete_by_user_and_date(payload.userId, date_str)
        shift_data = {**base, "date": date_str, "endDate": date_str}
        result = await shift_service.create_shift(db, shift_data, current_user)
        created.append(result)
        current_date += timedelta(days=1)

    return {
        "created": len(created),
        "skipped_leave": len(leave_blocked),
        "leave_dates": leave_blocked,
        "shifts": created,
    }


@router.get("/server-time")
async def get_server_time(
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    now = datetime.now(timezone.utc)
    return {
        "server_time": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "timezone": "UTC",
    }


@router.get("/stats", response_model=ShiftStats)
async def get_shift_stats(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return await shift_service.get_stats(db)


@router.get("/current/{user_id}")
async def get_current_shift(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    target_user = None
    if "Admin" not in current_user.get("roles", []):
        from app.features.redmine.sql_service import RedmineSQLService
        sql = RedmineSQLService(db)
        users = sql.get_all_users()
        target_user = next((u for u in users if u["id"] == user_id), None)
        if not target_user:
             raise HTTPException(status_code=404, detail="User not found.")
        
    shift = await shift_service.get_current_shift(db, user_id)
    if not shift:
        raise HTTPException(status_code=404, detail="No shift found for this user.")

    await authorize_shift_access(
        current_user=current_user,
        target_email=target_user["email"] if target_user else None,
        target_project_id=shift.get("projectId")
    )
    
    return shift


@router.get("/active/{user_id}")
async def get_active_shift(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    shift = await shift_service.get_active_shift(db, user_id)
    if not shift:
        raise HTTPException(status_code=404, detail="No active shift found.")
    return shift


@router.get("/user/{user_id}")
async def get_shifts_by_user_id(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return await shift_service.get_shifts_by_user_id(db, user_id)


@router.get("/user/email/{email}")
async def get_shifts_by_email(
    email: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if (
        current_user["email"] != email
        and "Admin" not in current_user.get("roles", [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own shifts.",
        )
    return await shift_service.get_shifts_by_email(db, email)


@router.get("/work-locations")
async def get_work_locations(
    current_user: dict = Depends(get_current_user),
):
    return ["OFFICE", "WFH", "REMOTE_ONSITE", "REMOTE_OFFSITE", "LEAVE"]


@router.get("/range")
async def get_shifts_by_range(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    user_id: Optional[int] = Query(None, description="User ID to view schedule for (PM/Admin only)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    roles = current_user.get("roles", [])
    current_email = current_user.get("email")
    if not current_email:
        raise HTTPException(status_code=400, detail="User email not found.")

    allowed_roles = ["Technical Resource", "Project Manager", "Admin", "Project Coordinator"]
    if not any(role in roles for role in allowed_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this feature.",
        )

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)
    current_rm_user = sql.get_user_by_email(current_email)
    if not current_rm_user:
        raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
    current_rm_id = current_rm_user["id"]

    target_user_id = user_id or current_rm_id

    target_email = current_email
    if target_user_id != current_rm_id:
        if "Admin" in roles:
            pass
        elif "Project Manager" in roles or "Project Coordinator" in roles:
            pm_projects = sql.get_projects_for_user(current_rm_id)
            tr_projects = sql.get_projects_for_user(target_user_id)
            pm_project_ids = {p.id for p in pm_projects}
            tr_project_ids = {p.id for p in tr_projects}
            if not pm_project_ids.intersection(tr_project_ids):
                raise HTTPException(
                    status_code=403,
                    detail="You can only view shifts for users in your projects."
                )
        else:
            raise HTTPException(status_code=403, detail="Not authorized to view other users' shifts.")

        from app.models.employee_master import EmployeeMaster
        from app.services.database.base_service import BaseService
        emp_svc = BaseService[EmployeeMaster](db)
        emp = emp_svc.fetch_one(EmployeeMaster, redmine_user_id=target_user_id)
        target_email = emp.user_email if emp else None

    shifts = await shift_service.get_shifts_by_date_range(
        db,
        start_date=start_date,
        end_date=end_date,
        user_id=target_user_id
    )

    leaves = []
    if target_email:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
        stmt = select(Leave).where(
            Leave.user_email == target_email,
            Leave.start_date <= ed,
            Leave.end_date >= sd,
            Leave.approval_status == "approved",
        ).order_by(Leave.start_date)
        leaves = [lv.to_dict() for lv in db.execute(stmt).scalars().all()]
        if leaves:
            lt_types = db.query(LeaveTypeConfig).all()
            ltype_map = {t.code: t.name for t in lt_types}
            for lv in leaves:
                code = lv.get("leave_type")
                name = ltype_map.get(code)
                if name is None and code == "PL":
                    name = ltype_map.get("CO")
                lv["leave_type_name"] = name or code

    timeline = {}
    for s in shifts:
        d = s.get("date")
        if not d:
            continue
        if d not in timeline:
            timeline[d] = {"date": d, "shift": None, "leave": None}
        timeline[d]["shift"] = s

    for lv in leaves:
        sd = lv.get("start_date")
        ed = lv.get("end_date")
        if not sd or not ed:
            continue
        current = date.fromisoformat(sd) if isinstance(sd, str) else sd
        end = date.fromisoformat(ed) if isinstance(ed, str) else ed
        while current <= end:
            d_str = current.isoformat()
            if d_str not in timeline:
                timeline[d_str] = {"date": d_str, "shift": None, "leave": None}
            if not timeline[d_str]["leave"]:
                timeline[d_str]["leave"] = lv
            current += timedelta(days=1)

    result = sorted(timeline.values(), key=lambda x: x["date"])
    return {"timeline": result}


@router.get("/date/{date_str}")
async def get_shifts_by_date(
    date_str: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="User email not found.")
    return await shift_service.get_shifts_by_date(db, date_str, email=email)


@router.get("/history")
async def get_shift_history(
    user_id: Optional[int] = Query(None, description="Admin/PM: view another user's shift history"),
    team: Optional[bool] = Query(None, description="PM/PC: view all team members' shifts"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    email = current_user.get("email")
    roles = current_user.get("roles", [])

    if team:
        if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
            raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")
        from app.features.redmine.sql_service import RedmineSQLService
        sql = RedmineSQLService(db)
        rm_user = sql.get_user_by_email(email)
        if not rm_user:
            raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
        member_rm_ids = sql.get_team_member_ids(rm_user["id"])
        member_rm_ids.add(rm_user["id"])
        emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
        member_emails = [e.user_email for e in emps if e.user_email]
        if not member_emails:
            return {"total": 0, "records": []}
        records = await shift_service.get_team_shifts(db, member_emails, from_date, to_date)
        return {"total": len(records), "records": records}

    if user_id is None:
        records = await shift_service.get_shift_history_by_email(db, email)
        return {"total": len(records), "records": records}

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)
    current_rm_user = sql.get_user_by_email(email)
    if not current_rm_user:
        raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
    current_rm_id = current_rm_user["id"]

    if user_id == current_rm_id:
        records = await shift_service.get_shift_history_by_email(db, email)
        return {"total": len(records), "records": records}

    if "Admin" in roles:
        pass
    elif "Project Manager" in roles or "Project Coordinator" in roles:
        pm_projects = sql.get_projects_for_user(current_rm_id)
        tr_projects = sql.get_projects_for_user(user_id)
        pm_project_ids = {p.id for p in pm_projects}
        tr_project_ids = {p.id for p in tr_projects}
        if not pm_project_ids.intersection(tr_project_ids):
            raise HTTPException(
                status_code=403,
                detail="You can only view shifts for users in your projects."
            )
    else:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view other users' shift history."
        )

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)
    all_users = sql.get_all_users()
    target_user = next((u for u in all_users if u["id"] == user_id), None)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    records = await shift_service.get_shift_history_by_email(db, target_user["email"])
    return {"total": len(records), "records": records}


def _build_shifts_excel(records: list) -> BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Shifts"
    ws.append(["Date", "Day", "Employee", "Designation", "Project", "Shift Code", "Shift Name",
               "Work Status", "Status", "Shift Start", "Shift End", "Check In", "Check Out",
               "Total Hours", "Late (hrs)", "Work Address"])
    for r in records:
        ws.append([
            r.get("date") or "",
            r.get("dayName") or "",
            r.get("userName") or "",
            r.get("userDesignation") or "",
            r.get("projectName") or "",
            r.get("shift") or "",
            r.get("shiftName") or "",
            r.get("workStatus") or "",
            r.get("status") or "",
            r.get("shiftStartTime") or "",
            r.get("shiftEndTime") or "",
            r.get("checkInTime") or "",
            r.get("checkOutTime") or "",
            r.get("totalHours") if r.get("totalHours") is not None else "",
            r.get("lateByHours") if r.get("lateByHours") is not None else "",
            r.get("workAddress") or "",
        ])
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


@router.get("/my-history")
async def get_my_shift_history(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("date", description="date, shiftStartTime, shiftCode, workStatus, userName"),
    sort_dir: str = Query("asc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    work_status: Optional[str] = Query(None, description="OFFICE, WFH, REMOTE_ONSITE, REMOTE_OFFSITE"),
    project_id: Optional[int] = Query(None, description="Filter by project"),
    status: Optional[str] = Query(None, description="Comma-separated: Ended, In Progress, on_leave, Missed, Not checked in, Yet to start"),
    search: Optional[str] = Query(None, description="Search shift code, project, work address"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="User email not found.")
    result = await shift_service.get_shifts_paginated(
        db, emails=[email],
        from_date=from_date, to_date=to_date,
        work_status=work_status, project_id=project_id,
        status=status,
        search=search, include_name_search=False,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size, export=export,
    )
    if export:
        output = _build_shifts_excel(result["records"])
        audit_logger.info(
            f"Shift history exported by {current_user.get('username')}",
            extra={
                "correlation_id": request.state.correlation_id,
                "extra_data": {
                    "action": "export_shifts",
                    "username": current_user.get("username"),
                    "status": "success",
                    "client_ip": _get_client_ip(request),
                    "exported_rows": len(result["records"]),
                },
            },
        )
        filename = f"my_shifts_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return result


@router.get("/team-history")
async def get_team_shift_history(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("date", description="date, shiftStartTime, shiftCode, workStatus, userName"),
    sort_dir: str = Query("asc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    work_status: Optional[str] = Query(None, description="OFFICE, WFH, REMOTE_ONSITE, REMOTE_OFFSITE"),
    project_id: Optional[int] = Query(None, description="Filter by project"),
    status: Optional[str] = Query(None, description="Comma-separated: Ended, In Progress, on_leave, Missed, Not checked in, Yet to start"),
    search: Optional[str] = Query(None, description="Search by name, email, shift code, project"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(email)
    if not rm_user:
        raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
    member_rm_ids = sql.get_team_member_ids(rm_user["id"])
    member_rm_ids.add(rm_user["id"])
    emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
    member_emails = [e.user_email for e in emps if e.user_email]
    if not member_emails:
        return {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

    result = await shift_service.get_shifts_paginated(
        db, emails=member_emails,
        from_date=from_date, to_date=to_date,
        work_status=work_status, project_id=project_id,
        status=status,
        search=search, include_name_search=True,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size, export=export,
    )
    if export:
        output = _build_shifts_excel(result["records"])
        audit_logger.info(
            f"Team shift history exported by {current_user.get('username')}",
            extra={
                "correlation_id": request.state.correlation_id,
                "extra_data": {
                    "action": "export_team_shifts",
                    "username": current_user.get("username"),
                    "status": "success",
                    "client_ip": _get_client_ip(request),
                    "exported_rows": len(result["records"]),
                },
            },
        )
        filename = f"team_shifts_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return result




@router.put("/{shift_id}")
@limiter.limit("10/minute")
async def update_shift(
    request: Request,
    shift_id: Annotated[str, Path(max_length=36)],
    payload: ShiftUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    existing_shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not existing_shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    await authorize_shift_access(
        current_user=current_user,
        target_email=existing_shift.user_email,
        target_project_id=payload.projectId or existing_shift.project_id,
        is_write=True,
    )

    existing_att = db.query(Attendance).filter(
        Attendance.keycloak_user_id == existing_shift.keycloak_user_id,
        Attendance.attendance_date >= existing_shift.date,
        Attendance.attendance_date <= (existing_shift.end_date or existing_shift.date),
        Attendance.check_in_time.isnot(None),
    ).first()
    if existing_att:
        raise HTTPException(status_code=400, detail="Cannot update shift. User has already checked in for this date range.")

    if payload.shift:
        shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == payload.shift).first()
        if not shift_def:
            raise HTTPException(status_code=400, detail=f"Shift code '{payload.shift}' does not exist.")

    if payload.shiftStartTime:
        try:
            time.fromisoformat(payload.shiftStartTime[:5])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid shiftStartTime format. Use HH:MM.")
    if payload.shiftEndTime:
        try:
            time.fromisoformat(payload.shiftEndTime[:5])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid shiftEndTime format. Use HH:MM.")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update.")

    if "endDate" in update_data:
        try:
            end = date.fromisoformat(update_data["endDate"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid endDate format. Use YYYY-MM-DD.")

        start = existing_shift.date
        if start > end:
            raise HTTPException(status_code=400, detail="endDate must be on or after date.")

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        if end < today:
            raise HTTPException(status_code=400, detail="Cannot update endDate to a past date.")

    updated = await shift_service.update_shift(db, shift_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Shift not found.")

    changed = ", ".join(update_data.keys())
    audit_logger.info(
        f"Shift updated for {existing_shift.user_email} ({existing_shift.date} to {existing_shift.end_date or existing_shift.date}) by {current_user.get('username')} — changed: {changed}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "shift_update",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "updated_user": existing_shift.user_email,
                "updated_date": existing_shift.date.isoformat(),
                "changed_fields": changed,
            },
        },
    )

    return updated


@router.delete("/by-date", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_shift_by_date(
    request: Request,
    user_id: int = Query(..., description="Redmine user ID"),
    target_date: str = Query(..., description="YYYY-MM-DD"),
    reason: Optional[str] = Query(None, max_length=200, description="Reason for deletion"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        td = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == user_id).first()
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(status_code=404, detail="Employee not found or not onboarded")

    shift = db.query(Shift).filter(
        Shift.keycloak_user_id == emp.keycloak_user_id,
        Shift.date <= td,
        Shift.end_date >= td,
    ).first()

    await authorize_shift_access(
        current_user=current_user,
        target_email=emp.user_email,
        target_project_id=shift.project_id if shift else None,
        is_write=True,
    )

    deleted = await shift_service.delete_shift_by_date(db, emp.keycloak_user_id, td)
    if not deleted:
        raise HTTPException(status_code=404, detail="No shift found for this date")

    audit_logger.info(
        f"Shift deleted for {emp.user_email} on {target_date} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "shift_delete",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "deleted_user": emp.user_email,
                "deleted_date": target_date,
                "reason": reason or "",
            },
        },
    )


@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_shift(
    request: Request,
    shift_id: Annotated[str, Path(max_length=36)],
    reason: Optional[str] = Query(None, max_length=200, description="Reason for deletion"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    await authorize_shift_access(
        current_user=current_user,
        target_email=shift.user_email,
        target_project_id=shift.project_id,
        is_write=True,
    )

    deleted = await shift_service.delete_shift(db, shift_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shift not found.")

    audit_logger.info(
        f"Shift deleted for {shift.user_email} ({shift.date} to {shift.end_date or shift.date}) by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "shift_delete",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "deleted_user": shift.user_email,
                "deleted_date": shift.date.isoformat(),
                "deleted_end_date": shift.end_date.isoformat() if shift.end_date else None,
                "reason": reason or "",
            },
        },
    )


@router.post("/definitions", status_code=status.HTTP_201_CREATED)
async def create_shift_definition(
    payload: ShiftDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return await shift_service.create_shift_definition(db, payload.model_dump())


@router.get("/definitions")
async def get_all_shift_definitions(
    request: Request,
    search: str = Query(None, description="Search by shift name"),
    country: str = Query(None, description="Filter by country"),
    sort_by: str = Query("shift_code", description="shift_code, shift_name, country"),
    sort_dir: str = Query("asc", description="asc or desc"),
    export: bool = Query(False, description="Export as Excel"),
    page: int = Query(1),
    page_size: int = Query(50, description="Max 100"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    search_term = (search or "").strip()[:100]

    query = db.query(ShiftDefinition)
    if search_term:
        query = query.filter(ShiftDefinition.shift_name.ilike(f"%{search_term}%"))
    if country:
        query = query.filter(ShiftDefinition.country == country)

    SORT_COLUMNS = {"shift_code", "shift_name", "country"}
    sort_by = sort_by if sort_by in SORT_COLUMNS else "shift_code"
    sort_dir = sort_dir if sort_dir in ("asc", "desc") else "asc"
    query = query.order_by(
        getattr(ShiftDefinition, sort_by).desc() if sort_dir == "desc"
        else getattr(ShiftDefinition, sort_by).asc()
    )

    total = query.count()

    if export:
        definitions = query.all()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Shift Definitions"
        ws.append(["Shift Code", "Shift Name", "Start Time", "End Time", "Timezone", "Country"])

        for d in definitions:
            ws.append([
                d.shift_code,
                d.shift_name,
                d.start_time.isoformat() if d.start_time else "",
                d.end_time.isoformat() if d.end_time else "",
                d.timezone,
                d.country or "",
            ])

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        audit_logger.info(
            f"Shift definitions exported by {current_user.get('username')}",
            extra={
                "correlation_id": request.state.correlation_id,
                "extra_data": {
                    "action": "export_excel",
                    "username": current_user.get("username"),
                    "status": "success",
                    "client_ip": _get_client_ip(request),
                    "exported_rows": len(definitions),
                },
            },
        )

        filename = f"shift_definitions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    offset = (page - 1) * page_size
    definitions = query.offset(offset).limit(page_size).all()

    return {
        "definitions": [
            {
                "_id": str(d.shift_id),
                "shiftCode": d.shift_code,
                "shiftName": d.shift_name,
                "startTime": d.start_time.isoformat() if d.start_time else None,
                "endTime": d.end_time.isoformat() if d.end_time else None,
                "timezone": d.timezone,
                "country": d.country,
                "createdAt": d.created_at,
                "updatedAt": d.updated_at,
            }
            for d in definitions
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/definitions/by-code/{shift_code}", response_model=ShiftDefinitionResponse)
async def get_shift_definition_by_code(
    shift_code: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    definition = await shift_service.get_shift_definition_by_code(db, shift_code)
    if not definition:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return definition


@router.get("/definitions/{shift_id}", response_model=ShiftDefinitionResponse)
async def get_shift_definition(
    shift_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    definition = await shift_service.get_shift_definition(db, shift_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return definition


@router.put("/definitions/{shift_id}")
async def update_shift_definition(
    shift_id: str,
    payload: ShiftDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    updated = await shift_service.update_shift_definition(db, shift_id, payload.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return updated


@router.delete("/definitions/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift_definition(
    shift_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")

    definition = db.query(ShiftDefinition).filter(ShiftDefinition.shift_id == shift_id).first()
    if not definition:
        raise HTTPException(status_code=404, detail="Shift definition not found.")

    shift_count = db.query(Shift).filter(Shift.shift_code == definition.shift_code).count()

    if shift_count:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete '{definition.shift_name}'. Still in use by {shift_count} shift{'s' if shift_count > 1 else ''}. Reassign or remove all shifts using this definition before deleting.",
        )

    deleted = await shift_service.delete_shift_definition(db, shift_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shift definition not found.")


@router.get("/{shift_id}")
@limiter.limit("20/minute")
async def get_shift_by_id(
    request: Request,
    shift_id: Annotated[str, Path(max_length=36)],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    await authorize_shift_access(
        current_user=current_user,
        target_email=shift.user_email,
        target_project_id=shift.project_id,
        is_write=False,
    )

    return await shift_service.get_shift_by_id(db, shift_id)
