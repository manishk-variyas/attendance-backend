import logging
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import List, Optional

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_admin, require_admin_or_pm
from app.features.attendance.schemas import AdminAttendanceCreate, AdminAttendanceUpdate, AttendanceResponse
from app.models.attendance import Attendance
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.shift_definition import ShiftDefinition
from app.services.database.base_service import BaseService
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/attendance", tags=["admin-attendance"])


def _safe_zone(tz_str: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _enrich(db: Session, data: dict, a: Attendance) -> dict:
    if a.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == a.location_id).first()
        data["officeName"] = office.name if office else None
    else:
        data["officeName"] = None
    if a.shift_code:
        sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == a.shift_code).first()
        if sd:
            data["shiftName"] = sd.shift_name
            tz = sd.timezone or "Asia/Kolkata"
            if sd.start_time:
                data["shiftStartTime"] = datetime.combine(a.attendance_date, sd.start_time, tzinfo=_safe_zone(tz)).isoformat()
            if sd.end_time:
                data["shiftEndTime"] = datetime.combine(a.attendance_date, sd.end_time, tzinfo=_safe_zone(tz)).isoformat()
    else:
        data["shiftName"] = None
    return data


def _to_response(db: Session, a: Attendance) -> dict:
    return _enrich(db, a.to_dict(), a)


def _to_response_with_email(db: Session, a: Attendance) -> dict:
    data = _enrich(db, a.to_dict(), a)
    emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.keycloak_user_id).first()
    if emp:
        data["userEmail"] = emp.user_email
        data["userName"] = f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ")
    return data


@router.post("", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_attendance(
    payload: AdminAttendanceCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    emp_svc = BaseService[EmployeeMaster](db)
    emp = emp_svc.fetch_one(EmployeeMaster, user_email=payload.user_email)
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(status_code=404, detail="Employee not found or not onboarded")

    try:
        att_date = date.fromisoformat(payload.attendance_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attendance_date format. Use YYYY-MM-DD.")

    existing = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == emp.keycloak_user_id,
            Attendance.attendance_date == att_date,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Attendance record already exists for this date")

    IST = ZoneInfo("Asia/Kolkata")
    check_in = None
    check_out = None
    if payload.check_in_time:
        try:
            check_in = datetime.fromisoformat(payload.check_in_time)
            if check_in.tzinfo is None:
                check_in = check_in.replace(tzinfo=IST)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid check_in_time format")
    if payload.check_out_time:
        try:
            check_out = datetime.fromisoformat(payload.check_out_time)
            if check_out.tzinfo is None:
                check_out = check_out.replace(tzinfo=IST)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid check_out_time format")

    attendance = Attendance(
        keycloak_user_id=emp.keycloak_user_id,
        attendance_date=att_date,
        check_in_time=check_in,
        check_out_time=check_out,
        work_location_status=payload.work_location_status,
        shift_code=payload.shift_code,
        status=payload.status,
        is_late=payload.is_late,
        is_manual_entry=True,
        modified_by=current_user["sub"],
        remarks=payload.remarks,
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    logger.info(f"Admin {current_user['email']} created attendance for {payload.user_email} on {att_date}")
    return _to_response(db, attendance)


@router.put("/{attendance_id}", response_model=AttendanceResponse)
async def admin_update_attendance(
    attendance_id: str,
    payload: AdminAttendanceUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    roles = current_user.get("roles", [])
    is_pm = "Admin" not in roles

    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    if is_pm:
        if payload.remarks is None:
            raise HTTPException(status_code=400, detail="PM can only update remarks.")
        if payload.check_in_time is not None or payload.check_out_time is not None or \
           payload.work_location_status is not None or payload.shift_code is not None or \
           payload.status is not None or payload.is_late is not None:
            raise HTTPException(status_code=403, detail="PM can only update remarks. Admin required for other fields.")
    else:
        if payload.check_in_time is not None:
            try:
                t = datetime.fromisoformat(payload.check_in_time) if payload.check_in_time else None
                if t and t.tzinfo is None:
                    t = t.replace(tzinfo=IST)
                attendance.check_in_time = t
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid check_in_time format")
        if payload.check_out_time is not None:
            try:
                t = datetime.fromisoformat(payload.check_out_time) if payload.check_out_time else None
                if t and t.tzinfo is None:
                    t = t.replace(tzinfo=IST)
                attendance.check_out_time = t
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid check_out_time format")
        if payload.work_location_status is not None:
            attendance.work_location_status = payload.work_location_status
        if payload.shift_code is not None:
            attendance.shift_code = payload.shift_code
        if payload.status is not None:
            attendance.status = payload.status
        if payload.is_late is not None:
            attendance.is_late = payload.is_late

    if payload.remarks is not None:
        attendance.remarks = payload.remarks

    attendance.is_manual_entry = True
    attendance.modified_by = current_user["sub"]

    db.commit()
    db.refresh(attendance)

    logger.info(f"Admin {current_user['email']} updated attendance {attendance_id}")
    return _to_response(db, attendance)


@router.get("/{attendance_id}", response_model=AttendanceResponse)
async def admin_get_attendance(
    attendance_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    return _to_response(db, attendance)


@router.get("")
async def admin_list_attendance(
    user_email: Optional[str] = Query(None, description="Filter by employee email"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    status: Optional[str] = Query(None, description="Filter by status"),
    is_synced: Optional[bool] = Query(None, description="Filter by synced records"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    query = db.query(Attendance)

    if user_email:
        emps = db.query(EmployeeMaster).filter(EmployeeMaster.user_email.ilike(f"{user_email}%")).limit(50).all()
        if not emps:
            return {"total": 0, "page": page, "per_page": per_page, "total_pages": 0, "records": []}
        keycloak_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
        if not keycloak_ids:
            return {"total": 0, "page": page, "per_page": per_page, "total_pages": 0, "records": []}
        query = query.filter(Attendance.keycloak_user_id.in_(keycloak_ids))

    if from_date:
        try:
            query = query.filter(Attendance.attendance_date >= date.fromisoformat(from_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format")
    if to_date:
        try:
            query = query.filter(Attendance.attendance_date <= date.fromisoformat(to_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format")
    if status:
        query = query.filter(Attendance.status == status)
    if is_synced is not None:
        query = query.filter(Attendance.is_synced == is_synced)

    total = query.count()
    offset = (page - 1) * per_page
    records = query.order_by(Attendance.attendance_date.desc()).offset(offset).limit(per_page).all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "records": [_to_response_with_email(db, r) for r in records],
    }


@router.delete("/{attendance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_attendance(
    attendance_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    db.delete(attendance)
    db.commit()

    logger.info(f"Admin {current_user['email']} deleted attendance {attendance_id}")
