from fastapi import APIRouter, Depends, HTTPException, Query, status
from datetime import datetime, timezone, date, timedelta, time
from typing import List, Optional

from app.core.mongodb import get_mongodb
from app.features.auth.dependencies import get_current_user
from app.features.shifts.schemas import ShiftCreate, ShiftBulkCreate, ShiftUpdate, ShiftResponse, ShiftStats, ShiftHistoryItem, ShiftDefinitionCreate, ShiftDefinitionResponse
from app.features.shifts.service import shift_service
from app.features.redmine.service import redmine_service

router = APIRouter(prefix="/shifts", tags=["shifts"])

async def authorize_shift_access(current_user: dict, target_email: str = None, target_project_id: int = None, is_write: bool = False):
    """
    Validates if the current user can manage a shift.
    - admin: Full access.
    - Project Manager / Project Coordinator: Access if they manage the target project.
    - Technical Resource (or default): Can only view their own shifts, cannot create or update.
    """
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if "Admin" in roles:
        return True
        
    # If PM or PC, they can act on behalf of others within their projects
    if "Project Manager" in roles or "Project Coordinator" in roles:
        if target_project_id:
            user = await redmine_service.get_user_by_email(email)
            if user:
                projects = await redmine_service.get_projects_for_user(user["id"])
                if any(p.id == target_project_id for p in projects):
                    return True
                    
    # Technical Resources (or anyone else) cannot perform write operations (create/update)
    if is_write:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to create or update shifts.",
        )

    # For read operations, users can view their own shifts
    if target_email and email == target_email:
        return True
        
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to access or manage this shift.",
    )




async def check_and_resolve_tr_availability(db, userId: int, userEmail: str, date_str: str, end_date_str: str = None):
    """
    Checks if a Technical Resource (TR) is available on a specific date or date range.
    - If TR is on leave (approved leave), raises HTTP 400.
    - If TR has an existing shift, deletes/removes it to allow replacement.
    """
    # 1. Check if TR is on leave (approved leave)
    try:
        shift_date = date.fromisoformat(date_str)
        shift_end_date = date.fromisoformat(end_date_str) if end_date_str else shift_date
        start_dt = datetime.combine(shift_date, time.min).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(shift_end_date, time.max).replace(tzinfo=timezone.utc)
        
        leave_query = {
            "user_email": userEmail,
            "status": "approved",
            "start_date": {"$lte": end_dt},
            "end_date": {"$gte": start_dt}
        }
        existing_leave = await db.leaves.find_one(leave_query)
        if existing_leave:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TR is not available for this shift."
            )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error checking leave status for {userEmail}: {e}")

    # 2. If TR already has a shift assigned on the same day, remove the existing shift
    await db.shifts.delete_many({"userId": userId, "date": date_str})


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
    - Technical Resource: Can only create shifts for themselves.
    - PC / PM: Can create shifts for users in their projects.
    - Admin: Can create shifts for anyone.
    """
    await authorize_shift_access(
        current_user=current_user,
        target_email=payload.userEmail,
        target_project_id=payload.projectId,
        is_write=True
    )

    try:
        start = date.fromisoformat(payload.date)
        end = date.fromisoformat(payload.endDate) if payload.endDate else start
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if start > end:
        raise HTTPException(status_code=400, detail="endDate must be on or after date.")

    today = date.today()
    if start < today:
        raise HTTPException(status_code=400, detail="Cannot create shifts for past dates.")

    await check_and_resolve_tr_availability(db, payload.userId, payload.userEmail, payload.date, payload.endDate)

    shift_data = payload.model_dump()
    if not shift_data.get("endDate"):
        shift_data["endDate"] = payload.date
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
    Create one shift per day for every day between startDate and endDate.
    - PC / PM: Can bulk create for their projects.
    - Admin: Full access.
    """
    await authorize_shift_access(
        current_user=current_user,
        target_email=payload.userEmail,
        target_project_id=payload.projectId,
        is_write=True
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
            shift_data = {**base, "date": date_str, "endDate": date_str}
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
    if "Admin" not in current_user.get("roles", []):
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
    # First, look up the target user's email to authorize
    target_user = None
    if "Admin" not in current_user.get("roles", []):
        users = await redmine_service.get_all_users()
        target_user = next((u for u in users if u["id"] == user_id), None)
        if not target_user:
             raise HTTPException(status_code=404, detail="User not found.")
        
    shift = await shift_service.get_current_shift(db, user_id)
    if not shift:
        raise HTTPException(status_code=404, detail="No shift found for this user.")

    await authorize_shift_access(
        current_user=current_user,
        target_email=target_user["email"] if target_user else None,
        target_project_id=shift.get("projectId")
    )
    
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
    if "Admin" not in current_user.get("roles", []):
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
    if "Admin" not in current_user.get("roles", []):
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
        and "Admin" not in current_user.get("roles", [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own shifts.",
        )
    return await shift_service.get_shifts_by_email(db, email)


# ------------------------------------------------------------------ #
#  GET /api/shifts/range  — Get shifts for a date range              #
# ------------------------------------------------------------------ #
@router.get("/range")
async def get_shifts_by_range(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    user_id: Optional[int] = Query(None, description="User ID to view schedule for (PM/Admin only)"),
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get shifts for a specific date range.
    - TR: own shifts only
    - PM: own shifts or any TR sharing a project
    - Admin: any user
    """
    roles = current_user.get("roles", [])
    current_email = current_user.get("email")
    if not current_email:
        raise HTTPException(status_code=400, detail="User email not found.")

    allowed_roles = ["Technical Resource", "Project Manager", "Admin"]
    if not any(role in roles for role in allowed_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this feature.",
        )

    current_rm_user = await redmine_service.get_user_by_email(current_email)
    if not current_rm_user:
        raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
    current_rm_id = current_rm_user["id"]

    target_user_id = user_id or current_rm_id

    if target_user_id != current_rm_id:
        if "Admin" in roles:
            pass
        elif "Project Manager" in roles:
            pm_projects = await redmine_service.get_projects_for_user(current_rm_id)
            tr_projects = await redmine_service.get_projects_for_user(target_user_id)
            pm_project_ids = {p.id for p in pm_projects}
            tr_project_ids = {p.id for p in tr_projects}
            if not pm_project_ids.intersection(tr_project_ids):
                raise HTTPException(
                    status_code=403,
                    detail="You can only view shifts for users in your projects."
                )
        else:
            raise HTTPException(status_code=403, detail="Not authorized to view other users' shifts.")

    return await shift_service.get_shifts_by_date_range(
        db,
        start_date=start_date,
        end_date=end_date,
        user_id=target_user_id
    )

# ------------------------------------------------------------------ #
#  GET /api/shifts/history  — Get shift history (last 10)            #
# ------------------------------------------------------------------ #
@router.get("/date/{date_str}")
async def get_shifts_by_date(
    date_str: str,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get shifts for a specific date.
    - Admin/PM: returns all shifts for that date.
    - Regular users: returns only their own shifts for that date.
    """
    roles = current_user.get("roles", [])
    if "Admin" in roles or "Project Manager" in roles:
        return await shift_service.get_shifts_by_date(db, date_str)

    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="User email not found.")
    return await shift_service.get_shifts_by_date(db, date_str, email=email)

@router.get("/history", response_model=List[ShiftHistoryItem])
async def get_shift_history(
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None, description="Admin/PM: view another user's shift history"),
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get paginated shift history.
    - No user_id: current user's own history
    - user_id matches current user: own history
    - user_id differs: Admin/PM only (PM must share a project with target user)
    """
    email = current_user.get("email")
    roles = current_user.get("roles", [])

    if user_id is None:
        return await shift_service.get_shift_history_by_email(db, email, limit, skip)

    current_rm_user = await redmine_service.get_user_by_email(email)
    if not current_rm_user:
        raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
    current_rm_id = current_rm_user["id"]

    if user_id == current_rm_id:
        return await shift_service.get_shift_history_by_email(db, email, limit, skip)

    if "Admin" in roles:
        pass
    elif "Project Manager" in roles:
        pm_projects = await redmine_service.get_projects_for_user(current_rm_id)
        tr_projects = await redmine_service.get_projects_for_user(user_id)
        pm_project_ids = {p.id for p in pm_projects}
        tr_project_ids = {p.id for p in tr_projects}
        if not pm_project_ids.intersection(tr_project_ids):
            raise HTTPException(
                status_code=403,
                detail="You can only view shifts for users in your projects."
            )
    else:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view other users' shift history."
        )

    all_users = await redmine_service.get_all_users()
    target_user = next((u for u in all_users if u["id"] == user_id), None)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    return await shift_service.get_shift_history_by_email(db, target_user["email"], limit, skip)


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
    """Update an existing shift."""
    # To properly authorize an update, we must check if they manage the shift's project
    # Since payload has projectId, we can use it if provided, or fallback to simple role check
    await authorize_shift_access(
        current_user=current_user,
        target_email=current_user.get("email"), # Not modifying someone else's email directly via ShiftUpdate schema
        target_project_id=payload.projectId,
        is_write=True
    )
    
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update.")

    if "endDate" in update_data:
        try:
            end = date.fromisoformat(update_data["endDate"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid endDate format. Use YYYY-MM-DD.")
        
        existing_shift = await shift_service.get_shift_by_id(db, shift_id)
        if not existing_shift:
            raise HTTPException(status_code=404, detail="Shift not found.")
        
        start = date.fromisoformat(existing_shift["date"])
        if start > end:
            raise HTTPException(status_code=400, detail="endDate must be on or after date.")
            
        today = date.today()
        if end < today:
            raise HTTPException(status_code=400, detail="Cannot update endDate to a past date.")

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
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    deleted = await shift_service.delete_shift(db, shift_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shift not found.")


# ------------------------------------------------------------------ #
#  SHIFT DEFINITIONS — Admin creates, PM/PC/TR reads                 #
# ------------------------------------------------------------------ #

@router.post("/definitions", status_code=status.HTTP_201_CREATED)
async def create_shift_definition(
    payload: ShiftDefinitionCreate,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Create a shift definition (M1, A1, N, etc). Admin only."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return await shift_service.create_shift_definition(db, payload.model_dump())


@router.get("/definitions", response_model=List[ShiftDefinitionResponse])
async def get_all_shift_definitions(
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get all shift definitions. Accessible to all authenticated users (for dropdown)."""
    return await shift_service.get_all_shift_definitions(db)


@router.get("/definitions/by-code/{shift_code}", response_model=ShiftDefinitionResponse)
async def get_shift_definition_by_code(
    shift_code: str,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get a single shift definition by shift code (e.g. M1, GN). Accessible to all authenticated users."""
    definition = await shift_service.get_shift_definition_by_code(db, shift_code)
    if not definition:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return definition


@router.get("/definitions/{shift_id}", response_model=ShiftDefinitionResponse)
async def get_shift_definition(
    shift_id: str,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Get a single shift definition by ID."""
    definition = await shift_service.get_shift_definition(db, shift_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return definition


@router.put("/definitions/{shift_id}")
async def update_shift_definition(
    shift_id: str,
    payload: ShiftDefinitionCreate,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Update a shift definition. Admin only."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    updated = await shift_service.update_shift_definition(db, shift_id, payload.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return updated


@router.delete("/definitions/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift_definition(
    shift_id: str,
    db=Depends(get_mongodb),
    current_user: dict = Depends(get_current_user),
):
    """Delete a shift definition. Admin only."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    deleted = await shift_service.delete_shift_definition(db, shift_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
