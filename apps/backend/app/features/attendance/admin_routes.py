import logging
from datetime import date, datetime, timezone, timedelta
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
from app.models.shift import Shift
from app.models.leave import Leave
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

    data["perDiemEligible"] = a.per_diem_eligible
    data["conveyanceEligible"] = a.conveyance_eligible

    return data


def _to_response(db: Session, a: Attendance) -> dict:
    return _enrich(db, a.to_dict(), a)


def _to_response_with_email(db: Session, a: Attendance) -> dict:
    data = _enrich(db, a.to_dict(), a)
    emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.keycloak_user_id).first()
    if emp:
        data["userEmail"] = emp.user_email
        data["userName"] = f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ")
        data["userDesignation"] = emp.designation
    data["derivedStatus"] = "shift_ended" if a.check_out_time else "in_progress"

    data["isAtAssignedLocation"] = a.location_id is not None and a.work_location_status == "OFFICE"

    data["dayName"] = a.attendance_date.strftime("%A") if a.attendance_date else None

    shift = db.query(Shift).filter(
        and_(Shift.keycloak_user_id == a.keycloak_user_id, Shift.date == a.attendance_date)
    ).first()
    data["shiftProject"] = shift.project_name if shift else None

    leave = db.query(Leave).filter(
        and_(
            Leave.keycloak_user_id == a.keycloak_user_id,
            Leave.approval_status == "approved",
            Leave.start_date <= a.attendance_date,
            Leave.end_date >= a.attendance_date,
        )
    ).first()
    data["onLeave"] = leave is not None
    data["leaveType"] = leave.leave_type if leave else None
    data["leaveStartDate"] = leave.start_date.isoformat() if leave and leave.start_date else None
    data["leaveEndDate"] = leave.end_date.isoformat() if leave and leave.end_date else None
    data["leaveStartDay"] = leave.start_date.strftime("%A") if leave and leave.start_date else None
    data["leaveEndDay"] = leave.end_date.strftime("%A") if leave and leave.end_date else None

    if a.check_out_time:
        if a.modified_by:
            ended_by_emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.modified_by).first()
            data["endedBy"] = f"{ended_by_emp.first_name} {ended_by_emp.last_name}".strip() if ended_by_emp else a.modified_by
            data["endedByRole"] = ended_by_emp.designation if ended_by_emp else None
        elif emp:
            data["endedBy"] = f"{emp.first_name} {emp.last_name}".strip()
            data["endedByRole"] = emp.designation
        else:
            data["endedBy"] = None
            data["endedByRole"] = None
    else:
        data["endedBy"] = None
        data["endedByRole"] = None
    return data


def _build_virtual_record(db: Session, shift, emp) -> dict:
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    has_started = False
    if shift.shift_code:
        sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == shift.shift_code).first()
        if sd and sd.start_time:
            tz = _safe_zone(sd.timezone or "Asia/Kolkata")
            shift_start_dt = datetime.combine(shift.date, sd.start_time, tzinfo=tz)
            has_started = now_ist >= shift_start_dt

    derived = "not_started" if not has_started else "no_show"

    return {
        "id": None,
        "keycloakUserId": shift.keycloak_user_id,
        "attendanceDate": shift.date.isoformat() if shift.date else None,
        "checkInTime": None, "checkInLat": None, "checkInLng": None, "checkInLocationName": None,
        "checkOutTime": None, "checkOutLat": None, "checkOutLng": None, "checkOutLocationName": None,
        "locationId": None, "officeName": None,
        "workLocationStatus": shift.work_location_status,
        "shiftCode": shift.shift_code, "shiftName": None,
        "totalHours": None, "isLate": False, "status": "present",
        "isManualEntry": False, "isSynced": False, "remarks": None,
        "userEmail": emp.user_email if emp else shift.user_email,
        "userName": f"{emp.first_name} {emp.last_name}".strip() if emp else shift.user_email,
        "userDesignation": emp.designation if emp else None,
        "derivedStatus": derived,
        "isAtAssignedLocation": None,
        "dayName": shift.date.strftime("%A") if shift.date else None,
        "shiftProject": shift.project_name,
        "onLeave": False,
        "leaveType": None,
        "leaveStartDate": None,
        "leaveEndDate": None,
        "leaveStartDay": None,
        "leaveEndDay": None,
        "endedBy": None,
        "endedByRole": None,
        "perDiemEligible": shift.per_diem_eligible,
        "conveyanceEligible": shift.conveyance_eligible,
    }


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
        per_diem_eligible=payload.per_diem_eligible if payload.per_diem_eligible is not None else False,
        conveyance_eligible=payload.conveyance_eligible if payload.conveyance_eligible is not None else False,
        is_manual_entry=True,
        modified_by=current_user["sub"],
        remarks=payload.remarks,
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    if payload.shift_code and emp.keycloak_user_id:
        shift_rec = db.query(Shift).filter(
            Shift.keycloak_user_id == emp.keycloak_user_id,
            Shift.date == att_date,
            Shift.shift_code == payload.shift_code,
        ).first()
        if shift_rec:
            shift_rec.status = "Ended" if payload.check_out_time else "In Progress"
            db.commit()

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
        if payload.per_diem_eligible is not None:
            attendance.per_diem_eligible = payload.per_diem_eligible
        if payload.conveyance_eligible is not None:
            attendance.conveyance_eligible = payload.conveyance_eligible

    if payload.remarks is not None:
        attendance.remarks = payload.remarks

    attendance.is_manual_entry = True
    attendance.modified_by = current_user["sub"]

    if attendance.shift_code and attendance.keycloak_user_id:
        shift_rec = db.query(Shift).filter(
            Shift.keycloak_user_id == attendance.keycloak_user_id,
            Shift.date == attendance.attendance_date,
            Shift.shift_code == attendance.shift_code,
        ).first()
        if shift_rec:
            if attendance.check_out_time:
                shift_rec.status = "Ended"
            elif attendance.check_in_time:
                shift_rec.status = "In Progress"

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
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    query = db.query(Attendance)

    if user_email:
        emps = db.query(EmployeeMaster).filter(EmployeeMaster.user_email.ilike(f"{user_email}%")).limit(50).all()
        if not emps:
            return {"total": 0, "records": []}
        keycloak_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
        if not keycloak_ids:
            return {"total": 0, "records": []}
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

    records = query.order_by(Attendance.attendance_date.desc()).all()
    result = [_to_response_with_email(db, r) for r in records]
    covered = {(r.keycloak_user_id, r.attendance_date) for r in records}

    if from_date and to_date:
        f = date.fromisoformat(from_date)
        t = date.fromisoformat(to_date)

        leave_covered = set()
        leave_list = db.query(Leave).filter(
            and_(
                Leave.approval_status == "approved",
                Leave.start_date <= t,
                Leave.end_date >= f,
            )
        ).all()
        for l in leave_list:
            if l.keycloak_user_id:
                ld = max(l.start_date, f)
                rd = min(l.end_date, t)
                current = ld
                while current <= rd:
                    leave_covered.add((l.keycloak_user_id, current))
                    current += timedelta(days=1)

        shift_query = db.query(Shift).filter(
            and_(
                Shift.date >= f,
                Shift.date <= t,
                Shift.keycloak_user_id.notin_({uid for uid, _ in covered}) if covered else True,
            )
        )
        for s in shift_query.order_by(Shift.date.desc()).all():
            if (s.keycloak_user_id, s.date) in leave_covered:
                continue
            emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == s.keycloak_user_id).first()
            record = _build_virtual_record(db, s, emp)
            result.append(record)
            covered.add((s.keycloak_user_id, s.date))

        for l in leave_list:
            if not l.keycloak_user_id:
                continue
            ld = max(l.start_date, f)
            rd = min(l.end_date, t)
            current = ld
            while current <= rd:
                if (l.keycloak_user_id, current) not in covered:
                    emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == l.keycloak_user_id).first()
                    record = {
                        "id": None, "keycloakUserId": l.keycloak_user_id,
                        "attendanceDate": current.isoformat(),
                        "checkInTime": None, "checkInLat": None, "checkInLng": None, "checkInLocationName": None,
                        "checkOutTime": None, "checkOutLat": None, "checkOutLng": None, "checkOutLocationName": None,
                        "locationId": None, "officeName": None,
                        "workLocationStatus": None, "shiftCode": None, "shiftName": None,
                        "totalHours": None, "isLate": False, "status": "present",
                        "isManualEntry": False, "isSynced": False, "remarks": None,
                        "userEmail": emp.user_email if emp else "",
                        "userName": f"{emp.first_name} {emp.last_name}".strip() if emp else "",
                        "userDesignation": emp.designation if emp else None,
                        "derivedStatus": "on_leave",
                        "isAtAssignedLocation": None,
                        "dayName": current.strftime("%A"),
                        "shiftProject": None,
                        "onLeave": True,
                        "leaveType": l.leave_type,
                        "leaveStartDate": l.start_date.isoformat() if l.start_date else None,
                        "leaveEndDate": l.end_date.isoformat() if l.end_date else None,
                        "leaveStartDay": l.start_date.strftime("%A") if l.start_date else None,
                        "leaveEndDay": l.end_date.strftime("%A") if l.end_date else None,
                        "endedBy": None, "endedByRole": None,
                    }
                    result.append(record)
                    covered.add((l.keycloak_user_id, current))
                current += timedelta(days=1)

    return {"total": len(result), "records": result}


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

    if attendance.check_in_time is not None and attendance.keycloak_user_id and attendance.attendance_date:
        shift_rec = db.query(Shift).filter(
            Shift.keycloak_user_id == attendance.keycloak_user_id,
            Shift.date == attendance.attendance_date,
        ).first()
        if shift_rec:
            shift_rec.status = "Yet to start"

    db.delete(attendance)
    db.commit()

    logger.info(f"Admin {current_user['email']} deleted attendance {attendance_id}")
