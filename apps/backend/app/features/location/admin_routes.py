from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.features.location.schemas.office_location import (
    OfficeLocationCreate,
    OfficeLocationUpdate,
    OfficeLocationResponse,
)
from app.features.auth.dependencies import get_current_user, require_admin
from app.services.database.office_location_service import OfficeLocationService
from app.models.office_location import OfficeLocation

router = APIRouter(prefix="/admin/office-locations", tags=["admin-office-locations"])


def _to_response(loc: OfficeLocation) -> dict:
    return {
        "id": str(loc.id),
        "name": loc.name,
        "address": loc.address,
        "city": loc.city,
        "country": loc.country,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "radius_meters": loc.radius_meters,
        "created_by": loc.created_by,
        "created_at": loc.created_at,
        "updated_at": loc.updated_at,
    }


@router.post("", response_model=OfficeLocationResponse, status_code=status.HTTP_201_CREATED)
async def create_office_location(
    payload: OfficeLocationCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = OfficeLocationService(db)
    existing = svc.fetch_one(OfficeLocation, name=payload.name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Office location with name '{payload.name}' already exists",
        )
    loc = svc.create(
        OfficeLocation,
        name=payload.name,
        address=payload.address,
        city=payload.city,
        country=payload.country or "",
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_meters=payload.radius_meters,
        created_by=current_user.get("email"),
    )
    return _to_response(loc)


@router.get("", response_model=List[OfficeLocationResponse])
async def list_office_locations(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = OfficeLocationService(db)
    locations = svc.fetch_all(OfficeLocation)
    return [_to_response(l) for l in locations]


@router.get("/{location_id}", response_model=OfficeLocationResponse)
async def get_office_location(
    location_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = OfficeLocationService(db)
    loc = svc.fetch_one(OfficeLocation, id=location_id)
    if not loc:
        raise HTTPException(status_code=404, detail="Office location not found")
    return _to_response(loc)


@router.put("/{location_id}", response_model=OfficeLocationResponse)
async def update_office_location(
    location_id: str,
    payload: OfficeLocationUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = OfficeLocationService(db)
    existing = svc.fetch_one(OfficeLocation, id=location_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Office location not found")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        return _to_response(existing)

    loc = svc.update(OfficeLocation, location_id, **update_data)
    return _to_response(loc)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_office_location(
    location_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = OfficeLocationService(db)
    deleted = svc.delete(OfficeLocation, location_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Office location not found")
