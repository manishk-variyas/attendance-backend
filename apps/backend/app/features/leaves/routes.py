from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from typing import List, Optional
from app.features.auth.dependencies import get_current_user, require_admin
from app.features.leaves.schemas.leaves import (
    LeaveApplyRequest, 
    LeaveHistoryItem, 
    LeaveStats, 
    Holiday
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
    limit: int = Query(10, ge=1),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can view leave history."
        )
    return await leave_service.get_user_leave_history(email, current_user, limit, skip)

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
    limit: int = Query(10, ge=1),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    user_id = current_user.get("sub")
    email = current_user.get("email")
    roles = current_user.get("roles", [])

    if team:
        if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
            raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")
        rm_user = await redmine_service.get_user_by_email(email)
        if not rm_user:
            raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
        my_projects = await redmine_service.get_projects_for_user(rm_user["id"])
        all_member_rm_ids = set()
        for p in my_projects:
            members = await redmine_service.get_project_members(p.id)
            for m in members:
                if m.get("user_id"):
                    all_member_rm_ids.add(m["user_id"])
        emp_q = leave_service.leave_db.db.query(EmployeeMaster).filter(
            EmployeeMaster.redmine_user_id.in_(all_member_rm_ids)
        ).all()
        member_emails = [e.user_email for e in emp_q if e.user_email]
        if not member_emails:
            return {"total": 0, "records": []}
        records = await leave_service.get_team_leaves(member_emails, from_date, to_date)
        return {"total": len(records), "records": records}

    records = await leave_service.get_leave_history(user_id, limit, skip)
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
    #roles = current_user.get("roles", [])
    #if "admin" not in roles:
    #    raise HTTPException(
    #        status_code=status.HTTP_403_FORBIDDEN,
    #        detail="Only Administrators can upload holidays."
    #    )
    correlation_id = request.state.correlation_id
    client_ip = request.client.host if request.client else "-"
    
    # if not file.filename.endswith(('.xlsx', '.xls')):
    #     raise HTTPException(status_code=400, detail="Invalid file type. Please upload an Excel file.")

    # # Validate file size (Limit to 5MB)
    # MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    # if file.size and file.size > MAX_FILE_SIZE:
    #     raise HTTPException(status_code=413, detail="File size exceeds the 5MB limit.")

    # contents = await file.read()
    # if len(contents) > MAX_FILE_SIZE:
    #     raise HTTPException(status_code=413, detail="File content exceeds the 5MB limit.")

    # result = await leave_service.upload_holidays_from_excel(contents)
    # return result
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

@router.post("/apply")
async def apply_leave(
    leave_data: LeaveApplyRequest,
    current_user: dict = Depends(get_current_user),
    leave_service: LeaveBusinessService = Depends(get_leave_business_service)
):
    """Submit a new leave application."""
    user_id = current_user.get("sub")
    email = current_user.get("email")
    result = await leave_service.apply_for_leave(user_id, email, leave_data)
    leave_id = result["leave_id"]
    bal = result["balance"]

    # Notify the approver
    return {"message": "Leave application submitted successfully", "leave_id": leave_id, "balance": bal}

@router.post("/emergency")
async def emergency_leave(
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
