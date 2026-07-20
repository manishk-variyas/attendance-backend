from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy import text
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
        "category": loc.category,
        "name": loc.name,
        "address": loc.address,
        "city": loc.city,
        "country": loc.country,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "radius_meters": loc.radius_meters,
        "parent_location_id": str(loc.parent_location_id) if loc.parent_location_id else None,
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
        category=payload.category,
        name=payload.name,
        address=payload.address,
        city=payload.city,
        country=payload.country or "",
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_meters=payload.radius_meters,
        parent_location_id=payload.parent_location_id,
        created_by=current_user.get("email"),
    )
    db.execute(text("UPDATE office_locations SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) WHERE id = :id"), {"id": str(loc.id)})
    db.commit()
    return _to_response(loc)


@router.get("")
async def list_office_locations(
    category: str = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if category and len(category) > 50:
        raise HTTPException(status_code=400, detail="Category must be 50 characters or less.")

    svc = OfficeLocationService(db)
    if category:
        locations = db.query(OfficeLocation).filter(OfficeLocation.category == category).order_by(OfficeLocation.updated_at.desc()).all()
    else:
        locations = svc.fetch_all(OfficeLocation, order_by=OfficeLocation.updated_at.desc())
    return [_to_response(l) for l in locations]


@router.get("/{location_id}", response_model=OfficeLocationResponse)
async def get_office_location(
    location_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
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
    update_data["updated_at"] = datetime.now(timezone.utc)

    if "parent_location_id" in update_data:
        new_parent = update_data["parent_location_id"]
        if new_parent and new_parent == location_id:
            raise HTTPException(status_code=400, detail="A location cannot be its own parent.")
        if new_parent:
            current = new_parent
            visited = set()
            while current:
                if current == location_id:
                    raise HTTPException(status_code=400, detail="Circular parent chain detected.")
                if current in visited:
                    raise HTTPException(status_code=400, detail="Circular parent chain detected.")
                visited.add(current)
                parent_loc = svc.fetch_one(OfficeLocation, id=current)
                current = str(parent_loc.parent_location_id) if parent_loc and parent_loc.parent_location_id else None

    loc = svc.update(OfficeLocation, location_id, **update_data)

    # Sync PostGIS geom whenever lat/lng change
    if "latitude" in update_data or "longitude" in update_data:
        db.execute(text("""
            UPDATE office_locations
            SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
            WHERE id = :id
        """), {"id": location_id})
        db.commit()

    return _to_response(loc)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_office_location(
    location_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    svc = OfficeLocationService(db)
    child = db.query(OfficeLocation).filter(OfficeLocation.parent_location_id == location_id).first()
    if child:
        raise HTTPException(status_code=400, detail="Cannot delete — this location has sub-locations. Remove them first.")
    deleted = svc.delete(OfficeLocation, location_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Office location not found")
