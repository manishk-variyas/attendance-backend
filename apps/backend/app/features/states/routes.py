"""States master-data endpoints.

- Read (list/get/options): any authenticated user (for dropdowns).
- Write (create/update/delete): Admin only.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_admin
from app.features.states.schemas import StateCreate, StateOption, StateResponse, StateUpdate
from app.features.states.service import StateService

router = APIRouter(prefix="/states", tags=["states"])


@router.get("", response_model=List[StateResponse])
def list_states(
    country: Optional[str] = Query(None, max_length=2, description="Filter by country code, e.g. IN"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, max_length=100, description="Search by name or code"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List states. Any authenticated user."""
    return [s.to_dict() for s in StateService(db).list(country=country, is_active=is_active, search=search)]


@router.get("/options", response_model=List[StateOption])
def list_state_options(
    country: Optional[str] = Query("IN", max_length=2, description="Country code, e.g. IN"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Active states only, light shape for dropdowns."""
    states = StateService(db).list(country=country, active_only=True)
    return [{"code": s.code, "name": s.name, "short_code": s.short_code} for s in states]


@router.get("/{code}", response_model=StateResponse)
def get_state(
    code: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get one state by its code."""
    return StateService(db).get_or_404(code).to_dict()


@router.post("", response_model=StateResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def create_state(
    request: Request,
    payload: StateCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    """Create a state. Admin only."""
    return StateService(db).create(payload.model_dump()).to_dict()


@router.put("/{code}", response_model=StateResponse)
@limiter.limit("20/minute")
def update_state(
    request: Request,
    code: str,
    payload: StateUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    """Update a state. Admin only."""
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update.")
    return StateService(db).update(code, data).to_dict()


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
def delete_state(
    request: Request,
    code: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    """Deactivate a state (soft delete — keeps history). Admin only."""
    StateService(db).deactivate(code)
    return None
