import asyncio
import logging
from datetime import datetime, date, time, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.core.database import SessionLocal
from app.models.attendance import Attendance
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.employee_master import EmployeeMaster
from app.models.leave import Leave
from app.services.email_service import email_service

logger = logging.getLogger(__name__)


def _safe_zone(tz_str: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _shift_end_datetime(shift_date: date, start_time, end_time, tz: ZoneInfo) -> datetime:
    if start_time and end_time and end_time <= start_time:
        shift_date += timedelta(days=1)
    return datetime.combine(shift_date, end_time, tzinfo=tz)


def run_attendance_background_sweep():
    """Runs periodic background sweep for auto-checkout (10 PM cutoff) and missed check-in email alerts."""
    db: Session = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()

        # ── 1. Auto-Checkout Night Sweeper ─────────────────────────────────────────
        open_attendances = db.query(Attendance).filter(
            and_(
                Attendance.check_out_time.is_(None),
                Attendance.attendance_date >= today_ist - timedelta(days=3),
            )
        ).all()

        for att in open_attendances:
            try:
                if not att.shift_code:
                    continue

                sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == att.shift_code).first()
                if not sd or not sd.end_time:
                    continue

                tz = _safe_zone(sd.timezone or "Asia/Kolkata")
                effective_date = att.attendance_date
                shift_end = _shift_end_datetime(effective_date, sd.start_time, sd.end_time, tz)

                # 10:00 PM (22:00) Night Cutoff
                cutoff_time = datetime.combine(effective_date, time(22, 0), tzinfo=tz)
                cutoff_dt = max(cutoff_time, shift_end)

                if now_utc > cutoff_dt:
                    att.check_out_time = shift_end
                    att.remarks = ", ".join(filter(None, [att.remarks, "Auto-checkout: shift ended (10 PM cutoff)"]))
                    
                    shift_rec = db.query(Shift).filter(
                        Shift.keycloak_user_id == att.keycloak_user_id,
                        Shift.date <= effective_date,
                        Shift.end_date >= effective_date,
                    ).first()
                    if shift_rec:
                        shift_rec.status = "Ended"
                    
                    db.commit()
                    logger.info(f"[Background Sweeper] Auto-checked out attendance {att.id} for user {att.keycloak_user_id}")
            except Exception as e:
                db.rollback()
                logger.error(f"[Background Sweeper] Error auto-checking out attendance {att.id}: {e}")

        # ── 2. Missed Check-In Email Sweeper ────────────────────────────────────────
        recent_shifts = db.query(Shift).filter(
            and_(
                Shift.checkin_reminder_sent.is_(False),
                Shift.date >= today_ist - timedelta(days=1),
                Shift.date <= today_ist + timedelta(days=1),
            )
        ).all()

        for shift in recent_shifts:
            try:
                sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == shift.shift_code).first()
                if not sd or not sd.start_time:
                    continue

                tz = _safe_zone(sd.timezone or "Asia/Kolkata")
                start_dt = datetime.combine(shift.date, sd.start_time, tzinfo=tz)

                # Check if current time > shift_start + 2 hours
                if now_utc > start_dt + timedelta(hours=2):
                    # Check if user has an attendance record for this shift date
                    att_exists = db.query(Attendance).filter(
                        and_(
                            Attendance.keycloak_user_id == shift.keycloak_user_id,
                            Attendance.attendance_date.in_([
                                shift.date - timedelta(days=1),
                                shift.date,
                                shift.date + timedelta(days=1),
                            ]),
                        )
                    ).first()

                    # Check if user is on leave
                    on_leave = db.query(Leave).filter(
                        Leave.keycloak_user_id == shift.keycloak_user_id,
                        Leave.approval_status.in_(["approved", "emergency"]),
                        Leave.start_date <= shift.date,
                        Leave.end_date >= shift.date,
                    ).first()

                    if not att_exists and not on_leave and shift.work_location_status != "LEAVE":
                        emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == shift.keycloak_user_id).first()
                        user_email = emp.user_email if emp else shift.user_email
                        emp_name = f"{emp.first_name} {emp.last_name}".strip() if (emp and emp.first_name) else (user_email or "Employee")

                        if user_email:
                            start_str = sd.start_time.strftime("%H:%M")
                            email_service.send_missed_checkin_reminder(
                                to=user_email,
                                employee_name=emp_name,
                                shift_date=shift.date.isoformat(),
                                shift_start_time=start_str,
                            )
                            logger.info(f"[Background Sweeper] Sent missed check-in email to {user_email} for shift {shift.date}")

                    shift.checkin_reminder_sent = True
                    db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"[Background Sweeper] Error processing shift reminder {shift.id}: {e}")

    except Exception as outer_err:
        logger.error(f"[Background Sweeper] Sweeper loop error: {outer_err}")
    finally:
        db.close()


async def attendance_sweep_loop(interval_seconds: int = 900):
    """Background asyncio task that runs the attendance sweeper periodically."""
    logger.info("[Background Sweeper] Starting attendance background sweep loop...")
    while True:
        try:
            run_attendance_background_sweep()
        except Exception as e:
            logger.error(f"[Background Sweeper] Unexpected loop exception: {e}")
        await asyncio.sleep(interval_seconds)
