import json
import os
import logging
import uuid
import hashlib
import httpx
import secrets
import openpyxl
from io import BytesIO
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, status, Query, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError as PydanticValidationError
from typing import List, Optional
from datetime import date, timedelta, datetime, timezone
from .schemas import IssueCreate, IssueUpdate, ProjectCreate, ProjectUpdate, ProjectResponse, AdminProjectListResponse, AdminProjectItem, AdminProjectDetail, UserWithProjects, IssueResponse, IssueListResponse, TimeZoneInfo, ProjectMember, MemberAdd, MemberUpdate, ProjectDeleteRequest, ProjectDeleteConfirm, SearchResponse, SearchResults, ProjectSearchItem, PersonSearchItem, ShiftSearchItem, RoleCreate, RoleUpdate
from .service import redmine_service
from .sql_service import RedmineSQLService
from .constants import REDMINE_TIMEZONES
from app.features.auth.dependencies import get_current_user
from app.core.limiter import limiter
from app.utils.audit import audit_logger
from app.utils.email_bg import send_email_bg
from app.middleware.logging import _get_client_ip
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db
from app.features.shifts.service import shift_service
from app.features.notifications.service import schedule_notification
from app.models.employee_master import EmployeeMaster
from app.models.delete_verification import DeleteVerificationToken
from app.services.email_service import email_service

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png",
    ".pdf",
    ".txt", ".csv", ".log",
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".webm",
}

RECORDING_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".webm"}

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_FILE_COUNT = 2


def _validation_errors(e: PydanticValidationError) -> list[dict]:
    return [
        {"field": err["loc"][-1] if err["loc"] else "__root__", "message": err["msg"]}
        for err in e.errors()
    ]


def _validate_uploaded_file(f: UploadFile):
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )


def _user_error(e: Exception, action: str) -> tuple[int, str]:
    """Map an upstream failure to a (status_code, message) safe to show an end user.

    Never exposes the upstream product name, raw response body, or stack traces.
    `action` is a user-facing verb phrase, e.g. "create tickets in this project".
    """
    if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
        code = e.response.status_code
        body = (e.response.text or "").lower()
        if code in (401, 403) or "not authorized" in body:
            return 403, f"You don't have permission to {action}. Please contact your administrator."
        if code == 404:
            return 404, "We couldn't find what you were looking for. It may have been removed."
        if code == 409:
            return 409, "This change conflicts with an existing item. Please refresh and try again."
        if code == 422:
            # The upstream service drops required fields (e.g. project/tracker
            # come back "blank") when the acting user lacks permission for the
            # action, so treat that pattern as a permission problem.
            if "cannot be blank" in body or "is not included in the list" in body:
                return 403, f"You don't have permission to {action}. Please contact your administrator."
            return 422, "Some of the details are invalid. Please review them and try again."
        if code >= 500:
            return 503, "The service is temporarily unavailable. Please try again in a few minutes."
    return 502, f"We couldn't {action} right now. Please try again or contact your administrator."


def _resolve_employee_id_from_redmine_user_id(db: Session, redmine_user_id) -> Optional[uuid.UUID]:
    """Resolve a Redmine internal user id (integer) into our app's
    employee_master.id (UUID).

    First tries direct match on employee_master.redmine_user_id.
    Falls back to email-based lookup via Redmine's email_addresses table.

    IMPORTANT: this is the bridge between the two keyspaces. Do NOT compare
    redmine_user_id directly to employee_master.id or .keycloak_user_id
    -- they're different (int vs UUID).
    """
    if redmine_user_id is None:
        return None
    try:
        redmine_user_id_int = int(redmine_user_id)
    except (TypeError, ValueError):
        return None

    emp = (
        db.query(EmployeeMaster)
        .filter(EmployeeMaster.redmine_user_id == redmine_user_id_int)
        .first()
    )
    if emp:
        return emp.id

    redmine_user = RedmineSQLService(db).get_user_by_id(redmine_user_id_int)
    if not redmine_user or not redmine_user.get("mail"):
        return None
    emp = (
        db.query(EmployeeMaster)
        .filter(EmployeeMaster.user_email == redmine_user["mail"])
        .first()
    )
    return emp.id if emp else None


def _can_manage_members(current_user: dict, db: Session, project_id: int) -> bool:
    """Admin can manage any project's members; PM/PC only projects they hold PM/PC role in."""
    roles = current_user.get("roles", [])
    if "Admin" in roles:
        return True
    if "Project Manager" not in roles and "Project Coordinator" not in roles:
        return False
    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(current_user.get("email"))
    if not rm_user:
        return False
    for m in sql.get_memberships_for_users([rm_user["id"]]):
        if m["project_id"] == project_id and m["role"] in ("Project Manager", "Project Coordinator"):
            return True
    return False


router = APIRouter()


# ------------------------------------------------------------------ #
#  GET /api/redmine/timezones  — Redmine-compatible timezone list    #
# ------------------------------------------------------------------ #
@router.get("/redmine/timezones", response_model=List[TimeZoneInfo])
def list_timezones(
    current_user: dict = Depends(get_current_user),
):
    """Return the full list of Redmine-compatible timezones.
    Mirrors the timezone dropdown from Redmine's user preferences form.
    Used by the onboarding screen to pick a user's timezone."""
    return REDMINE_TIMEZONES


# ------------------------------------------------------------------ #
#  GET /api/redmine/roles  — Redmine role list                       #
# ------------------------------------------------------------------ #
@router.get("/redmine/roles")
async def list_redmine_roles(
    current_user: dict = Depends(get_current_user),
):
    """Return all Redmine roles. Admin, PM, or PC only."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
    return await redmine_service.get_roles()


@router.get("/admin/roles")
@limiter.limit("5/minute")
async def list_roles(
    request: Request,
    current_user: dict = Depends(get_current_user),
    search: Optional[str] = Query(None, max_length=100, description="Case-insensitive substring on role name"),
    assignable: Optional[bool] = Query(None, description="Filter by assignable flag"),
    include_builtin: bool = Query(True, description="Include built-in roles (Non member, Anonymous)"),
    page: int = Query(1, ge=1, le=10000, description="Page number (default 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page (default 10, max 100)"),
):
    """Return the lightweight Redmine roles list (id, name, builtin, assignable). Admin only. Cached 60s."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    all_roles = await redmine_service.get_roles_list()
    filtered = redmine_service.filter_roles(
        all_roles,
        search=search,
        assignable=assignable,
        include_builtin=include_builtin,
    )
    return redmine_service.paginate_roles(filtered, page=page, page_size=page_size)


@router.get("/admin/roles/{role_id}")
@limiter.limit("10/minute")
async def get_role_detail(
    request: Request,
    role_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return a single Redmine role with permissions, assignable, and visibility flags. Admin only. Cached 60s."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    if role_id < 1 or role_id > 2_147_483_647:
        raise HTTPException(status_code=400, detail="Invalid role_id")
    role = await redmine_service.get_role_details(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.post("/redmine/roles", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_redmine_role(
    request: Request,
    payload: RoleCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    try:
        role = await redmine_service.create_role(payload.name, payload.permissions)
    except httpx.HTTPStatusError as e:
        logger.error("Role create failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "create this role")
        raise HTTPException(status_code=code, detail=message)
    except Exception:
        logger.exception("Role create failed")
        raise HTTPException(status_code=400, detail="We couldn't create the role right now. Please try again.")
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Something went wrong while saving the role. Please try again.")
    audit_logger.info(
        f"Role created: {payload.name} by {current_user.get('username')}",
        extra={"correlation_id": request.state.correlation_id, "extra_data": {"action": "create_role", "username": current_user.get("username"), "status": "success", "role_name": payload.name, "client_ip": _get_client_ip(request)}},
    )
    return role


@router.put("/redmine/roles/{role_id}")
@limiter.limit("10/minute")
async def update_redmine_role(
    request: Request,
    role_id: int,
    payload: RoleUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    sql = RedmineSQLService(db)
    existing = sql.get_role(role_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Role not found")
    if existing["builtin"] != 0:
        raise HTTPException(status_code=403, detail="Built-in roles cannot be edited")
    try:
        role = await redmine_service.update_role(role_id, payload.name, payload.permissions)
    except httpx.HTTPStatusError as e:
        db.rollback()
        logger.error("Role update failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "update this role")
        raise HTTPException(status_code=code, detail=message)
    except Exception:
        db.rollback()
        logger.exception("Role update failed")
        raise HTTPException(status_code=400, detail="We couldn't update the role right now. Please try again.")
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Something went wrong while saving the role. Please try again.")
    audit_logger.info(
        f"Role {role_id} updated by {current_user.get('username')}",
        extra={"correlation_id": request.state.correlation_id, "extra_data": {"action": "update_role", "username": current_user.get("username"), "status": "success", "role_id": role_id, "role_name": payload.name, "client_ip": _get_client_ip(request)}},
    )
    return role


@router.delete("/redmine/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_redmine_role(
    request: Request,
    role_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    sql = RedmineSQLService(db)
    existing = sql.get_role(role_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Role not found")
    if existing["builtin"] != 0:
        raise HTTPException(status_code=403, detail="Built-in roles cannot be deleted")
    in_use = sql.is_role_in_use(role_id)
    if in_use > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Role in use by {in_use} member(s). Remove users from this role first.",
        )
    try:
        ok = await redmine_service.delete_role(role_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Role not found")
    except HTTPException:
        db.rollback()
        raise
    except httpx.HTTPStatusError as e:
        db.rollback()
        logger.error("Role delete failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "delete this role")
        raise HTTPException(status_code=code, detail=message)
    except Exception:
        db.rollback()
        logger.exception("Role delete failed")
        raise HTTPException(status_code=400, detail="We couldn't delete the role right now. Please try again.")
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Something went wrong while saving the role. Please try again.")
    audit_logger.info(
        f"Role {role_id} deleted by {current_user.get('username')}",
        extra={"correlation_id": request.state.correlation_id, "extra_data": {"action": "delete_role", "username": current_user.get("username"), "status": "success", "role_id": role_id, "role_name": existing["name"], "client_ip": _get_client_ip(request)}},
    )
    return None


# ------------------------------------------------------------------ #
#  GET /api/redmine/users  — Roster: employee dropdown               #
# ------------------------------------------------------------------ #
@router.get("/redmine/users")
async def list_redmine_users(
    current_user: dict = Depends(get_current_user),
):
    """Return a lightweight list of all active Redmine users (id, name, email).
    Used to populate the employee dropdown on the admin roster page."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
    return await redmine_service.get_all_users()


@router.get("/projects", response_model=List[dict])
def list_all_projects(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine projects (direct SQL). Admin, PM, or PC only."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
    sql = RedmineSQLService(db)
    return sql.get_all_projects()

# ── BACKUP: old HTTP-based handler ──────────────────────────────────
# @router.get("/projects", response_model=List[dict])
# async def list_all_projects_http(
#     current_user: dict = Depends(get_current_user),
# ):
#     roles = current_user.get("roles", [])
#     if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Admin, Project Manager, or Project Coordinator access required",
#         )
#     return await redmine_service.get_all_projects()


@router.post("/projects", response_model=dict)
@limiter.limit("10/minute")
async def create_project(
    request: Request,
    data: ProjectCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    # Rate limit: max 10 projects per admin per day
    sql = RedmineSQLService(db)
    email = current_user.get("email")
    user = sql.get_user_by_email(email)
    if user:
        daily = sql.count_daily_projects(user["id"])
        if daily >= 10:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You can create up to 10 projects per day. Please try again tomorrow.",
            )

    try:
        project = await redmine_service.create_project(data)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error("Project create failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "create this project")
        raise HTTPException(status_code=code, detail=message)
    except Exception:
        logger.exception(f"Project creation failed for {data.customerName}")
        raise HTTPException(status_code=400, detail="We couldn't create the project right now. Please try again.")

    audit_logger.info(
        f"Project created: {data.customerName} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "create_project",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "project_name": data.customerName,
                "project_id": project.get("id") if project else None,
                "owner_email": data.email,
            },
        },
    )
    return {"status": "success", "project": project}


@router.put("/projects/{project_id_or_identifier}", response_model=dict)
@limiter.limit("20/minute")
async def update_project(
    project_id_or_identifier: str,
    request: Request,
    data: ProjectUpdate,
    current_user: dict = Depends(get_current_user),
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    try:
        project = await redmine_service.update_project(project_id_or_identifier, data)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error("Project update failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "update this project")
        raise HTTPException(status_code=code, detail=message)
    except Exception:
        logger.exception(f"Project update failed for {project_id_or_identifier}")
        raise HTTPException(status_code=400, detail="We couldn't update the project right now. Please try again.")

    audit_logger.info(
        f"Project updated: {project_id_or_identifier} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "update_project",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "project_id": project.get("id"),
                "project_name": project.get("name") if isinstance(project, dict) else None,
            },
        },
    )
    return {"status": "success", "project": project}


@router.delete("/projects/{project_id_or_identifier}")
@limiter.limit("10/minute")
async def delete_project(
    project_id_or_identifier: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    try:
        deleted = await redmine_service.delete_project(project_id_or_identifier)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error("Project delete failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "delete this project")
        raise HTTPException(status_code=code, detail=message)
    except Exception:
        logger.exception(f"Project deletion failed for {project_id_or_identifier}")
        raise HTTPException(status_code=400, detail="We couldn't delete the project right now. Please try again.")

    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")

    audit_logger.info(
        f"Project deleted: {project_id_or_identifier} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "delete_project",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "project": project_id_or_identifier,
            },
        },
    )
    return {"status": "success", "message": "Project deleted successfully", "id": project_id_or_identifier}


@router.post("/projects/delete-request")
@limiter.limit("5/minute")
def request_project_delete(
    request: Request,
    payload: ProjectDeleteRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    admin_sub = current_user.get("sub")
    admin_email = current_user.get("email")
    if not admin_email:
        raise HTTPException(status_code=400, detail="Admin email not found")

    ids = list(dict.fromkeys(payload.ids))
    if len(ids) > 50:
        raise HTTPException(status_code=400, detail="Max 50 projects per batch")

    today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc)
    recent = db.query(DeleteVerificationToken).filter(
        DeleteVerificationToken.admin_sub == admin_sub,
        DeleteVerificationToken.created_at >= today_start,
    ).count()
    if recent >= 10:  # TEMP raised from 3 for initial cleanup; reset later
        raise HTTPException(status_code=429, detail="Too many delete requests. Try again tomorrow.")

    errors = []
    resolved = []
    for ref in ids:
        row = db.execute(
            text("SELECT id, name FROM redmine.projects WHERE id::text = :v OR identifier = :v"),
            {"v": str(ref)},
        ).fetchone()
        if not row:
            errors.append({"id": ref, "reason": "Project not found"})
        else:
            resolved.append({"input": str(ref), "project_id": row[0], "name": row[1]})

    if errors:
        raise HTTPException(status_code=400, detail={"message": "Pre-validation failed", "failed": errors})

    otp = f"{secrets.randbelow(1000000):06d}"
    token_id = secrets.token_hex(16)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    token = DeleteVerificationToken(
        id=token_id,
        admin_sub=admin_sub,
        admin_email=admin_email,
        otp_hash=hashlib.sha256(otp.encode()).hexdigest(),
        ids_json=json.dumps([r["project_id"] for r in resolved]),
        force=payload.force,
        expires_at=expires_at,
    )
    db.add(token)
    db.commit()

    # Off-loop: smtplib blocks for seconds; never await it in the request path.
    send_email_bg(email_service.send_delete_verification, admin_email, otp, len(resolved), 10, resource_type="project")

    return {
        "request_id": token_id,
        "expires_at": expires_at.isoformat(),
        "count": len(resolved),
        "projects": [{"project_id": r["project_id"], "name": r["name"]} for r in resolved],
        "message": f"Verification code sent to {admin_email}",
    }


@router.post("/projects/delete-confirm")
@limiter.limit("5/minute")
async def confirm_project_delete(
    request: Request,
    payload: ProjectDeleteConfirm,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    admin_sub = current_user.get("sub")
    token = db.query(DeleteVerificationToken).filter(DeleteVerificationToken.id == payload.request_id).first()
    if not token or token.admin_sub != admin_sub:
        raise HTTPException(status_code=404, detail="Verification request not found")
    if token.used:
        raise HTTPException(status_code=400, detail="Verification code already used")

    exp = token.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > exp:
        raise HTTPException(status_code=400, detail="Verification code expired")
    if token.attempts >= 3:
        raise HTTPException(status_code=400, detail="Too many attempts")

    if hashlib.sha256(payload.otp.encode()).hexdigest() != token.otp_hash:
        token.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid verification code")

    try:
        ids = json.loads(token.ids_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request data")

    # Resolve project names before deletion so the audit log can show them
    name_map = {}
    if ids:
        placeholders = ", ".join(f"'{str(i)}'" for i in ids)
        rows = db.execute(
            text(f"SELECT id, name FROM redmine.projects WHERE id::text IN ({placeholders})")
        ).fetchall()
        name_map = {str(r[0]): r[1] for r in rows}

    deleted = []
    failed = []
    for pid in ids:
        pname = name_map.get(str(pid))
        try:
            ok = await redmine_service.delete_project(pid)
            if ok:
                deleted.append({"id": pid, "name": pname})
            else:
                failed.append({"id": pid, "name": pname, "reason": "Project not found"})
        except Exception as e:
            failed.append({"id": pid, "name": pname, "reason": str(e)[:200]})

    token.used = True
    db.commit()

    deleted_names = ", ".join(d["name"] or f"#{d['id']}" for d in deleted) or "none"
    failed_names = ", ".join(f["name"] or f"#{f['id']}" for f in failed) or "none"

    audit_logger.info(
        f"Verified project deletion by {current_user.get('username')}: {len(deleted)} deleted ({deleted_names}), {len(failed)} failed ({failed_names})",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "delete_projects_verified",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "deleted_ids": [d["id"] for d in deleted],
                "deleted_names": [d["name"] for d in deleted],
                "failed": failed,
            },
        },
    )

    return {
        "status": "success",
        "message": f"{len(deleted)} project(s) deleted",
        "deleted_ids": [d["id"] for d in deleted],
        "deleted_projects": deleted,
        "failed": failed,
    }


@router.get("/admin/projects", response_model=AdminProjectListResponse)
@limiter.limit("30/minute")
def list_admin_projects(
    request: Request,
    q: Optional[str] = Query(None, max_length=100, description="Search name, identifier, description, custom fields"),
    status_filter: Optional[str] = Query(None, alias="status", description="active, closed, archived, or all"),
    project_type: Optional[str] = Query(None, max_length=100, description="Filter by project type"),
    city: Optional[str] = Query(None, max_length=100, description="Filter by city"),
    page: int = Query(1, ge=1, description="Page number (default: 1)"),
    page_size: int = Query(20, ge=1, le=100, description="Page size (default: 20, max: 100)"),
    sort_by: str = Query("created_on", description="created_on, updated_on, name, id, status"),
    sort_order: str = Query("desc", description="asc or desc"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Admin-only endpoint to list projects with full data, filters, backend-safe searching, and pagination.
    """
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    sql = RedmineSQLService(db)
    return sql.get_admin_projects_paginated(
        search=q,
        status=status_filter,
        project_type=project_type,
        city=city,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/admin/projects/{project_id}", response_model=AdminProjectDetail)
@limiter.limit("30/minute")
def get_admin_project_detail(
    request: Request,
    project_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin-only endpoint to fetch full project details by id."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    sql = RedmineSQLService(db)
    detail = sql.get_project_details(project_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Project not found")
    return detail


@router.get("/projects/{email}", response_model=List[ProjectResponse])
async def get_user_projects_by_email_alias(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    # Admin or self
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_projects_for_user(user["id"])

@router.get("/user-projects/email/{email}", response_model=List[ProjectResponse])
async def get_user_projects_by_email(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    # Admin or self
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_projects_for_user(user["id"])

@router.get("/user-projects/id/{user_id}", response_model=List[ProjectResponse])
async def get_user_projects_by_id(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    # Admin only (since we can't easily check self by ID here without a lookup)
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
        
    return await redmine_service.get_projects_for_user(user_id)

@router.get("/user-projects", response_model=List[UserWithProjects])
async def get_all_user_projects(
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
    return await redmine_service.get_all_users_with_projects()

# @router.get("/user-issues/email/{email}", response_model=List[IssueResponse])
# async def get_user_issues_by_email(
#     email: str,
#     current_user: dict = Depends(get_current_user)
# ):
#     # Admin or self
#     if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
#         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
#
#     user = await redmine_service.get_user_by_email(email)
#     if not user:
#         raise HTTPException(status_code=404, detail="User not found")
#     return await redmine_service.get_issues_for_user(user["id"])


@router.get("/projects/all/issues", response_model=List[IssueResponse])
def get_all_project_issues(
    team: Optional[bool] = Query(False, description="Set to true to view your team's issues. Admin/PM/PC only."),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)

    email = current_user.get("email")
    current_rm_user = sql.get_user_by_email(email)
    if not current_rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = not is_admin and not is_pm_or_pc

    # Own issues (default) — all roles see only assigned issues
    if not team:
        if is_tr:
            return sql.get_all_issues_for_user(current_rm_user["id"], assigned_only=True, include_authored=True)
        return sql.get_all_issues_for_user(current_rm_user["id"], assigned_only=True)

    # Team issues
    if is_admin:
        return sql.get_all_issues_admin()

    if not is_pm_or_pc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can view team issues.",
        )

    team_ids = sql.get_team_member_ids(current_rm_user["id"])
    team_ids.add(current_rm_user["id"])
    seen = set()
    issues = []
    for tid in team_ids:
        for issue in sql.get_all_issues_for_user(tid, assigned_only=False):
            if issue["id"] not in seen:
                seen.add(issue["id"])
                issues.append(issue)
    return issues


def _parse_issue_date(value: Optional[str], add_day: bool = False):
    if not value:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    return d + timedelta(days=1) if add_day else d


def _paginated_response(records: list, total: int, page: int, page_size: int) -> dict:
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    return {"records": records, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


def _build_issues_excel(records: list) -> BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Issues"
    ws.append(["ID", "Subject", "Description", "Status", "Priority", "Tracker", "Project",
               "Assigned To", "Author", "Created On", "Updated On", "Start Date", "Due Date",
               "Estimated Hours", "Done Ratio", "Parent Issue ID", "Subtasks Count"])
    for r in records:
        ws.append([
            r.get("id"),
            r.get("subject"),
            r.get("description"),
            r.get("status"),
            r.get("priority"),
            r.get("tracker"),
            r.get("project_name"),
            r.get("assigned_to_name"),
            r.get("author_name"),
            r.get("created_on"),
            r.get("updated_on"),
            r.get("start_date"),
            r.get("due_date"),
            r.get("estimated_hours"),
            r.get("done_ratio"),
            r.get("parent_issue_id"),
            r.get("subtasks_count", 0),
        ])
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def _export_issues(request: Request, current_user: dict, records: list, label: str):
    output = _build_issues_excel(records)
    audit_logger.info(
        f"Issues exported by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": f"export_{label}_issues",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "exported_rows": len(records),
            },
        },
    )
    filename = f"{label}_issues_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/my-issues/paginated", response_model=IssueListResponse)
@limiter.limit("30/minute")
def get_my_issues_paginated(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_by: str = Query("updated_on", description="updated_on, created_on, subject, priority, status, due_date, done_ratio, project_name"),
    sort_dir: str = Query("desc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    status_id: Optional[int] = Query(None, description="Filter by status id"),
    priority_id: Optional[int] = Query(None, description="Filter by priority id"),
    tracker_id: Optional[int] = Query(None, description="Filter by tracker id"),
    project_id: Optional[int] = Query(None, description="Filter by project id"),
    search: Optional[str] = Query(None, description="Search subject or author name"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not any(r in roles for r in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(status_code=403, detail="Access denied")

    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    is_tr = "Technical Resource" in roles and "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles

    records, total = sql.get_issues_paginated(
        scope_user_id=rm_user["id"],
        include_authored=is_tr,
        status_id=status_id,
        priority_id=priority_id,
        tracker_id=tracker_id,
        project_id=project_id,
        search=(search or "").strip()[:100] or None,
        from_date=_parse_issue_date(from_date),
        to_date=_parse_issue_date(to_date, add_day=True),
        sort_by=sort_by,
        sort_dir=sort_dir if sort_dir in ("asc", "desc") else "desc",
        limit=page_size,
        offset=(page - 1) * page_size,
        export=export,
    )
    if export:
        return _export_issues(request, current_user, records, "my")
    return _paginated_response(records, total, page, page_size)


@router.get("/team-issues", response_model=IssueListResponse)
@limiter.limit("30/minute")
def get_team_issues_paginated(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_by: str = Query("updated_on", description="updated_on, created_on, subject, priority, status, due_date, done_ratio, project_name"),
    sort_dir: str = Query("desc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    status_id: Optional[int] = Query(None, description="Filter by status id"),
    priority_id: Optional[int] = Query(None, description="Filter by priority id"),
    tracker_id: Optional[int] = Query(None, description="Filter by tracker id"),
    project_id: Optional[int] = Query(None, description="Filter by project id"),
    search: Optional[str] = Query(None, description="Search subject or author name"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not any(r in roles for r in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    is_admin = "Admin" in roles
    team_ids = None
    if not is_admin:
        team_ids = sql.get_team_member_ids(rm_user["id"])
        team_ids.add(rm_user["id"])

    records, total = sql.get_issues_paginated(
        admin_all=is_admin,
        team_ids=team_ids,
        status_id=status_id,
        priority_id=priority_id,
        tracker_id=tracker_id,
        project_id=project_id,
        search=(search or "").strip()[:100] or None,
        from_date=_parse_issue_date(from_date),
        to_date=_parse_issue_date(to_date, add_day=True),
        sort_by=sort_by,
        sort_dir=sort_dir if sort_dir in ("asc", "desc") else "desc",
        limit=page_size,
        offset=(page - 1) * page_size,
        export=export,
    )
    if export:
        return _export_issues(request, current_user, records, "team")
    return _paginated_response(records, total, page, page_size)


@router.get("/projects/{project_id}/issues", response_model=List[IssueResponse])
async def get_project_issues(
    project_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Get issues for a project.
    - Admin/PM/PC: sees all issues.
    - TR: sees only issues assigned to them.
    """
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    is_tr = "Technical Resource" in roles and "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles

    if is_tr:
        user = await redmine_service.get_user_by_email(current_user.get("email"))
        if not user:
            raise HTTPException(status_code=404, detail="User not found in Redmine")
        return await redmine_service.get_issues_for_project(project_id, assigned_to_id=user["id"])

    return await redmine_service.get_issues_for_project(project_id)

@router.get("/user-issues/id/{user_id}", response_model=List[IssueResponse])
async def get_user_issues_by_id(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
    return await redmine_service.get_issues_for_user(user_id)

@router.get("/my-projects", response_model=List[ProjectResponse])
def get_my_projects(
    user_id: Optional[int] = Query(None, description="Admin/PM/PC: view another user's projects"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get projects for a user (direct SQL — no Redmine HTTP API)."""
    email = current_user.get("email")
    roles = current_user.get("roles", [])

    sql = RedmineSQLService(db)

    if user_id is None:
        if not email:
            raise HTTPException(status_code=400, detail="User email not found in session")
        user = sql.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found in Redmine. Contact admin to sync your account.")
        return sql.get_projects_for_user_rich(user["id"])

    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )

    return sql.get_projects_for_user_rich(user_id)

# ── BACKUP: old HTTP-based handler (Redmine API) ────────────────────
# @router.get("/my-projects", response_model=List[ProjectResponse])
# async def get_my_projects_http(
#     user_id: Optional[int] = Query(None, description="Admin/PM/PC: view another user's projects"),
#     current_user: dict = Depends(get_current_user),
# ):
#     email = current_user.get("email")
#     username = current_user.get("username")
#     roles = current_user.get("roles", [])
#
#     if user_id is None:
#         if not email:
#             raise HTTPException(status_code=400, detail="User email not found in session")
#         user = await redmine_service.get_user_by_email(email)
#         if not user:
#             user = await redmine_service.create_user(username, email)
#             if not user:
#                 raise HTTPException(status_code=500, detail="Failed to sync user with Redmine")
#         return await redmine_service.get_projects_for_user(user["id"])
#
#     if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Admin, Project Manager, or Project Coordinator access required",
#         )
#
#     return await redmine_service.get_projects_for_user(user_id)

@router.get("/my-issues", response_model=List[IssueResponse])
async def get_my_issues(current_user: dict = Depends(get_current_user)):
    """Get issues for the currently logged-in user, auto-creating them in Redmine if missing."""
    email = current_user.get("email")
    username = current_user.get("username")
    
    if not email:
        raise HTTPException(status_code=400, detail="User email not found in session")
        
    user = await redmine_service.get_user_by_email(email)
    if not user:
        user = await redmine_service.create_user(username, email)
        if not user:
            raise HTTPException(status_code=500, detail="Failed to sync user with Redmine")
            
    return await redmine_service.get_issues_for_user(user["id"])


@router.get("/project-members/{project_id}", response_model=List[ProjectMember])
def get_project_members(
    project_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all members of a project (direct SQL). Scoped by user role."""
    roles = current_user.get("roles", [])
    sql = RedmineSQLService(db)

    if "Admin" in roles:
        members = sql.get_project_members(project_id)
    else:
        email = current_user.get("email")
        user = sql.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=403, detail="Redmine account not found")
        user_projects = sql.get_projects_for_user(user["id"])
        if not any(p.id == project_id for p in user_projects):
            raise HTTPException(status_code=403, detail="You are not a member of this project")
        members = sql.get_project_members(project_id)

    for m in members:
        m["email"] = m.get("email", "")
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == m.get("user_id")).first()
        if emp:
            m["email"] = emp.user_email
            emp_name = f"{emp.first_name or ''} {emp.middle_name or ''} {emp.last_name or ''}".strip().replace("  ", " ")
            if emp_name:
                m["name"] = emp_name

    return members


@router.get("/projects/{project_id}/members")
def list_project_members(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("name", description="name, email, designation"),
    sort_dir: str = Query("asc", description="asc or desc"),
    search: Optional[str] = Query(None, max_length=100, description="Search name, email, designation"),
    role_id: Optional[str] = Query(None, max_length=200, description="Comma-separated role ids"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_members(current_user, db, project_id):
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    sql = RedmineSQLService(db)
    members = sql.get_project_memberships(project_id)
    for m in members:
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == m["user_id"]).first()
        if emp:
            if emp.user_email:
                m["email"] = emp.user_email
            emp_name = f"{emp.first_name or ''} {emp.middle_name or ''} {emp.last_name or ''}".strip().replace("  ", " ")
            if emp_name:
                m["name"] = emp_name
            m["designation"] = emp.designation
        else:
            m["designation"] = None

    # Search filter (name, email, designation)
    if search:
        term = search.strip().lower()[:100]
        if term:
            members = [
                m for m in members
                if term in " ".join(filter(None, [m.get("name"), m.get("email"), m.get("designation")])).lower()
            ]

    # Role filter (any-of)
    if role_id:
        try:
            rids = [int(x.strip()) for x in role_id.split(",") if x.strip()][:50]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid role_id. Use comma-separated role ids.")
        if rids:
            members = [
                m for m in members
                if any(r.get("id") in rids for r in m.get("roles", []))
            ]

    # Sort (whitelisted)
    sort_attr = sort_by if sort_by in ("name", "email", "designation") else "name"
    members.sort(key=lambda m: (m.get(sort_attr) or "").lower(), reverse=(sort_dir == "desc"))

    # Paginate
    total = len(members)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    offset = (page - 1) * page_size
    records = members[offset:offset + page_size]

    return {
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.post("/projects/{project_id}/members")
@limiter.limit("10/minute")
async def add_project_member(
    request: Request,
    project_id: int,
    payload: MemberAdd,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_members(current_user, db, project_id):
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    user_row = db.execute(
        text("SELECT u.firstname, u.lastname, e.address FROM redmine.users u LEFT JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true WHERE u.id = :uid"),
        {"uid": payload.user_id},
    ).fetchone()
    user_name = f"{user_row[0]} {user_row[1]}".strip() if user_row else None
    user_email = (user_row[2] if user_row and user_row[2] else None)
    user_label = user_email or user_name or f"#{payload.user_id}"

    project_row = db.execute(
        text("SELECT name FROM redmine.projects WHERE id = :pid"),
        {"pid": project_id},
    ).fetchone()
    project_name = project_row[0] if project_row else None
    project_label = project_name or f"#{project_id}"

    ok = await redmine_service.add_membership(project_id, payload.user_id, payload.role_ids)
    if not ok:
        raise HTTPException(status_code=409, detail="User is already a member of this project or invalid input.")

    audit_logger.info(
        f"Member {user_label} added to project {project_label} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "add_member",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "project_id": project_id,
                "project_name": project_name,
                "user_id": payload.user_id,
                "user_name": user_name,
                "user_email": user_email,
            },
        },
    )
    return {"status": "success", "message": "Member added"}


@router.put("/projects/{project_id}/members/{user_id}")
@limiter.limit("10/minute")
async def update_project_member(
    request: Request,
    project_id: int,
    user_id: int,
    payload: MemberUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_members(current_user, db, project_id):
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    sql = RedmineSQLService(db)
    member = next((m for m in sql.get_project_memberships(project_id) if m["user_id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this project.")

    member_name = member.get("name")
    member_email = member.get("email")
    member_label = member_email or member_name or f"#{user_id}"

    project_row = db.execute(
        text("SELECT name FROM redmine.projects WHERE id = :pid"),
        {"pid": project_id},
    ).fetchone()
    project_name = project_row[0] if project_row else None
    project_label = project_name or f"#{project_id}"

    ok = await redmine_service.update_membership(member["membership_id"], payload.role_ids)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to update member roles.")

    audit_logger.info(
        f"Member roles updated for {member_label} in project {project_label} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "update_member",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "project_id": project_id,
                "project_name": project_name,
                "user_id": user_id,
                "user_name": member_name,
                "user_email": member_email,
            },
        },
    )
    return {"status": "success", "message": "Member updated"}


@router.delete("/projects/{project_id}/members/{user_id}")
@limiter.limit("10/minute")
async def remove_project_member(
    request: Request,
    project_id: int,
    user_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_members(current_user, db, project_id):
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    sql = RedmineSQLService(db)
    user_row = db.execute(
        text("SELECT u.firstname, u.lastname, e.address FROM redmine.users u LEFT JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true WHERE u.id = :uid"),
        {"uid": user_id},
    ).fetchone()
    user_name = f"{user_row[0]} {user_row[1]}".strip() if user_row else None
    user_email = (user_row[2] if user_row and user_row[2] else None)
    user_label = user_email or user_name or f"#{user_id}"

    project_row = db.execute(
        text("SELECT name FROM redmine.projects WHERE id = :pid"),
        {"pid": project_id},
    ).fetchone()
    project_name = project_row[0] if project_row else None
    project_label = project_name or f"#{project_id}"

    removed = await redmine_service.remove_user_from_project(user_id, project_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found in this project.")

    audit_logger.info(
        f"Member {user_label} removed from project {project_label} by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "remove_member",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "project_id": project_id,
                "project_name": project_name,
                "user_id": user_id,
                "user_name": user_name,
                "user_email": user_email,
            },
        },
    )
    return {"status": "success", "message": "Member removed"}


# ── BACKUP: old HTTP-based handler ──────────────────────────────────
# @router.get("/project-members/{project_id}", response_model=List[ProjectMember])
# async def get_project_members_http(
#     project_id: int,
#     current_user: dict = Depends(get_current_user),
#     db: Session = Depends(get_db),
# ):
#     roles = current_user.get("roles", [])
#     if "Admin" in roles:
#         members = await redmine_service.get_project_members(project_id)
#     else:
#         email = current_user.get("email")
#         user = await redmine_service.get_user_by_email(email)
#         if not user:
#             raise HTTPException(status_code=403, detail="Redmine account not found")
#         user_projects = await redmine_service.get_projects_for_user(user["id"])
#         if not any(p.id == project_id for p in user_projects):
#             raise HTTPException(status_code=403, detail="You are not a member of this project")
#         members = await redmine_service.get_project_members(project_id)
#
#     for m in members:
#         m["email"] = m.get("email", "")
#         emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == m.get("user_id")).first()
#         if emp:
#             m["email"] = emp.user_email
#
#     return members


@router.get("/search", response_model=SearchResponse)
async def global_search(
    q: str = Query(..., min_length=1, max_length=100, description="Search query"),
    limit: int = Query(5, ge=1, le=50, description="Max results per section"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Global search across projects, people, and shifts."""
    projects = await redmine_service.search_projects(q, limit)
    project_ids = [int(p["id"]) for p in projects]
    people = await redmine_service.search_people(project_ids, limit) if project_ids else []
    shifts = await shift_service.search_shifts(db, q, limit)

    return SearchResponse(
        query=q,
        results=SearchResults(
            projects=[ProjectSearchItem(**p) for p in projects],
            people=[PersonSearchItem(**p) for p in people],
            shifts=[ShiftSearchItem(**s) for s in shifts],
        ),
    )


@router.get("/redmine/trackers")
def list_trackers(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue trackers (Bug, Feature, Support, etc.)"""
    sql = RedmineSQLService(db)
    return sql.get_trackers()


@router.get("/redmine/priorities")
def list_priorities(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue priorities (Low, Normal, High, etc.)"""
    sql = RedmineSQLService(db)
    return sql.get_priorities()


@router.get("/redmine/statuses")
def list_statuses(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue statuses (New, In Progress, Closed, etc.)"""
    sql = RedmineSQLService(db)
    return sql.get_statuses()


@router.get("/redmine/users")
def list_users(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all active Redmine users for assignee/watcher dropdowns."""
    sql = RedmineSQLService(db)
    return sql.get_all_users()


@router.get("/redmine/custom_fields")
def list_custom_fields(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue custom field definitions."""
    sql = RedmineSQLService(db)
    return sql.get_custom_fields()


@router.get("/redmine/issues/search")
def search_redmine_issues(
    q: str = Query(..., min_length=1, max_length=100, description="Search query"),
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search issues by subject (ILIKE). Optionally filter by project."""
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    sql = RedmineSQLService(db)
    return sql.search_issues(q, project_id, limit)


@router.get("/redmine/versions")
def list_versions(
    project_id: int = Query(..., description="Project ID"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all versions (sprint/milestones) for a project."""
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    sql = RedmineSQLService(db)
    return sql.get_versions(project_id)


@router.get("/redmine/categories")
def list_categories(
    project_id: int = Query(..., description="Project ID"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all issue categories for a project."""
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    sql = RedmineSQLService(db)
    return sql.get_categories(project_id)


@router.post("/redmine/issues", status_code=201)
async def create_redmine_issue(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    data: str = Form(...),
    files: list[UploadFile] = File(None),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Parse JSON data
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")
    try:
        issue_data = IssueCreate(**parsed)
    except PydanticValidationError as e:
        raise HTTPException(status_code=422, detail=_validation_errors(e))

    sql = RedmineSQLService(db)
    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your account isn't set up for tickets yet. Please contact your administrator.",
        )

    redmine_user_id = user["id"]

    # Rate limit: max 50 issues per user per day
    daily_count = sql.count_daily_issues(redmine_user_id)
    if daily_count >= 50:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You can create up to 50 tickets per day. Please try again tomorrow.",
        )

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    # Project membership check (skip for Admin)
    if not is_admin:
        if not sql.is_project_member(redmine_user_id, issue_data.project_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this project",
            )

    # Duplicate check: same subject + same project
    existing = sql.get_duplicate_issue(issue_data.subject, issue_data.project_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An issue with this subject already exists in this project",
        )

    # Subtask validation: parent must exist and live in the same project
    # (Redmine rejects cross-project subtasks). Membership of the parent's
    # project is covered by the project membership check below.
    if issue_data.parent_issue_id is not None:
        parent_project_id = sql.get_issue_project_id(issue_data.parent_issue_id)
        if parent_project_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent issue #{issue_data.parent_issue_id} not found",
            )
        if parent_project_id != issue_data.project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subtask must belong to the same project as its parent issue #{issue_data.parent_issue_id}",
            )

    # Build issue payload
    issue_attrs = issue_data.model_dump(exclude_none=True)
    issue_attrs["author_id"] = redmine_user_id

    # Validate assignee is a project member
    if "assigned_to_id" in issue_attrs and issue_attrs["assigned_to_id"] != redmine_user_id:
        if not sql.is_project_member(issue_attrs["assigned_to_id"], issue_data.project_id):
            del issue_attrs["assigned_to_id"]

    # Upload files to Redmine and get tokens
    uploads = []
    if files:
        if len(files) > MAX_FILE_COUNT:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_FILE_COUNT} files allowed per request",
            )
        for f in files:
            _validate_uploaded_file(f)
            file_bytes = await f.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"File {f.filename} exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")
            upload_result = await redmine_service.upload_file(file_bytes, f.filename, f.content_type)
            uploads.append({
                "token": upload_result["token"],
                "filename": f.filename or "attachment",
                "content_type": f.content_type or "application/octet-stream",
                "description": "",
            })

    if uploads:
        issue_attrs["uploads"] = uploads

    try:
        issue = await redmine_service.create_issue(issue_attrs, switch_user=user["login"])

        # Background-schedule a notification if the issue is assigned to someone
        # other than the creator. We resolve the assignee's employee_master row
        # here (using their email) so the background task only needs to insert.
        assigned_to_id = issue_attrs.get("assigned_to_id")
        creator_id = redmine_user_id
        if assigned_to_id and assigned_to_id != creator_id:
            try:
                assignee_emp_id = _resolve_employee_id_from_redmine_user_id(db, assigned_to_id)
                if assignee_emp_id is not None:
                    # Resolve the actor (creator) from the session so we can
                    # populate actor_id. The session's `sub` is the Keycloak
                    # user id, which is stored on employee_master.keycloak_user_id.
                    actor_sub = current_user.get("sub")
                    actor_emp_id = None
                    if actor_sub:
                        try:
                            actor_emp = (
                                db.query(EmployeeMaster)
                                .filter(EmployeeMaster.keycloak_user_id == str(actor_sub))
                                .first()
                            )
                            if actor_emp:
                                actor_emp_id = actor_emp.id
                        except Exception:
                            actor_emp_id = None
                    creator_name = (
                        (user.get("firstname") or "")
                        + " "
                        + (user.get("lastname") or "")
                    ).strip() or user.get("login") or "Someone"
                    schedule_notification(
                        background_tasks,
                        user_id=assignee_emp_id,
                        category="ticket_assigned",
                        title=f"Ticket #{issue['id']} assigned to you",
                        message=f"{creator_name} assigned a ticket to you: {issue_attrs.get('subject', '')}",
                        priority="normal",
                        actor_id=actor_emp_id,
                        actor_name=creator_name,
                        link=f"/api/redmine/issues/{issue['id']}",
                        extra={
                            "issue_id": str(issue["id"]),
                            "project_id": str(issue_attrs.get("project_id", "")),
                        },
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to schedule ticket_assigned notification: {e}"
                )

        return {"status": "success", "message": "Issue created successfully", "id": issue["id"]}
    except httpx.HTTPStatusError as e:
        logger.error("Ticket create failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "create tickets in this project")
        raise HTTPException(status_code=code, detail=message)
    except Exception as e:
        logger.exception("Ticket create failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't create the ticket right now. Please try again or contact your administrator.",
        )


@router.get("/redmine/attachments/{attachment_id}")
async def download_attachment(
    attachment_id: int,
    current_user: dict = Depends(get_current_user),
):
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{redmine_service.url}/attachments/download/{attachment_id}",
            headers={"X-Redmine-API-Key": redmine_service.headers["X-Redmine-API-Key"]},
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Attachment not found")
        if resp.status_code >= 400:
            logger.error("Attachment download failed: status=%s", resp.status_code)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="We couldn't download the attachment right now. Please try again.",
            )

        content_type = resp.headers.get("content-type", "application/octet-stream")
        return StreamingResponse(
            resp.iter_bytes(),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="attachment-{attachment_id}"',
                "Content-Length": resp.headers.get("content-length", ""),
            },
        )


@router.delete("/redmine/attachments/{attachment_id}")
async def delete_attachment(
    attachment_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    sql = RedmineSQLService(db)
    info = sql.get_attachment_issue_info(attachment_id)
    if not info:
        raise HTTPException(status_code=404, detail="Attachment not found")

    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="Your account isn't set up for tickets yet. Please contact your administrator.")
    redmine_user_id = user["id"]

    if is_admin:
        pass
    elif is_pm_or_pc:
        if not sql.is_project_member(redmine_user_id, info["project_id"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this issue's project",
            )
    elif is_tr:
        if info["assigned_to_id"] != redmine_user_id and info["author_id"] != redmine_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete attachments from tickets you created or are assigned to",
            )
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    deleted = await redmine_service.delete_attachment(attachment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return {"status": "success", "message": "Attachment deleted successfully", "id": attachment_id}


@router.post("/redmine/issues/{issue_id}/attachments")
async def upload_issue_attachment(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    sql = RedmineSQLService(db)
    email = current_user.get("email")
    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=400, detail="Your account isn't set up for tickets yet. Please contact your administrator.")

    redmine_user_id = user["id"]
    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    issue_pid = sql.get_issue_project_id(issue_id)
    if issue_pid is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    if is_admin:
        pass
    elif is_pm_or_pc:
        if not sql.is_project_member(redmine_user_id, issue_pid):
            raise HTTPException(status_code=403, detail="You are not a member of this issue's project")
    elif is_tr:
        assigned_to = sql.get_issue_assigned_to(issue_id)
        author_id = sql.get_issue_author_id(issue_id)
        if assigned_to != redmine_user_id and author_id != redmine_user_id:
            raise HTTPException(status_code=403, detail="You can only add recordings to tickets you created or are assigned to")
    else:
        raise HTTPException(status_code=403, detail="Access denied")

    # Rate limit: max 20 uploads per user per day
    daily = sql.count_daily_updates(redmine_user_id)
    if daily >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You can upload up to 20 recordings per day. Please try again tomorrow.",
        )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in RECORDING_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Only audio recordings are allowed. Got '{ext}'",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")

    existing = sql.count_issue_attachments(issue_id)
    if existing + 1 > 10:
        raise HTTPException(
            status_code=400,
            detail=f"Attachment limit reached (10 per ticket). Create a new ticket and attach there.",
        )

    upload_result = await redmine_service.upload_file(file_bytes, file.filename, file.content_type)
    uploads = [{
        "token": upload_result["token"],
        "filename": file.filename or "recording",
        "content_type": file.content_type or "application/octet-stream",
        "description": "",
    }]

    await redmine_service.update_issue(issue_id, {"uploads": uploads}, switch_user=user["login"])
    return {"status": "success", "message": "Recording uploaded", "id": issue_id}


@router.get("/redmine/issues/{issue_id}")
def get_redmine_issue(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    sql = RedmineSQLService(db)
    issue = sql.get_issue_by_id_enriched(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    # Scope like the subtasks/edit endpoints: Admin has full access; PM/PC only
    # in the issue's project; everyone else only their own (assigned/authored).
    if "Admin" not in roles:
        rm_user = sql.get_user_by_email(current_user.get("email"))
        if not rm_user:
            raise HTTPException(status_code=404, detail="User not found in Redmine")
        if "Project Manager" in roles or "Project Coordinator" in roles:
            if not sql.is_project_member(rm_user["id"], issue["project_id"]):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of this issue's project",
                )
        elif issue.get("assigned_to_id") != rm_user["id"] and issue.get("author_id") != rm_user["id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view tickets you created or are assigned to",
            )
    return {"status": "success", "issue": issue}


@router.get("/redmine/issues/{issue_id}/subtasks", response_model=IssueListResponse)
@limiter.limit("30/minute")
def get_issue_subtasks(
    issue_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_by: str = Query("updated_on", description="updated_on, created_on, subject, priority, status, due_date, done_ratio, project_name"),
    sort_dir: str = Query("desc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    status_id: Optional[int] = Query(None, description="Filter by status id"),
    priority_id: Optional[int] = Query(None, description="Filter by priority id"),
    tracker_id: Optional[int] = Query(None, description="Filter by tracker id"),
    search: Optional[str] = Query(None, description="Search subject or author name"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List subtasks of an issue.

    Same pagination, filters, response shape, and role-based security
    as the team-issues endpoint — scoped to children of ``issue_id``.
    """
    roles = current_user.get("roles", [])
    if not any(r in roles for r in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(status_code=403, detail="Access denied")

    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    parent_project_id = sql.get_issue_project_id(issue_id)
    if parent_project_id is None:
        raise HTTPException(status_code=404, detail="Parent issue not found")

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles

    scope_user_id = None
    include_authored = False
    if not is_admin:
        if is_pm_or_pc:
            if not sql.is_project_member(rm_user["id"], parent_project_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of this issue's project",
                )
        else:  # Technical Resource: parent must be theirs, children scoped to them
            assigned_to = sql.get_issue_assigned_to(issue_id)
            author_id = sql.get_issue_author_id(issue_id)
            if assigned_to != rm_user["id"] and author_id != rm_user["id"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only view subtasks of tickets you created or are assigned to",
                )
            scope_user_id = rm_user["id"]
            include_authored = True

    records, total = sql.get_issues_paginated(
        scope_user_id=scope_user_id,
        include_authored=include_authored,
        parent_id=issue_id,
        status_id=status_id,
        priority_id=priority_id,
        tracker_id=tracker_id,
        search=(search or "").strip()[:100] or None,
        from_date=_parse_issue_date(from_date),
        to_date=_parse_issue_date(to_date, add_day=True),
        sort_by=sort_by,
        sort_dir=sort_dir if sort_dir in ("asc", "desc") else "desc",
        limit=page_size,
        offset=(page - 1) * page_size,
        export=export,
    )
    if export:
        return _export_issues(request, current_user, records, "subtasks")
    return _paginated_response(records, total, page, page_size)


@router.delete("/redmine/issues/{issue_id}")
async def delete_redmine_issue(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    try:
        deleted = await redmine_service.delete_issue(issue_id)
    except httpx.HTTPStatusError as e:
        logger.error("Ticket delete failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "delete this ticket")
        raise HTTPException(status_code=code, detail=message)
    except Exception:
        logger.exception("Ticket delete failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't delete the ticket right now. Please try again or contact your administrator.",
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Issue not found")

    return {"status": "success", "message": "Issue deleted successfully", "id": issue_id}


@router.put("/redmine/issues/{issue_id}")
async def update_redmine_issue(
    issue_id: int,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    data: str = Form(...),
    files: list[UploadFile] = File(None),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Parse JSON data
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")
    try:
        issue_data = IssueUpdate(**parsed)
    except PydanticValidationError as e:
        raise HTTPException(status_code=422, detail=_validation_errors(e))

    sql = RedmineSQLService(db)
    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your account isn't set up for tickets yet. Please contact your administrator.",
        )

    redmine_user_id = user["id"]

    # Rate limit: max 20 updates per user per day
    daily = sql.count_daily_updates(redmine_user_id)
    if daily >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You can update up to 20 tickets per day. Please try again tomorrow.",
        )

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    # Role-based access control
    issue_pid = sql.get_issue_project_id(issue_id)
    if issue_pid is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    if is_admin:
        pass  # full access
    elif is_pm_or_pc:
        if not sql.is_project_member(redmine_user_id, issue_pid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this issue's project",
            )
    elif is_tr:
        assigned_to = sql.get_issue_assigned_to(issue_id)
        author_id = sql.get_issue_author_id(issue_id)
        if assigned_to != redmine_user_id and author_id != redmine_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only edit tickets you created or are assigned to",
            )
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Subtask validation: new parent must exist, share the issue's project,
    # and not be the issue itself (deeper cycles are rejected by Redmine).
    if issue_data.parent_issue_id is not None:
        if issue_data.parent_issue_id == issue_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An issue cannot be a subtask of itself",
            )
        parent_project_id = sql.get_issue_project_id(issue_data.parent_issue_id)
        if parent_project_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent issue #{issue_data.parent_issue_id} not found",
            )
        if parent_project_id != issue_pid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subtask must belong to the same project as its parent issue #{issue_data.parent_issue_id}",
            )

    # Build issue payload
    issue_attrs = issue_data.model_dump(exclude_none=True)
    notes = issue_attrs.pop("notes", None)
    private_notes = issue_attrs.pop("private_notes", None)

    if notes:
        issue_attrs["notes"] = notes
    if private_notes is not None:
        issue_attrs["private_notes"] = private_notes

    # Validate assignee is a project member
    if "assigned_to_id" in issue_attrs and issue_attrs["assigned_to_id"] != redmine_user_id:
        if not sql.is_project_member(issue_attrs["assigned_to_id"], issue_pid):
            del issue_attrs["assigned_to_id"]

    # Upload files to Redmine and get tokens
    uploads = []
    if files:
        existing = sql.count_issue_attachments(issue_id)
        if existing + len(files) > 10:
            raise HTTPException(
                status_code=400,
                detail=f"Attachment limit reached (10 per ticket). Create a new ticket and attach there.",
            )
        for f in files:
            _validate_uploaded_file(f)
            file_bytes = await f.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"File {f.filename} exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")
            upload_result = await redmine_service.upload_file(file_bytes, f.filename, f.content_type)
            uploads.append({
                "token": upload_result["token"],
                "filename": f.filename or "attachment",
                "content_type": f.content_type or "application/octet-stream",
                "description": "",
            })

    if uploads:
        issue_attrs["uploads"] = uploads

    try:
        issue = await redmine_service.update_issue(issue_id, issue_attrs, switch_user=user["login"])

        # Background-schedule a notification if the update assigned the issue
        # to a new user (different from the creator).
        assigned_to_id = issue_attrs.get("assigned_to_id")
        creator_id = redmine_user_id
        if assigned_to_id and assigned_to_id != creator_id:
            try:
                assignee_emp_id = _resolve_employee_id_from_redmine_user_id(db, assigned_to_id)
                if assignee_emp_id is not None:
                    # Resolve the updater (session user) so we can populate
                    # actor_id from employee_master via the Keycloak sub.
                    actor_sub = current_user.get("sub")
                    actor_emp_id = None
                    if actor_sub:
                        try:
                            actor_emp = (
                                db.query(EmployeeMaster)
                                .filter(EmployeeMaster.keycloak_user_id == str(actor_sub))
                                .first()
                            )
                            if actor_emp:
                                actor_emp_id = actor_emp.id
                        except Exception:
                            actor_emp_id = None
                    updater_name = (
                        (user.get("firstname") or "")
                        + " "
                        + (user.get("lastname") or "")
                    ).strip() or user.get("login") or "Someone"
                    schedule_notification(
                        background_tasks,
                        user_id=assignee_emp_id,
                        category="ticket_assigned",
                        title=f"Ticket #{issue_id} reassigned to you",
                        message=f"{updater_name} reassigned a ticket to you: {issue_attrs.get('subject', '')}",
                        priority="normal",
                        actor_id=actor_emp_id,
                        actor_name=updater_name,
                        link=f"/api/redmine/issues/{issue_id}",
                        extra={
                            "issue_id": str(issue_id),
                            "project_id": str(issue_attrs.get("project_id", "")),
                        },
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to schedule ticket_assigned (update) notification: {e}"
                )

        return {"status": "success", "message": "Issue updated successfully", "id": issue_id}
    except httpx.HTTPStatusError as e:
        logger.error("Ticket update failed: status=%s body=%s", e.response.status_code, e.response.text)
        code, message = _user_error(e, "edit this ticket")
        raise HTTPException(status_code=code, detail=message)
    except Exception as e:
        logger.exception("Ticket update failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't update the ticket right now. Please try again or contact your administrator.",
        )
