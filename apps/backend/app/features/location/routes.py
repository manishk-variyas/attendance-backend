from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.features.location.schemas.location import LocationSaveRequest, LocationResponse
from app.features.auth.dependencies import get_current_user
from app.services.database.location_service import LocationService

router = APIRouter()

@router.post("", response_model=LocationResponse)
async def save_location(
    payload: LocationSaveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != payload.email and "admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own location."
        )

    svc = LocationService(db)
    loc = svc.upsert(email=payload.email, latitude=payload.latitude, longitude=payload.longitude)
    return {
        "email": loc.email,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "updated_at": loc.updated_at,
    }

@router.get("/{email}", response_model=LocationResponse)
async def get_location(
    email: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != email and "admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own location."
        )
    svc = LocationService(db)
    loc = svc.fetch_by_email(email)
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Location not found for email: {email}"
        )
    return {
        "email": loc.email,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "updated_at": loc.updated_at,
    }
