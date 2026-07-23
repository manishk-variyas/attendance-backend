from fastapi import APIRouter, Depends, HTTPException, Query, status
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.features.leaves.schemas.leaves import (
    LeaveBalanceCreate,
    LeaveBalanceUpdate,
    LeaveBalanceResponse,
)
from app.features.auth.dependencies import get_current_user, require_admin, require_admin_or_pm
from app.services.database.leave_service import LeaveBalanceService
from app.models.leave_balance import LeaveBalance
from app.models.employee_master import EmployeeMaster
from app.services.database.base_service import BaseService


def _fetch_emp_map(db: Session, balances: list) -> dict:
    ids = {b.keycloak_user_id for b in balances if b.keycloak_user_id}
    if not ids:
        return {}
    emps = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id.in_(ids)).all()
    return {e.keycloak_user_id: e for e in emps}


router = APIRouter(prefix="/admin/leave-balances", tags=["admin-leave-balances"])


def _to_response(b: LeaveBalance, emp_map: dict = None) -> dict:
    emp = emp_map.get(b.keycloak_user_id) if emp_map else None
    return {
        "id": str(b.id),
        "keycloak_user_id": b.keycloak_user_id,
        "year": b.year,
        "month": b.month,
        "userName": f"{emp.first_name} {emp.last_name}".strip() if emp else None,
        "userDesignation": emp.designation if emp else None,
        "total_earned": b.total_earned,
        "used_earned": b.used_earned,
        "accrued_compoff": b.accrued_compoff,
        "consumed_compoff": b.consumed_compoff,
        "unpaid": b.unpaid,
        "modified_by": b.modified_by,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
    }


def _validate_joining(emp: EmployeeMaster, year: int, month: int | None):
    if not emp.joining_date:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create leave balance: {emp.first_name} {emp.last_name} has no joining_date. Set it first.",
        )
    period = f"{year}-{month:02d}" if month else str(year)
    if year < emp.joining_date.year or (
        year == emp.joining_date.year
        and month is not None
        and month < emp.joining_date.month
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Period {period} is before employee's joining date ({emp.joining_date.isoformat()})",
        )


@router.post("", response_model=LeaveBalanceResponse, status_code=status.HTTP_201_CREATED)
async def create_leave_balance(
    payload: LeaveBalanceCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    emp_svc = BaseService[EmployeeMaster](db)
    if payload.keycloak_user_id:
        emp = emp_svc.fetch_one(EmployeeMaster, keycloak_user_id=payload.keycloak_user_id)
    elif payload.user_email:
        emp = emp_svc.fetch_one(EmployeeMaster, user_email=payload.user_email)
    else:
        raise HTTPException(status_code=400, detail="Provide either user_email or keycloak_user_id")
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(
            status_code=404,
            detail="Employee not found or not onboarded (no keycloak_user_id)",
        )

    _validate_joining(emp, payload.year, payload.month)

    month = payload.month
    if month is None and emp.joining_date and emp.joining_date.year == payload.year:
        month = emp.joining_date.month

    roles = current_user.get("roles", [])
    if "Admin" not in roles:
        from app.features.redmine.sql_service import RedmineSQLService
        sql = RedmineSQLService(db)
        pm_user = sql.get_user_by_email(current_user.get("email"))
        if not pm_user:
            raise HTTPException(status_code=403, detail="Not found in Redmine")
        pm_emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == current_user.get("email")).first()
        if not sql.check_project_access(pm_user["id"], emp.redmine_user_id):
            raise HTTPException(status_code=403, detail="You can only edit leave balances for team members")

    bal_svc = LeaveBalanceService(db)
    existing = bal_svc.fetch_one(
        LeaveBalance,
        keycloak_user_id=emp.keycloak_user_id,
        year=payload.year,
        month=month,
    )
    if existing:
        update_data = payload.model_dump(exclude={"user_email", "keycloak_user_id"})
        update_data["modified_by"] = current_user.get("email")
        updated = bal_svc.update(LeaveBalance, existing.id, **update_data)
        emp_map = _fetch_emp_map(db, [updated])
        return _to_response(updated, emp_map)

    bal = bal_svc.create(
        LeaveBalance,
        keycloak_user_id=emp.keycloak_user_id,
        year=payload.year,
        month=month,
        total_earned=payload.total_earned,
        used_earned=payload.used_earned,
        accrued_compoff=payload.accrued_compoff,
        consumed_compoff=payload.consumed_compoff,
        unpaid=payload.unpaid,
        modified_by=current_user.get("email"),
    )
    return _to_response(bal, _fetch_emp_map(db, [bal]))


@router.get("", response_model=List[LeaveBalanceResponse])
async def list_leave_balances(
    user_email: Optional[str] = Query(None, description="Filter by employee email"),
    year: Optional[int] = Query(None, ge=2000, description="Filter by year"),
    month: Optional[int] = Query(None, ge=1, le=12, description="Filter by month"),
    team: Optional[bool] = Query(None, description="PM/PC: view team members' balances"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    roles = current_user.get("roles", [])

    if team:
        if "Admin" in roles:
            query = db.query(LeaveBalance)
        elif "Project Manager" in roles or "Project Coordinator" in roles:
            from app.features.redmine.sql_service import RedmineSQLService
            sql = RedmineSQLService(db)
            pm_user = sql.get_user_by_email(current_user.get("email"))
            if not pm_user:
                return []
            member_ids = sql.get_team_member_ids(pm_user["id"])
            member_ids.add(pm_user["id"])
            emps = db.query(EmployeeMaster).filter(
                EmployeeMaster.redmine_user_id.in_(member_ids)
            ).all()
            allowed_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
            query = db.query(LeaveBalance).filter(LeaveBalance.keycloak_user_id.in_(allowed_ids))
        else:
            raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")
        if year:
            query = query.filter(LeaveBalance.year == year)
        if month:
            query = query.filter(LeaveBalance.month == month)
        balances = query.order_by(LeaveBalance.keycloak_user_id, LeaveBalance.year, LeaveBalance.month).all()
        emp_map = _fetch_emp_map(db, balances)
        balances = [b for b in balances if _is_after_joining(b, emp_map)]
        return [_to_response(b, emp_map) for b in balances]

    svc = LeaveBalanceService(db)
    query = db.query(LeaveBalance)

    if user_email:
        emp_svc = BaseService[EmployeeMaster](db)
        emp = emp_svc.fetch_one(EmployeeMaster, user_email=user_email)
        if not emp or not emp.keycloak_user_id:
            raise HTTPException(status_code=404, detail="Employee not found or not onboarded")
        query = query.filter(LeaveBalance.keycloak_user_id == emp.keycloak_user_id)
    if year:
        query = query.filter(LeaveBalance.year == year)
    if month:
        query = query.filter(LeaveBalance.month == month)
    balances = query.order_by(LeaveBalance.year, LeaveBalance.month).all()

    if not balances and user_email and not team:
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == user_email).first()
        if emp:
            return [{
                "id": "",
                "keycloak_user_id": emp.keycloak_user_id,
                "year": year or datetime.now().year,
                "month": month,
                "userName": f"{emp.first_name} {emp.last_name}".strip(),
                "userDesignation": emp.designation,
                "total_earned": 0.0,
                "used_earned": 0.0,
                "accrued_compoff": 0.0,
                "consumed_compoff": 0.0,
                "unpaid": 0.0,
                "modified_by": None,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }]
    emp_map = _fetch_emp_map(db, balances)
    balances = [b for b in balances if _is_after_joining(b, emp_map)]
    return [_to_response(b, emp_map) for b in balances]


def _is_after_joining(b: LeaveBalance, emp_map: dict) -> bool:
    emp = emp_map.get(b.keycloak_user_id) if emp_map else None
    if not emp or not emp.joining_date:
        return True
    if b.year < emp.joining_date.year:
        return False
    if b.year == emp.joining_date.year and b.month is not None and emp.joining_date.month is not None and b.month < emp.joining_date.month:
        return False
    return True


@router.get("/{balance_id}", response_model=LeaveBalanceResponse)
async def get_leave_balance(
    balance_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    svc = LeaveBalanceService(db)
    bal = svc.fetch_one(LeaveBalance, id=balance_id)
    if not bal:
        raise HTTPException(status_code=404, detail="Leave balance not found")
    return _to_response(bal, _fetch_emp_map(db, [bal]))


@router.put("/by-user", response_model=LeaveBalanceResponse)
async def update_leave_balance_by_user(
    user_email: str = Query(..., description="Employee email"),
    year: int = Query(..., ge=2000, description="Year"),
    month: Optional[int] = Query(None, ge=1, le=12, description="Month (skip for yearly)"),
    payload: LeaveBalanceUpdate = ...,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    emp_svc = BaseService[EmployeeMaster](db)
    emp = emp_svc.fetch_one(EmployeeMaster, user_email=user_email)
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(status_code=404, detail="Employee not found or not onboarded")

    _validate_joining(emp, year, month)

    if month is None and emp.joining_date and emp.joining_date.year == year:
        month = emp.joining_date.month

    roles = current_user.get("roles", [])
    if "Admin" not in roles:
        from app.features.redmine.sql_service import RedmineSQLService
        sql = RedmineSQLService(db)
        pm_user = sql.get_user_by_email(current_user.get("email"))
        if not pm_user:
            raise HTTPException(status_code=403, detail="Not found in Redmine")
        if not sql.check_project_access(pm_user["id"], emp.redmine_user_id):
            raise HTTPException(status_code=403, detail="You can only edit leave balances for team members")

    bal_svc = LeaveBalanceService(db)
    bal = bal_svc.fetch_one(LeaveBalance, keycloak_user_id=emp.keycloak_user_id, year=year, month=month)
    if not bal:
        raise HTTPException(status_code=404, detail="Leave balance not found for this user/year")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        return _to_response(bal, _fetch_emp_map(db, [bal]))

    update_data["modified_by"] = current_user.get("email")
    updated = bal_svc.update(LeaveBalance, bal.id, **update_data)
    return _to_response(updated, _fetch_emp_map(db, [updated]))


@router.put("/{balance_id}", response_model=LeaveBalanceResponse)
async def update_leave_balance(
    balance_id: str,
    payload: LeaveBalanceUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    svc = LeaveBalanceService(db)
    existing = svc.fetch_one(LeaveBalance, id=balance_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Leave balance not found")

    year = payload.year if payload.year is not None else existing.year
    month = payload.month if payload.month is not None else existing.month
    emp_svc = BaseService[EmployeeMaster](db)
    emp = emp_svc.fetch_one(EmployeeMaster, keycloak_user_id=existing.keycloak_user_id)
    if emp:
        _validate_joining(emp, year, month)

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        return _to_response(existing)

    update_data["modified_by"] = current_user.get("email")
    bal = svc.update(LeaveBalance, balance_id, **update_data)
    return _to_response(bal, _fetch_emp_map(db, [bal]))


@router.delete("/{balance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_leave_balance(
    balance_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = LeaveBalanceService(db)
    deleted = svc.delete(LeaveBalance, balance_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Leave balance not found")
