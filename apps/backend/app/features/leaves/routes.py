from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from typing import List
from bson import ObjectId
from app.features.auth.dependencies import get_current_user, require_admin
from app.features.leaves.schemas.leaves import (
    LeaveApplyRequest, 
    LeaveHistoryItem, 
    LeaveStats, 
    Holiday
)
from app.features.leaves.services.leave_service import leave_service
from app.features.notifications.services.notification_service import notification_service
from app.features.redmine.service import redmine_service
from app.core.mongodb import get_mongodb
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaves", tags=["leaves"])

@router.get("/stats", response_model=LeaveStats)
async def get_stats(current_user: dict = Depends(get_current_user)):
    """Get summarized leave stats for the dashboard cards."""
    user_id = current_user.get("sub")
    stats = await leave_service.get_leave_stats(user_id)
    return stats

@router.get("/admin/{email}", response_model=List[LeaveHistoryItem])
async def get_user_leave_history(
    email: str,
    limit: int = Query(10, ge=1),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Get leave history for a specific user.
    - Admin: Any user.
    - PM: Only users sharing a project.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admins and Project Managers can view other users' leave history."
        )
    history = await leave_service.get_user_leave_history(email, current_user, limit, skip)
    return history

@router.get("/users")
async def list_leave_users(
    current_user: dict = Depends(get_current_user)
):
    """Get list of users whose leaves can be viewed.
    - Admin: All Redmine users.
    - PM: Users sharing at least one project.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admins and Project Managers can view team leave history."
        )
    return await leave_service.get_visible_leave_users(current_user)

@router.get("/history", response_model=List[LeaveHistoryItem])
async def get_history(
    limit: int = Query(10, ge=1),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Get paginated leave application history."""
    user_id = current_user.get("sub")
    history = await leave_service.get_leave_history(user_id, limit, skip)
    return history

@router.get("/holidays", response_model=List[Holiday])
async def get_holidays(
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Get the holiday calendar with pagination."""
    return await leave_service.get_holidays(limit, skip)

from fastapi import UploadFile, File

@router.post("/holidays/upload")
async def upload_holidays(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    _:None = Depends(require_admin)
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
    current_user: dict = Depends(get_current_user)
):
    """Submit a new leave application."""
    user_id = current_user.get("sub")
    email = current_user.get("email")
    leave_id = await leave_service.apply_for_leave(user_id, email, leave_data)

    # Notify the approver
    if leave_data.approver_id:
        try:
            all_users = await redmine_service.get_all_users()
            approver = next(
                (u for u in all_users if str(u["id"]) == str(leave_data.approver_id)),
                None,
            )
            if approver:
                user_name = current_user.get("username") or email
                await notification_service.create_notification(
                    user_id=approver["email"],
                    type="leave_applied",
                    message=f"{user_name} applied for {leave_data.leave_type} leave",
                    from_user=email,
                    reference_id=leave_id,
                )
        except Exception as e:
            logger.warning(f"Failed to create notification: {e}")

    return {"message": "Leave application submitted successfully", "leave_id": leave_id}

@router.get("/pending")
async def get_pending_leaves(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all pending leave requests.
    - PM: Gets pending leaves for TRs in their projects.
    - Admin: Gets all pending leaves.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Project Managers and Administrators can view pending leaves."
        )

    pending_list = await leave_service.get_pending_leaves(current_user)
    return pending_list

@router.post("/{leave_id}/approve")
async def approve_leave(
    leave_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Approve a leave application.
    - PM: Can approve leaves for TRs in their projects.
    - Admin: Full access.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Project Managers and Administrators can approve leaves."
        )

    success = await leave_service.approve_leave(leave_id, current_user)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave application not found or unauthorized."
        )

    # Notify the applicant
    try:
        db = get_mongodb()
        leave_doc = await db["leaves"].find_one({"_id": ObjectId(leave_id)})
        if leave_doc:
            actor_name = current_user.get("username") or current_user.get("email")
            await notification_service.create_notification(
                user_id=leave_doc["user_email"],
                type="leave_approved",
                message=f"Your leave has been approved by {actor_name}",
                from_user=current_user["email"],
                reference_id=leave_id,
            )
    except Exception as e:
        logger.warning(f"Failed to create notification: {e}")

    return {"message": "Leave application approved successfully"}


@router.post("/{leave_id}/reject")
async def reject_leave(
    leave_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Reject a leave application.
    - PM: Can reject leaves for TRs in their projects.
    - Admin: Full access.
    """
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Project Managers and Administrators can reject leaves."
        )

    success = await leave_service.reject_leave(leave_id, current_user)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave application not found or unauthorized."
        )

    # Notify the applicant
    try:
        db = get_mongodb()
        leave_doc = await db["leaves"].find_one({"_id": ObjectId(leave_id)})
        if leave_doc:
            actor_name = current_user.get("username") or current_user.get("email")
            await notification_service.create_notification(
                user_id=leave_doc["user_email"],
                type="leave_rejected",
                message=f"Your leave has been rejected by {actor_name}",
                from_user=current_user["email"],
                reference_id=leave_id,
            )
    except Exception as e:
        logger.warning(f"Failed to create notification: {e}")

    return {"message": "Leave application rejected successfully"}
