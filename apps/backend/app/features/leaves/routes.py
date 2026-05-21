from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List
from app.features.auth.dependencies import get_current_user
from app.features.leaves.schemas.leaves import (
    LeaveApplyRequest, 
    LeaveHistoryItem, 
    LeaveStats, 
    HolidayItem
)
from app.features.leaves.services.leave_service import leave_service

router = APIRouter(prefix="/leaves", tags=["leaves"])

@router.get("/stats", response_model=LeaveStats)
async def get_stats(current_user: dict = Depends(get_current_user)):
    """Get summarized leave stats for the dashboard cards."""
    user_id = current_user.get("sub")
    stats = await leave_service.get_leave_stats(user_id)
    return stats

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

@router.get("/holidays", response_model=List[HolidayItem])
async def get_holidays(current_user: dict = Depends(get_current_user)):
    """Get the holiday calendar."""
    return await leave_service.get_holidays()

@router.post("/apply")
async def apply_leave(
    leave_data: LeaveApplyRequest,
    current_user: dict = Depends(get_current_user)
):
    """Submit a new leave application."""
    user_id = current_user.get("sub")
    email = current_user.get("email")
    leave_id = await leave_service.apply_for_leave(user_id, email, leave_data)
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
    if "admin" not in roles and "Project Manager" not in roles:
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
    if "admin" not in roles and "Project Manager" not in roles:
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

    return {"message": "Leave application approved successfully"}
