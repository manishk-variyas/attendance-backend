from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_admin
from app.features.leaves.schemas.leaves import LeaveTypeCreate, LeaveTypeUpdate, LeaveTypeResponse
from app.models.leave_type import LeaveTypeConfig
from app.services.database.base_service import BaseService

router = APIRouter(prefix="/admin/leave-types", tags=["admin-leave-types"])


def _to_response(lt: LeaveTypeConfig) -> dict:
    return lt.to_dict()


@router.get("")
async def list_leave_types(
    search: Optional[str] = Query(None, description="Search by code or name"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    sort_by: str = Query("name", description="name, code, created_at"),
    sort_dir: str = Query("asc", description="asc or desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = db.query(LeaveTypeConfig)
    if is_active is not None:
        query = query.filter(LeaveTypeConfig.is_active == is_active)
    if search:
        term = (search or "").strip()[:100]
        if term:
            query = query.filter(or_(
                LeaveTypeConfig.code.ilike(f"%{term}%"),
                LeaveTypeConfig.name.ilike(f"%{term}%"),
            ))

    allowed_sort = {"name", "code", "created_at"}
    sort_by = sort_by if sort_by in allowed_sort else "name"
    sort_col = getattr(LeaveTypeConfig, sort_by)
    query = query.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    offset = (page - 1) * page_size
    items = query.offset(offset).limit(page_size).all()

    return {
        "records": [_to_response(i) for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.post("", response_model=LeaveTypeResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_leave_type(
    request: Request,
    payload: LeaveTypeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[LeaveTypeConfig](db)

    existing = svc.fetch_one(LeaveTypeConfig, code=payload.code)
    if existing:
        raise HTTPException(status_code=409, detail=f"Leave type code '{payload.code}' already exists.")
    existing = svc.fetch_one(LeaveTypeConfig, name=payload.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Leave type name '{payload.name}' already exists.")

    lt = svc.create(
        LeaveTypeConfig,
        code=payload.code,
        name=payload.name,
        is_paid=payload.is_paid,
        carry_forward_allowed=payload.carry_forward_allowed,
        carry_forward_cap=payload.carry_forward_cap,
    )
    return _to_response(lt)


@router.put("/{leave_type_id}", response_model=LeaveTypeResponse)
@limiter.limit("10/minute")
async def update_leave_type(
    request: Request,
    leave_type_id: str,
    payload: LeaveTypeUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[LeaveTypeConfig](db)
    lt = svc.fetch_one(LeaveTypeConfig, id=leave_type_id)
    if not lt:
        raise HTTPException(status_code=404, detail="Leave type not found.")

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    if "code" in data and data["code"] != lt.code:
        dup = svc.fetch_one(LeaveTypeConfig, code=data["code"])
        if dup and str(dup.id) != leave_type_id:
            raise HTTPException(status_code=409, detail=f"Leave type code '{data['code']}' already exists.")
    if "name" in data and data["name"] != lt.name:
        dup = svc.fetch_one(LeaveTypeConfig, name=data["name"])
        if dup and str(dup.id) != leave_type_id:
            raise HTTPException(status_code=409, detail=f"Leave type name '{data['name']}' already exists.")

    data["updated_at"] = datetime.now(timezone.utc)
    updated = svc.update(LeaveTypeConfig, leave_type_id, **data)
    return _to_response(updated)


@router.delete("/{leave_type_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_leave_type(
    request: Request,
    leave_type_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = BaseService[LeaveTypeConfig](db)
    lt = svc.fetch_one(LeaveTypeConfig, id=leave_type_id)
    if not lt:
        raise HTTPException(status_code=404, detail="Leave type not found.")

    # Soft delete — keep the row, mark inactive (preserves history)
    svc.update(LeaveTypeConfig, leave_type_id, is_active=False, updated_at=datetime.now(timezone.utc))
    return None
