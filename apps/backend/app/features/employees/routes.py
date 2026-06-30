import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_admin, require_active
from app.features.employees.schemas import EmployeeCreate, EmployeeResponse, EmployeeOnboardRequest, EmployeeStatusUpdate, EmployeeLocationUpdate, EmployeeActiveToggle, AssignProjectRequest, EmployeeUpdate, DisonboardRequest
from app.models.employee_master import EmployeeMaster, EmployeeStatus
from app.models.office_location import OfficeLocation
from app.features.redmine.service import redmine_service
from app.features.auth.services.keycloak import update_keycloak_user, remove_realm_role_from_user, add_realm_role_to_user
from app.services.database.base_service import BaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/employees", tags=["employees"])


def _employee_to_dict(e: EmployeeMaster) -> dict:
    return {
        "id": str(e.id),
        "keycloak_user_id": e.keycloak_user_id,
        "redmine_user_id": e.redmine_user_id,
        "first_name": e.first_name,
        "middle_name": e.middle_name,
        "last_name": e.last_name,
        "user_email": e.user_email,
        "designation": e.designation,
        "employee_id": e.employee_id,
        "work_location_status": e.work_location_status,
        "work_address": e.work_address,
        "home_address": e.home_address,
        "contact_number": e.contact_number,
        "alt_contact_number": e.alt_contact_number,
        "country": e.country,
        "status": e.status,
        "is_active": e.is_active,
        "onboarded_at": e.onboarded_at,
        "location_id": str(e.location_id) if e.location_id else None,
        "reports_to": e.reports_to,
        "reports_to_name": e.reports_to_name,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
    }


@router.get("")
async def list_employees(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    onboarded: Optional[bool] = Query(None, description="Filter onboarded employees (have keycloak_user_id and active)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[EmployeeMaster](db)
    offset = (page - 1) * per_page
    query = db.query(EmployeeMaster)

    if status:
        query = query.filter(EmployeeMaster.status == status)
    if onboarded is True:
        query = query.filter(EmployeeMaster.onboarded_at.isnot(None))
    elif onboarded is False:
        query = query.filter(EmployeeMaster.onboarded_at.is_(None))

    total = query.count()
    employees = query.order_by(EmployeeMaster.created_at.desc()).offset(offset).limit(per_page).all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "employees": [_employee_to_dict(e) for e in employees],
    }


@router.post("", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[EmployeeMaster](db)
    existing = svc.fetch_one(EmployeeMaster, user_email=payload.user_email)
    if existing:
        raise HTTPException(status_code=409, detail="Employee with this email already exists")
    if payload.employee_id:
        existing_emp = svc.fetch_one(EmployeeMaster, employee_id=payload.employee_id)
        if existing_emp:
            raise HTTPException(status_code=409, detail="Employee with this employee ID already exists")
    emp = svc.create(EmployeeMaster, **payload.model_dump())
    return _employee_to_dict(emp)


@router.post("/onboard")
async def onboard_employee(
    payload: EmployeeOnboardRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, user_email=payload.email)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee record not found")
    if emp.keycloak_user_id and emp.keycloak_user_id != payload.keycloak_user_id:
        raise HTTPException(status_code=409, detail="Employee already linked to a different Keycloak user")
    if emp.keycloak_user_id == payload.keycloak_user_id and emp.status == EmployeeStatus.ACTIVE:
        return {"message": "Employee already onboarded"}

    existing_linked = svc.fetch_one(EmployeeMaster, keycloak_user_id=payload.keycloak_user_id)
    if existing_linked and existing_linked.id != emp.id:
        raise HTTPException(status_code=409, detail="Keycloak user already linked to another employee")

    emp.keycloak_user_id = payload.keycloak_user_id
    emp.status = EmployeeStatus.ACTIVE

    # Auto-resolve redmine_user_id from Redmine by employee email
    if not emp.redmine_user_id:
        try:
            redmine_user = await redmine_service.get_user_by_email(emp.user_email)
            if redmine_user:
                emp.redmine_user_id = redmine_user["id"]
                logger.info(f"Auto-linked Redmine user {redmine_user['id']} to employee {emp.user_email}")
            else:
                logger.warning(f"No Redmine user found for email {emp.user_email} — redmine_user_id not set")
        except Exception as e:
            logger.error(f"Failed to fetch Redmine user for {emp.user_email}: {e}")
            # Non-fatal: onboard proceeds even if Redmine lookup fails

    db.commit()
    db.refresh(emp)
    return _employee_to_dict(emp)


@router.patch("/{employee_id}/status")
async def update_employee_status(
    employee_id: str,
    payload: EmployeeStatusUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    if payload.status not in (EmployeeStatus.PENDING, EmployeeStatus.ACTIVE, EmployeeStatus.DISABLED):
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: pending, active, disabled")
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, id=employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    emp.status = payload.status
    db.commit()
    return {"message": f"Employee status updated to {payload.status}"}


@router.patch("/{employee_id}/location")
async def update_employee_location(
    employee_id: str,
    payload: EmployeeLocationUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, id=employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    office_svc = BaseService[OfficeLocation](db)
    office = office_svc.fetch_one(OfficeLocation, id=payload.location_id)
    if not office:
        raise HTTPException(status_code=404, detail="Office location not found")

    emp.location_id = payload.location_id
    db.commit()
    return _employee_to_dict(emp)


@router.patch("/{employee_id}/activate")
async def toggle_employee_active(
    employee_id: str,
    payload: EmployeeActiveToggle,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, id=employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    svc.update(EmployeeMaster, employee_id, is_active=payload.is_active)
    return {"message": f"Employee {'activated' if payload.is_active else 'deactivated'}"}


@router.put("/{employee_id}")
async def update_employee(
    employee_id: str,
    payload: EmployeeUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, id=employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    is_admin = "Admin" in current_user.get("roles", [])
    is_self = current_user.get("email") == emp.user_email
    if not is_admin and not is_self:
        raise HTTPException(status_code=403, detail="Not authorized to update this employee")

    warnings = []
    changes = payload.model_dump(exclude_none=True)

    # ── 1. employee_master fields (always safe, always first) ──────────────
    db_fields = {
        "first_name": "first_name",
        "last_name": "last_name",
        "designation": "designation",
        "contact_number": "contact_number",
        "alt_contact_number": "alt_contact_number",
        "work_address": "work_address",
        "home_address": "home_address",
        "country": "country",
        "location_id": "location_id",
        "reports_to": "reports_to",
        "user_email": "user_email",
    }
    update_data = {}
    for payload_key, db_col in db_fields.items():
        if payload_key in changes:
            update_data[db_col] = changes[payload_key]

    if update_data:
        update_data["updated_at"] = datetime.now(timezone.utc)
        svc.update(EmployeeMaster, employee_id, **update_data)

    # ── 2. Keycloak user ────────────────────────────────────────────────────
    if emp.keycloak_user_id:
        kc_data = {}
        if "first_name" in changes:
            kc_data["firstName"] = changes["first_name"]
        if "last_name" in changes:
            kc_data["lastName"] = changes["last_name"]
        if "user_email" in changes:
            kc_data["email"] = changes["user_email"]
        if "timezone" in changes:
            kc_data["attributes"] = {"timezone": [changes["timezone"]]}

        if kc_data:
            try:
                ok = await update_keycloak_user(emp.keycloak_user_id, kc_data)
                if not ok:
                    warnings.append("Keycloak user sync failed")
            except Exception:
                warnings.append("Keycloak user sync failed")

    # ── 3. Keycloak role ────────────────────────────────────────────────────
    if is_admin and emp.keycloak_user_id and "keycloak_role_name" in changes:
        try:
            current_roles = current_user.get("roles", [])  # not the target user's roles, just skip self-demote
            await add_realm_role_to_user(emp.keycloak_user_id, changes["keycloak_role_name"])
        except Exception:
            warnings.append("Keycloak role sync failed")

    # ── 4. Redmine user ─────────────────────────────────────────────────────
    if emp.redmine_user_id:
        rm_data = {}
        if "first_name" in changes:
            rm_data["firstname"] = changes["first_name"]
        if "last_name" in changes:
            rm_data["lastname"] = changes["last_name"]
        if "user_email" in changes:
            rm_data["mail"] = changes["user_email"]

        if rm_data:
            try:
                ok = await redmine_service.update_user(emp.redmine_user_id, rm_data)
                if not ok:
                    warnings.append("Redmine user sync failed")
            except Exception:
                warnings.append("Redmine user sync failed")

    # ── 5. Redmine project ──────────────────────────────────────────────────
    if emp.redmine_user_id and "project_id" in changes:
        try:
            role_name = changes.get("redmine_role_name", "Developer")
            roles = await redmine_service.get_roles()
            role = next((r for r in roles if r["name"].lower() == role_name.lower()), None)
            await redmine_service.add_user_to_project(emp.redmine_user_id, changes["project_id"], role["id"] if role else None)
            if not emp.onboarded_at:
                svc.update(EmployeeMaster, employee_id, onboarded_at=datetime.now(timezone.utc))
        except Exception:
            warnings.append("Redmine project sync failed")

    # ── 6. Auto-fill reports_to_name ────────────────────────────────────────
    if "reports_to" in changes and changes["reports_to"]:
        manager = svc.fetch_one(EmployeeMaster, redmine_user_id=changes["reports_to"])
        if manager:
            name = f"{manager.first_name} {manager.last_name}, {manager.designation}"
            svc.update(EmployeeMaster, employee_id, reports_to_name=name)

    # ── Re-fetch and return ─────────────────────────────────────────────────
    db.refresh(emp)
    response = _employee_to_dict(emp)
    response["message"] = "Employee updated successfully"
    if warnings:
        response["warnings"] = warnings
    return response


@router.patch("/assign-project")
async def assign_project(
    payload: AssignProjectRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, user_email=payload.user_email)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not emp.redmine_user_id:
        raise HTTPException(status_code=400, detail="Employee not onboarded in Redmine yet")

    manager = svc.fetch_one(EmployeeMaster, redmine_user_id=payload.reports_to)
    if not manager:
        raise HTTPException(status_code=404, detail="Reporting manager not found")

    try:
        roles = await redmine_service.get_roles()
        role = next((r for r in roles if r["name"].lower() == payload.redmine_role_name.lower()), None)
        if not role:
            raise HTTPException(status_code=400, detail=f"Redmine role '{payload.redmine_role_name}' not found")
        await redmine_service.add_user_to_project(emp.redmine_user_id, payload.project_id, role["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to assign project: {e}")

    projects = await redmine_service.get_all_projects()
    project_name = next((p["name"] for p in projects if p["id"] == payload.project_id), str(payload.project_id))

    reports_to_name = f"{manager.first_name} {manager.last_name}, {manager.designation}"
    update_fields = {"reports_to": payload.reports_to, "reports_to_name": reports_to_name}
    if not emp.onboarded_at:
        update_fields["onboarded_at"] = datetime.now(timezone.utc)
    svc.update(EmployeeMaster, emp.id, **update_fields)

    logger.info(f"Admin assigned project {payload.project_id} + reports-to {payload.reports_to} for {payload.user_email}")
    return {
        "message": "Project assigned successfully",
        "project_id": payload.project_id,
        "project_name": project_name,
        "redmine_role": payload.redmine_role_name,
        "reports_to": payload.reports_to,
        "reports_to_name": reports_to_name,
    }


@router.patch("/{employee_id}/disonboard")
async def disonboard_employee(
    employee_id: str,
    payload: DisonboardRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, id=employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not emp.redmine_user_id:
        raise HTTPException(status_code=400, detail="Employee not onboarded in Redmine")

    try:
        await redmine_service.remove_user_from_project(emp.redmine_user_id, payload.project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove from project: {e}")

    remaining_projects = await redmine_service.get_projects_for_user(emp.redmine_user_id)
    if not remaining_projects:
        svc.update(EmployeeMaster, employee_id,
            onboarded_at=None,
            reports_to=None,
            reports_to_name=None,
        )
        logger.info(f"Employee {emp.user_email} fully disonboarded — no projects remaining")
    else:
        logger.info(f"Removed project {payload.project_id} from {emp.user_email}, still has {len(remaining_projects)} project(s)")

    return {
        "message": "Project removed",
        "project_id": payload.project_id,
        "still_onboarded": len(remaining_projects) > 0,
        "remaining_projects": len(remaining_projects),
    }


@router.get("/by-email/{email}", response_model=EmployeeResponse)
async def get_employee_by_email(
    email: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, user_email=email)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _employee_to_dict(emp)


@router.get("/suggest")
async def suggest_employees(
    q: str = Query(..., min_length=1, description="Search prefix for email or name"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    emps = db.query(EmployeeMaster).filter(
        EmployeeMaster.user_email.ilike(f"{q}%")
    ).limit(10).all()
    return [
        {"user_email": e.user_email, "first_name": e.first_name, "last_name": e.last_name, "designation": e.designation}
        for e in emps
    ]
