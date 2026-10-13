"""Per-shift punches → daily attendance rollup.

Multi-shift source of truth lives in `attendance_punches`; `attendance` stays the
daily rollup that every existing API reads. See docs/multi-shift-punch-plan.md.

Phase 1 (this file): foundation only — nothing is wired into the write paths yet,
so behavior is unchanged. `summarize_punches` is pure and unit-testable.
"""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.attendance import Attendance
from app.models.attendance_punch import AttendancePunch


def summarize_punches(punches: list) -> Optional[dict]:
    """Compute the daily rollup fields from a day's punches (pure function).

    Rules (docs/multi-shift-punch-plan.md §7):
      - present if any punch has a check-in
      - check_in = earliest, check_out = latest
      - is_late = any punch late
      - work_location_status = OFFICE if any OFFICE, else WFH if any WFH
      - shift_id only when the day has exactly one shift
    Returns None when there are no active punches.
    """
    active = [p for p in punches if not p.is_deleted]
    if not active:
        return None

    present = [p for p in active if p.check_in_time is not None]
    check_ins = [p.check_in_time for p in present]
    check_outs = [p.check_out_time for p in active if p.check_out_time is not None]

    locations = {p.work_location_status for p in present if p.work_location_status}
    if "OFFICE" in locations:
        work_location = "OFFICE"
    elif "WFH" in locations:
        work_location = "WFH"
    elif locations:
        work_location = sorted(locations)[0]
    else:
        work_location = None

    shift_ids = {p.shift_id for p in active if p.shift_id is not None}
    single_shift = next(iter(shift_ids)) if len(shift_ids) == 1 else None

    hours = [float(p.total_hours) for p in active if p.total_hours is not None]

    return {
        "status": "present" if present else "absent",
        "check_in_time": min(check_ins) if check_ins else None,
        "check_out_time": max(check_outs) if check_outs else None,
        "total_hours": round(sum(hours), 2) if hours else None,
        "is_late": any(p.is_late for p in present),
        "work_location_status": work_location,
        "shift_id": single_shift,
        "punch_count": len(active),
    }


def recompute_daily_attendance(
    db: Session, keycloak_user_id: str, attendance_date: date
) -> Optional[dict]:
    """Rebuild the `attendance` rollup row for one user/day from its punches.

    Does not commit — callers commit as part of their transaction. `total_hours`
    on `attendance` is a generated column (span of check_in..check_out), so it is
    left to the DB; for single-shift days it equals the punch hours.
    """
    punches = db.query(AttendancePunch).filter(
        AttendancePunch.keycloak_user_id == keycloak_user_id,
        AttendancePunch.attendance_date == attendance_date,
        AttendancePunch.is_deleted.is_(False),
    ).all()
    summary = summarize_punches(punches)
    if summary is None:
        return None

    att = db.query(Attendance).filter(
        Attendance.keycloak_user_id == keycloak_user_id,
        Attendance.attendance_date == attendance_date,
    ).first()
    if att is None:
        att = Attendance(keycloak_user_id=keycloak_user_id, attendance_date=attendance_date)
        db.add(att)

    att.status = summary["status"]
    att.check_in_time = summary["check_in_time"]
    att.check_out_time = summary["check_out_time"]
    att.total_hours = summary["total_hours"]
    att.is_late = summary["is_late"]
    if summary["work_location_status"]:
        att.work_location_status = summary["work_location_status"]
    att.shift_id = summary["shift_id"]
    return summary
