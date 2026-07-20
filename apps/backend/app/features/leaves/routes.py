from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from datetime import datetime
from typing import List, Optional
from app.features.auth.dependencies import get_current_user, require_admin
from app.core.limiter import limiter
from app.features.leaves.schemas.leaves import (
    LeaveApplyRequest, 
    LeaveHistoryItem, 
    LeaveStats, 
    Holiday,
    BatchLeaveRequest
)
from app.features.leaves.services.leave_service import LeaveBusinessService
from app.services.database.dependencies import get_leave_business_service
from app.features.redmine.service import redmine_service
from app.models.employee_master import EmployeeMaster
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaves", tags=["leaves"])

@router.get("/stats", response_model=LeaveStats)
async def get_stats(
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    """Get summarized leave stats for the dashboard cards."""
    user_id = current_user.get("sub")
    stats = await leave_service.get_leave_stats(user_id)
    return stats

@router.get("/admin/{email}", response_model=List[LeaveHistoryItem])
async def get_user_leave_history(
    email: str,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can view leave history."
        )
    return await leave_service.get_user_leave_history(email, current_user)

@router.get("/users")
async def list_leave_users(
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    """Get list of users whose leaves can be viewed.
    - Admin: All Redmine users.
    - PM: Users sharing at least one project.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can view team leave history."
        )
    return await leave_service.get_visible_leave_users(current_user)

@router.get("/history")
async def get_history(
    team: Optional[bool] = Query(None, description="PM/PC: view all team members' leaves"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    user_id = current_user.get("sub")
    email = current_user.get("email")
    roles = current_user.get("roles", [])

    if team:
        if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
            raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")
        from app.features.redmine.sql_service import RedmineSQLService
        sql = RedmineSQLService(leave_service.leave_db.db)
        rm_user = sql.get_user_by_email(email)
        if not rm_user:
            raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
        member_rm_ids = sql.get_team_member_ids(rm_user["id"])
        member_rm_ids.add(rm_user["id"])
        emp_q = leave_service.leave_db.db.query(EmployeeMaster).filter(
            EmployeeMaster.redmine_user_id.in_(member_rm_ids)
        ).all()
        member_emails = [e.user_email for e in emp_q if e.user_email]
        if not member_emails:
            return {"total": 0, "records": []}
        records = await leave_service.get_team_leaves(member_emails, from_date, to_date)
        return {"total": len(records), "records": records}

    records = await leave_service.get_leave_history(user_id)
    return {"total": len(records), "records": records}

@router.get("/holidays", response_model=List[Holiday])
async def get_holidays(
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    return await leave_service.get_holidays(limit, skip)

from fastapi import UploadFile, File

@router.post("/holidays/upload")
async def upload_holidays(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    _:None = Depends(require_admin),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    """Upload Excel file to sync holidays. (Admins only)"""
    correlation_id = request.state.correlation_id
    try:
        MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

        if not file.filename.endswith(('.xlsx', '.xls')):
            raise HTTPException(status_code=400, detail="Invalid file type. Please upload an Excel file.")

        # Validate file size (Limit to 5MB)
        if file.size and file.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File size exceeds the 5MB limit.")

        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File content exceeds the 5MB limit.")

        result = await leave_service.upload_holidays_from_excel(contents)
        return result
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error uploading holidays: {e}")
        raise HTTPException(status_code=500, detail="Error uploading holidays")
    finally:
        await file.close()

@router.post("/validate")
async def validate_leave_dates(
    payload: dict,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    return await leave_service.validate_leave_dates(
        datetime.fromisoformat(payload["start_date"]),
        datetime.fromisoformat(payload["end_date"]),
        current_user.get("sub"),
        [datetime.fromisoformat(d) for d in payload["leave_dates"]] if "leave_dates" in payload else None,
    )

@router.post("/apply")
@limiter.limit("5/minute")
async def apply_leave(
    request: Request,
    leave_data: LeaveApplyRequest,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    user_id = current_user.get("sub")
    email = current_user.get("email")
    result = await leave_service.apply_for_leave(user_id, email, leave_data)
    leave_id = result["leave_id"]
    bal = result["balance"]

    return {"message": "Leave application submitted successfully", "leave_id": leave_id, "leave_ids": result["leave_ids"], "created": result["created"], "balance": bal}

@router.post("/emergency")
@limiter.limit("3/minute")
async def emergency_leave(
    request: Request,
    payload: Optional[dict] = None,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    user_id = current_user.get("sub")
    email = current_user.get("email")
    reason = payload.get("reason") if payload else None
    return leave_service.emergency_leave(user_id, email, reason)

@router.get("/pending")
async def get_pending_leaves(
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    """
    Get all pending leave requests.
    - PM: Gets pending leaves for TRs in their projects.
    - Admin: Gets all pending leaves.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can view pending leaves."
        )

    pending_list = await leave_service.get_pending_leaves(current_user)
    return pending_list

@router.post("/batch/approve")
async def batch_approve_leaves(
    payload: BatchLeaveRequest,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Admin, PM, or PC can approve leaves.")
    return await leave_service.batch_approve_leaves(payload.leave_ids, current_user)


@router.post("/batch/reject")
async def batch_reject_leaves(
    payload: BatchLeaveRequest,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Admin, PM, or PC can reject leaves.")
    return await leave_service.batch_reject_leaves(payload.leave_ids, current_user)


@router.post("/{leave_id}/approve")
async def approve_leave(
    leave_id: str,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    """
    Approve a leave application.
    - PM: Can approve leaves for TRs in their projects.
    - Admin: Full access.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can approve leaves."
        )

    success = await leave_service.approve_leave(leave_id, current_user)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave application not found or unauthorized."
        )

    return {"message": "Leave application approved successfully"}


@router.post("/{leave_id}/reject")
async def reject_leave(
    leave_id: str,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    """
    Reject a leave application.
    - PM: Can reject leaves for TRs in their projects.
    - Admin: Full access.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can reject leaves."
        )

    success = await leave_service.reject_leave(leave_id, current_user)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave application not found or unauthorized."
        )

    return {"message": "Leave application rejected successfully"}
