from datetime import date
from typing import Any, Dict, FrozenSet, Iterable, Optional

from sqlalchemy import select

from app.models.employee_leave_balance import EmployeeLeaveBalance, EmployeeLeaveBalanceMonthly
from app.models.leave import Leave, LeaveStatus
from app.models.leave_type import LeaveTypeConfig

# Leave statuses that consume a day from the balance. Every leave that is
# currently active/granted counts as used:
#   - approved
#   - emergency
#   - cancellation_requested (still consumed while the cancellation is pending)
#   - cancellation_rejected   (still active, the cancellation was refused)
# Only pending / rejected / cancelled leaves are excluded.
COUNTED_STATUSES: FrozenSet[str] = frozenset({
    LeaveStatus.APPROVED.value,
    LeaveStatus.EMERGENCY.value,
    LeaveStatus.CANCELLATION_REQUESTED.value,
    LeaveStatus.CANCELLATION_REJECTED.value,
})


def fiscal_year_for(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def leave_codes_for_type(code: str) -> list:
    # Old leaves table stored comp-off as "PL"; new leave_types uses "CO"
    if code == "CO":
        return ["CO", "PL"]
    return [code]


def type_total(db, user_id: str, leave_type_id: str, fiscal_year: int) -> float:
    """Total entitlement for a leave type: carry_forward + adjustment + monthly accruals."""
    bal = db.execute(
        select(EmployeeLeaveBalance).where(
            EmployeeLeaveBalance.keycloak_user_id == user_id,
            EmployeeLeaveBalance.leave_type_id == leave_type_id,
            EmployeeLeaveBalance.fiscal_year == fiscal_year,
        )
    ).scalars().first()
    if not bal:
        return 0.0
    monthly_total = sum(
        float(m.accrued or 0)
        for m in db.execute(
            select(EmployeeLeaveBalanceMonthly).where(
                EmployeeLeaveBalanceMonthly.leave_balance_id == bal.id
            )
        ).scalars().all()
    )
    return float(bal.carry_forward or 0) + float(bal.adjustment or 0) + monthly_total


def availed_days(
    db,
    user_id: str,
    codes: Iterable[str],
    fiscal_year: int,
    *,
    up_to: Optional[date] = None,
) -> float:
    """Sum of consumed leave days in a fiscal year for the given leave codes.

    Counts every leave whose status is in COUNTED_STATUSES. When ``up_to`` is
    given only leaves whose end_date is on/before that date are counted,
    otherwise all leaves in the fiscal year count (including future-dated
    approved leaves, so an approval deducts immediately).
    """
    stmt = select(Leave).where(
        Leave.keycloak_user_id == user_id,
        Leave.approval_status.in_(COUNTED_STATUSES),
        Leave.start_date >= date(fiscal_year, 4, 1),
    )
    if up_to is not None:
        stmt = stmt.where(Leave.end_date <= up_to)

    leaves = db.execute(stmt).scalars().all()
    code_set = set(codes)
    return sum(
        (lv.end_date - lv.start_date).days + 1
        for lv in leaves if lv.leave_type in code_set
    )


def balance_for(
    db,
    user_id: str,
    code: str,
    fiscal_year: int,
    *,
    up_to: Optional[date] = None,
) -> Dict[str, float]:
    lt = db.execute(
        select(LeaveTypeConfig).where(LeaveTypeConfig.code == code)
    ).scalars().first()
    if not lt:
        return {"total": 0.0, "used": 0.0, "remaining": 0.0}

    total = type_total(db, user_id, lt.id, fiscal_year)
    used = availed_days(db, user_id, leave_codes_for_type(code), fiscal_year, up_to=up_to)
    remaining = total - used
    return {"total": round(total, 2), "used": round(used, 2), "remaining": round(remaining, 2)}


def apply_balance(
    db,
    user_id: str,
    lt: LeaveTypeConfig,
    start_date: date,
    requested_days: int,
) -> Dict[str, Any]:
    fiscal_year = fiscal_year_for(start_date)
    result: Dict[str, Any] = {"requested_days": requested_days, "remaining": None, "will_be_negative": None}

    bal = db.execute(
        select(EmployeeLeaveBalance).where(
            EmployeeLeaveBalance.keycloak_user_id == user_id,
            EmployeeLeaveBalance.leave_type_id == lt.id,
            EmployeeLeaveBalance.fiscal_year == fiscal_year,
        )
    ).scalars().first()
    if not bal:
        return result

    total = type_total(db, user_id, lt.id, fiscal_year)
    used = availed_days(db, user_id, leave_codes_for_type(lt.code), fiscal_year, up_to=start_date)
    remaining = total - used
    result["remaining"] = remaining
    result["will_be_negative"] = remaining < requested_days
    return result


def fiscal_month_for(d: date) -> int:
    # Fiscal year starts in April: 1 = Apr ... 12 = Mar
    return ((d.month - 4) % 12) + 1


def credit_comp_off(db, user_id: str, on_date: date) -> None:
    """Credit one comp-off day to the CO monthly accrual for working a holiday."""
    lt = db.execute(
        select(LeaveTypeConfig).where(LeaveTypeConfig.code == "CO")
    ).scalars().first()
    if not lt:
        return

    fiscal_year = fiscal_year_for(on_date)
    bal = db.execute(
        select(EmployeeLeaveBalance).where(
            EmployeeLeaveBalance.keycloak_user_id == user_id,
            EmployeeLeaveBalance.leave_type_id == lt.id,
            EmployeeLeaveBalance.fiscal_year == fiscal_year,
        )
    ).scalars().first()

    if not bal:
        bal = EmployeeLeaveBalance(
            keycloak_user_id=user_id,
            leave_type_id=lt.id,
            fiscal_year=fiscal_year,
            carry_forward=0.0,
            adjustment=0.0,
        )
        db.add(bal)
        db.flush()

    month = fiscal_month_for(on_date)
    monthly = db.execute(
        select(EmployeeLeaveBalanceMonthly).where(
            EmployeeLeaveBalanceMonthly.leave_balance_id == bal.id,
            EmployeeLeaveBalanceMonthly.month == month,
        )
    ).scalars().first()

    if monthly:
        monthly.accrued = (monthly.accrued or 0) + 1
    else:
        db.add(EmployeeLeaveBalanceMonthly(
            leave_balance_id=bal.id,
            month=month,
            accrued=1.0,
        ))
