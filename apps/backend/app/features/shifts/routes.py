from fastapi import APIRouter, Depends, HTTPException, Query, status
from datetime import datetime, timezone, date, timedelta
from typing import List

from app.core.mongodb import get_mongodb
from app.features.auth.dependencies import get_current_user
from app.features.shifts.schemas import ShiftCreate, ShiftBulkCreate, ShiftUpdate, ShiftResponse, ShiftStats, ShiftHistoryItem
from app.features.shifts.service import shift_service

router = APIRouter(prefix="/shifts", tags=["shifts"])


# ------------------------------------------------------------------ #
#  POST /api/shifts  — Create a shift                                 #
# ------------------------------------------------------------------ #
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_shift(
    payload: ShiftCreate,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """
    Create a new shift record.
    - Regular users can only create shifts for themselves.
    - Admins can create shifts for any user.
    """
    if (
        current_user["email"] != payload.userEmail
        and "admin" not in current_user.get("roles", [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only create shifts for yourself.",
        )

    shift_data = payload.model_dump()
    result = await shift_service.create_shift(db, shift_data)
    return result


# ------------------------------------------------------------------ #
#  POST /api/shifts/bulk  — Create shifts over a date range          #
# ------------------------------------------------------------------ #
@router.post("/bulk", status_code=status.HTTP_201_CREATED)
async def create_bulk_shifts(
    payload: ShiftBulkCreate,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """
    Admin-only. Create one shift per day for every day between startDate and endDate
    (inclusive). Days that already have a shift for this user are silently skipped.
    """
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    try:
        start = date.fromisoformat(payload.startDate)
        end = date.fromisoformat(payload.endDate)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if start > end:
        raise HTTPException(status_code=400, detail="startDate must be on or before endDate.")

    today = date.today()
    if start < today:
        raise HTTPException(status_code=400, detail="Cannot create shifts for past dates.")

    # Build base data without the date-range fields
    base = payload.model_dump(exclude={"startDate", "endDate"})

    created_shifts = []
    skipped_dates = []

    current_date = start
    while current_date <= end:
        date_str = current_date.isoformat()

        # Duplicate guard: one shift per user per day
        existing = await db.shifts.find_one({"userId": payload.userId, "date": date_str})
        if existing:
            skipped_dates.append(date_str)
        else:
            shift_data = {**base, "date": date_str}
            result = await shift_service.create_shift(db, shift_data)
            created_shifts.append(result)

        current_date += timedelta(days=1)

    return {
        "created": len(created_shifts),
        "skipped": len(skipped_dates),
        "skipped_dates": skipped_dates,
        "shifts": created_shifts,
    }


# ------------------------------------------------------------------ #
#  GET /api/shifts/server-time                                        #
# ------------------------------------------------------------------ #
@router.get("/server-time")
async def get_server_time():
    """Returns the current server time in UTC (no auth required)."""
    now = datetime.now(timezone.utc)
    return {
        "server_time": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "timezone": "UTC",
    }


# ------------------------------------------------------------------ #
#  GET /api/shifts/stats  — Aggregate stats (admin only)             #
# ------------------------------------------------------------------ #
@router.get("/stats", response_model=ShiftStats)
async def get_shift_stats(
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Return aggregate statistics across all shifts. Admin only."""
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return await shift_service.get_stats(db)


# ------------------------------------------------------------------ #
#  GET /api/shifts/current/{userId}                                   #
# ------------------------------------------------------------------ #
@router.get("/current/{user_id}")
async def get_current_shift(
    user_id: int,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get the most recent shift for a user by ID."""
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    shift = await shift_service.get_current_shift(db, user_id)
    if not shift:
        raise HTTPException(status_code=404, detail="No shift found for this user.")
    return shift


# ------------------------------------------------------------------ #
#  GET /api/shifts/active/{userId}                                    #
# ------------------------------------------------------------------ #
@router.get("/active/{user_id}")
async def get_active_shift(
    user_id: int,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get the currently active shift for a user (if any)."""
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    shift = await shift_service.get_active_shift(db, user_id)
    if not shift:
        raise HTTPException(status_code=404, detail="No active shift found.")
    return shift


# ------------------------------------------------------------------ #
#  GET /api/shifts/user/{userId}  — All shifts by user ID            #
# ------------------------------------------------------------------ #
@router.get("/user/{user_id}")
async def get_shifts_by_user_id(
    user_id: int,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get all shifts for a user by their numeric ID. Admin only."""
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return await shift_service.get_shifts_by_user_id(db, user_id)


# ------------------------------------------------------------------ #
#  GET /api/shifts/user/email/{email}  — All shifts by email         #
# ------------------------------------------------------------------ #
@router.get("/user/email/{email}")
async def get_shifts_by_email(
    email: str,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """
    Get all shifts for a user by email.
    - Users can view their own shifts.
    - Admins can view any user's shifts.
    """
    if (
        current_user["email"] != email
        and "admin" not in current_user.get("roles", [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own shifts.",
        )
    return await shift_service.get_shifts_by_email(db, email)


# ------------------------------------------------------------------ #
#  GET /api/shifts/history  — Get shift history (last 10)            #
# ------------------------------------------------------------------ #
@router.get("/history", response_model=List[ShiftHistoryItem])
async def get_shift_history(
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get paginated shift history for the current user."""
    email = current_user.get("email")
    return await shift_service.get_shift_history_by_email(db, email, limit, skip)


# ------------------------------------------------------------------ #
#  PUT /api/shifts/{shift_id}  — Update a shift                      #
# ------------------------------------------------------------------ #
@router.put("/{shift_id}")
async def update_shift(
    shift_id: str,
    payload: ShiftUpdate,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Update an existing shift. Admin only."""
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update.")

    updated = await shift_service.update_shift(db, shift_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Shift not found.")
    return updated


# ------------------------------------------------------------------ #
#  DELETE /api/shifts/{shift_id}  — Delete a shift                   #
# ------------------------------------------------------------------ #
@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift(
    shift_id: str,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Delete a shift by ID. Admin only."""
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    deleted = await shift_service.delete_shift(db, shift_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shift not found.")
