import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from datetime import datetime, timezone, date, timedelta, time
from typing import List, Optional
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_active
from app.features.shifts.schemas import ShiftCreate, ShiftBulkCreate, ShiftUpdate, ShiftResponse, ShiftStats, ShiftHistoryItem, ShiftDefinitionCreate, ShiftDefinitionResponse
from app.features.shifts.service import shift_service
from app.features.redmine.service import redmine_service
from app.models.leave import Leave
from app.models.shift import Shift
from app.models.employee_master import EmployeeMaster
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shifts", tags=["shifts"])

async def authorize_shift_access(current_user: dict, target_email: str = None, target_project_id: int = None, is_write: bool = False):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if "Admin" in roles:
        return True

    if "Project Manager" not in roles and "Project Coordinator" not in roles:
        if is_write:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Admin, Project Manager, or Project Coordinator can create or update shifts.",
            )
        if target_email and email == target_email:
            return True
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this shift.",
        )

    if target_project_id:
        user = await redmine_service.get_user_by_email(email)
        if user:
            projects = await redmine_service.get_projects_for_user(user["id"])
            project_ids = {p.id for p in projects}
            if target_project_id in project_ids:
                return True
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't manage this project.",
        )

    if target_email and email == target_email:
        return True

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have access to this shift.",
    )



async def check_and_resolve_tr_availability(db: Session, userId: int, userEmail: str, date_str: str, end_date_str: str = None):
    try:
        shift_date = date.fromisoformat(date_str)
        shift_end_date = date.fromisoformat(end_date_str) if end_date_str else shift_date
        
        stmt = select(Leave).where(
            Leave.user_email == userEmail,
            Leave.approval_status == "approved",
            Leave.start_date <= shift_end_date,
            Leave.end_date >= shift_date,
        )
        existing_leave = db.execute(stmt).scalars().first()
        if existing_leave:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User has an approved leave during this period."
            )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error checking leave status for {userEmail}: {e}")

    from app.services.database.shift_service import ShiftService as PGShiftService
    svc = PGShiftService(db)
    svc.delete_by_user_and_date(userId, date_str)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_shift(
    payload: ShiftCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
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
    if end < today:
        raise HTTPException(status_code=400, detail="Cannot create shifts for past dates.")

    from app.models.shift_definition import ShiftDefinition
    shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == payload.shift).first()
    if not shift_def:
        raise HTTPException(status_code=400, detail=f"Shift code '{payload.shift}' does not exist.")

    base = payload.model_dump()
    created = []
    from app.services.database.shift_service import ShiftService as PGShiftService
    svc = PGShiftService(db)
    current_date = start
    while current_date <= end:
        date_str = current_date.isoformat()
        await check_and_resolve_tr_availability(db, payload.userId, payload.userEmail, date_str, date_str)
        shift_data = {**base, "date": date_str, "endDate": date_str}
        result = await shift_service.create_shift(db, shift_data, current_user)
        created.append(result)
        current_date += timedelta(days=1)

    if len(created) == 1:
        return created[0]
    return {"created": len(created), "shifts": created}


@router.post("/validate")
async def validate_shift(
    payload: ShiftCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await authorize_shift_access(
        current_user=current_user,
        target_email=payload.userEmail,
        target_project_id=payload.projectId,
        is_write=True
    )
    shift_data = payload.model_dump()
    if not shift_data.get("endDate"):
        shift_data["endDate"] = payload.date
    return await shift_service.validate_shift(db, shift_data, current_user)


@router.post("/bulk", status_code=status.HTTP_201_CREATED)
async def create_bulk_shifts(
    payload: ShiftBulkCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
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

    try:
        st = time.fromisoformat(payload.shiftStartTime[:5])
        et = time.fromisoformat(payload.shiftEndTime[:5])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid time format. Use HH:MM.")
    if st == et:
        raise HTTPException(status_code=400, detail="Shift start and end time cannot be the same.")

    from app.models.shift_definition import ShiftDefinition
    shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == payload.shift).first()
    if not shift_def:
        raise HTTPException(status_code=400, detail=f"Shift code '{payload.shift}' does not exist.")

    base = payload.model_dump(exclude={"startDate", "endDate"})

    created = []
    skipped_dates = []
    leave_blocked = []

    from app.services.database.shift_service import ShiftService as PGShiftService
    svc = PGShiftService(db)
    current_date = start
    while current_date <= end:
        date_str = current_date.isoformat()

        on_leave = db.execute(
            select(Leave).where(
                Leave.user_email == payload.userEmail,
                Leave.approval_status.in_(["approved"]),
                Leave.start_date <= current_date,
                Leave.end_date >= current_date,
            )
        ).scalars().first()
        if on_leave:
            leave_blocked.append(date_str)
            current_date += timedelta(days=1)
            continue

        svc.delete_by_user_and_date(payload.userId, date_str)
        shift_data = {**base, "date": date_str, "endDate": date_str}
        result = await shift_service.create_shift(db, shift_data, current_user)
        created.append(result)
        current_date += timedelta(days=1)

    return {
        "created": len(created),
        "skipped_leave": len(leave_blocked),
        "leave_dates": leave_blocked,
        "shifts": created,
    }


@router.get("/server-time")
async def get_server_time(
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    now = datetime.now(timezone.utc)
    return {
        "server_time": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "timezone": "UTC",
    }


@router.get("/stats", response_model=ShiftStats)
async def get_shift_stats(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return await shift_service.get_stats(db)


@router.get("/current/{user_id}")
async def get_current_shift(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
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


@router.get("/active/{user_id}")
async def get_active_shift(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    shift = await shift_service.get_active_shift(db, user_id)
    if not shift:
        raise HTTPException(status_code=404, detail="No active shift found.")
    return shift


@router.get("/user/{user_id}")
async def get_shifts_by_user_id(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return await shift_service.get_shifts_by_user_id(db, user_id)


@router.get("/user/email/{email}")
async def get_shifts_by_email(
    email: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if (
        current_user["email"] != email
        and "Admin" not in current_user.get("roles", [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own shifts.",
        )
    return await shift_service.get_shifts_by_email(db, email)


@router.get("/work-locations")
async def get_work_locations():
    return ["OFFICE", "WFH", "REMOTE_ONSITE", "REMOTE_OFFSITE", "LEAVE"]


@router.get("/range")
async def get_shifts_by_range(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    user_id: Optional[int] = Query(None, description="User ID to view schedule for (PM/Admin only)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    roles = current_user.get("roles", [])
    current_email = current_user.get("email")
    if not current_email:
        raise HTTPException(status_code=400, detail="User email not found.")

    allowed_roles = ["Technical Resource", "Project Manager", "Admin", "Project Coordinator"]
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

    target_email = current_email
    if target_user_id != current_rm_id:
        if "Admin" in roles:
            pass
        elif "Project Manager" in roles or "Project Coordinator" in roles:
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

        from app.models.employee_master import EmployeeMaster
        from app.services.database.base_service import BaseService
        emp_svc = BaseService[EmployeeMaster](db)
        emp = emp_svc.fetch_one(EmployeeMaster, redmine_user_id=target_user_id)
        target_email = emp.user_email if emp else None

    shifts = await shift_service.get_shifts_by_date_range(
        db,
        start_date=start_date,
        end_date=end_date,
        user_id=target_user_id
    )

    leaves = []
    if target_email:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
        stmt = select(Leave).where(
            Leave.user_email == target_email,
            Leave.start_date <= ed,
            Leave.end_date >= sd,
            Leave.approval_status == "approved",
        ).order_by(Leave.start_date)
        leaves = [lv.to_dict() for lv in db.execute(stmt).scalars().all()]

    return {"shifts": shifts, "leaves": leaves}


@router.get("/date/{date_str}")
async def get_shifts_by_date(
    date_str: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="User email not found.")
    return await shift_service.get_shifts_by_date(db, date_str, email=email)


@router.get("/history", response_model=List[ShiftHistoryItem])
async def get_shift_history(
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None, description="Admin/PM: view another user's shift history"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
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
    elif "Project Manager" in roles or "Project Coordinator" in roles:
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


@router.put("/{shift_id}")
async def update_shift(
    shift_id: str,
    payload: ShiftUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await authorize_shift_access(
        current_user=current_user,
        target_email=current_user.get("email"),
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


@router.delete("/by-date", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift_by_date(
    user_id: int = Query(..., description="Redmine user ID"),
    target_date: str = Query(..., description="YYYY-MM-DD"),
    reason: Optional[str] = Query(None, max_length=200, description="Reason for deletion"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    try:
        td = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == user_id).first()
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(status_code=404, detail="Employee not found or not onboarded")

    deleted = await shift_service.delete_shift_by_date(db, emp.keycloak_user_id, td)
    if not deleted:
        raise HTTPException(status_code=404, detail="No shift found for this date")

    if reason:
        logger.info(f"Admin {current_user.get('email')} deleted shift for user {user_id} on {target_date}: {reason}")


@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift(
    shift_id: str,
    reason: Optional[str] = Query(None, max_length=200, description="Reason for deletion"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    deleted = await shift_service.delete_shift(db, shift_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if reason:
        logger.info(f"Admin {current_user.get('email')} deleted shift {shift_id}: {reason}")


@router.post("/definitions", status_code=status.HTTP_201_CREATED)
async def create_shift_definition(
    payload: ShiftDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return await shift_service.create_shift_definition(db, payload.model_dump())


@router.get("/definitions", response_model=List[ShiftDefinitionResponse])
async def get_all_shift_definitions(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await shift_service.get_all_shift_definitions(db)


@router.get("/definitions/by-code/{shift_code}", response_model=ShiftDefinitionResponse)
async def get_shift_definition_by_code(
    shift_code: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    definition = await shift_service.get_shift_definition_by_code(db, shift_code)
    if not definition:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return definition


@router.get("/definitions/{shift_id}", response_model=ShiftDefinitionResponse)
async def get_shift_definition(
    shift_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    definition = await shift_service.get_shift_definition(db, shift_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return definition


@router.put("/definitions/{shift_id}")
async def update_shift_definition(
    shift_id: str,
    payload: ShiftDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    updated = await shift_service.update_shift_definition(db, shift_id, payload.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
    return updated


@router.delete("/definitions/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift_definition(
    shift_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    deleted = await shift_service.delete_shift_definition(db, shift_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shift definition not found.")
