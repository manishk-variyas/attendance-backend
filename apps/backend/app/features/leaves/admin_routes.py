from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.features.leaves.schemas.leaves import (
    LeaveBalanceCreate,
    LeaveBalanceUpdate,
    LeaveBalanceResponse,
)
from app.features.auth.dependencies import get_current_user, require_admin
from app.services.database.leave_service import LeaveBalanceService
from app.models.leave_balance import LeaveBalance
from app.models.employee_master import EmployeeMaster
from app.services.database.base_service import BaseService

router = APIRouter(prefix="/admin/leave-balances", tags=["admin-leave-balances"])


def _to_response(b: LeaveBalance) -> dict:
    return {
        "id": str(b.id),
        "keycloak_user_id": b.keycloak_user_id,
        "year": b.year,
        "total_earned": b.total_earned,
        "used_earned": b.used_earned,
        "accrued_compoff": b.accrued_compoff,
        "consumed_compoff": b.consumed_compoff,
        "unpaid": b.unpaid,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
    }


@router.post("", response_model=LeaveBalanceResponse, status_code=status.HTTP_201_CREATED)
async def create_leave_balance(
    payload: LeaveBalanceCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    emp_svc = BaseService[EmployeeMaster](db)
    emp = emp_svc.fetch_one(EmployeeMaster, user_email=payload.user_email)
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(
            status_code=404,
            detail="Employee not found or not onboarded (no keycloak_user_id)",
        )

    bal_svc = LeaveBalanceService(db)
    existing = bal_svc.fetch_one(
        LeaveBalance,
        keycloak_user_id=emp.keycloak_user_id,
        year=payload.year,
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Leave balance already exists for {payload.user_email} in year {payload.year}",
        )

    bal = bal_svc.create(
        LeaveBalance,
        keycloak_user_id=emp.keycloak_user_id,
        year=payload.year,
        total_earned=payload.total_earned,
        used_earned=payload.used_earned,
        accrued_compoff=payload.accrued_compoff,
        consumed_compoff=payload.consumed_compoff,
        unpaid=payload.unpaid,
    )
    return _to_response(bal)


@router.get("", response_model=List[LeaveBalanceResponse])
async def list_leave_balances(
    user_email: Optional[str] = Query(None, description="Filter by employee email"),
    year: Optional[int] = Query(None, description="Filter by year"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
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
    balances = query.all()
    return [_to_response(b) for b in balances]


@router.get("/{balance_id}", response_model=LeaveBalanceResponse)
async def get_leave_balance(
    balance_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = LeaveBalanceService(db)
    bal = svc.fetch_one(LeaveBalance, id=balance_id)
    if not bal:
        raise HTTPException(status_code=404, detail="Leave balance not found")
    return _to_response(bal)


@router.put("/by-user", response_model=LeaveBalanceResponse)
async def update_leave_balance_by_user(
    user_email: str = Query(..., description="Employee email"),
    year: int = Query(..., description="Year"),
    payload: LeaveBalanceUpdate = ...,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    emp_svc = BaseService[EmployeeMaster](db)
    emp = emp_svc.fetch_one(EmployeeMaster, user_email=user_email)
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(status_code=404, detail="Employee not found or not onboarded")

    bal_svc = LeaveBalanceService(db)
    bal = bal_svc.fetch_one(LeaveBalance, keycloak_user_id=emp.keycloak_user_id, year=year)
    if not bal:
        raise HTTPException(status_code=404, detail="Leave balance not found for this user/year")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        return _to_response(bal)

    updated = bal_svc.update(LeaveBalance, bal.id, **update_data)
    return _to_response(updated)


@router.put("/{balance_id}", response_model=LeaveBalanceResponse)
async def update_leave_balance(
    balance_id: str,
    payload: LeaveBalanceUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = LeaveBalanceService(db)
    existing = svc.fetch_one(LeaveBalance, id=balance_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Leave balance not found")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        return _to_response(existing)

    bal = svc.update(LeaveBalance, balance_id, **update_data)
    return _to_response(bal)


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
