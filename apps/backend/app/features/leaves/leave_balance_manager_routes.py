from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional

from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_admin_or_pm
from app.features.leaves.schemas.leaves import EmployeeLeaveBalanceBulkCreate
from app.features.leaves.services.leave_service import LeaveBusinessService
from app.services.database.dependencies import get_leave_business_service
from app.middleware.logging import _get_client_ip
from app.utils.audit import audit_logger

router = APIRouter(prefix="/admin/leave-balance-manager", tags=["admin-leave-balance-manager"])


def _current_fiscal_year() -> int:
    from datetime import date
    today = date.today()
    return today.year if today.month >= 4 else today.year - 1


@router.get("/list")
async def get_leave_balance_list(
    fiscal_year: int = Query(None, ge=2000, description="Fiscal year (default current)"),
    search: Optional[str] = Query(None, description="Search by name, email, employee id"),
    sort_by: str = Query("userName", description="userName, designation, employee_id, total_available_leave"),
    sort_dir: str = Query("asc", description="asc or desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service),
):
    if fiscal_year is None:
        fiscal_year = _current_fiscal_year()
    return await leave_service.get_leave_balance_list(
        current_user, fiscal_year,
        search=search, sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
    )


@router.get("/{id}/grid")
async def get_leave_balance_grid(
    id: str,
    fiscal_year: int = Query(..., ge=2000, description="Fiscal year (e.g. 2026 = Apr 2026 - Mar 2027)"),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service),
):
    return await leave_service.get_leave_balance_grid(id, fiscal_year, current_user)


@router.post("/bulk")
@limiter.limit("10/minute")
async def save_leave_balance_bulk(
    request: Request,
    payload: EmployeeLeaveBalanceBulkCreate,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service),
):
    result = await leave_service.save_leave_balance_bulk(payload, current_user)
    audit_logger.info(
        f"Leave balance saved for employee {payload.id} FY {payload.fiscal_year} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "leave_balance_save",
                "username": current_user.get("username"),
                "employee_id": payload.id,
                "fiscal_year": payload.fiscal_year,
                "client_ip": _get_client_ip(request),
            },
        },
    )
    return result
