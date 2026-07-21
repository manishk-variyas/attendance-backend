from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.features.location.schemas.location import LocationSaveRequest, LocationSaveResponse, LocationGetResponse
from app.features.auth.dependencies import get_current_user
from app.services.database.location_service import LocationService
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation

router = APIRouter()

GEOFENCE_SQL = text("""
    SELECT
        ST_DWithin(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, o.geom::geography, o.radius_meters) AS inside,
        ROUND(ST_Distance(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, o.geom::geography))::INT AS distance_m
    FROM office_locations o
    WHERE o.id = :office_id
""")

DISTANCE_KM_SQL = text("""
    SELECT ROUND((ST_Distance(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography
    ) / 1000.0)::numeric, 2)
""")


def _loc_name(db: Session, lat: float, lng: float, location_id) -> str:
    if not location_id:
        return f"{lat:.4f}, {lng:.4f}"
    office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
    if not office:
        return f"{lat:.4f}, {lng:.4f}"
    inside = db.execute(
        GEOFENCE_SQL,
        {"lng": lng, "lat": lat, "office_id": str(location_id)},
    ).first()
    if inside and inside.inside:
        return f"{office.name}, {office.address}" if office.address else office.name
    try:
        dist = db.execute(
            DISTANCE_KM_SQL,
            {"lng": lng, "lat": lat, "office_id": str(location_id)},
        ).scalar()
        if dist is not None:
            return f"{dist} km from {office.name}"
    except Exception:
        pass
    return f"{lat:.4f}, {lng:.4f}"

@router.post("", response_model=LocationSaveResponse)
async def save_location(
    payload: LocationSaveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != payload.email and "Admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own location."
        )

    svc = LocationService(db)
    email = payload.email
    emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == email).first()
    office = None
    geo = None
    if emp and emp.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == emp.location_id).first()
        if office and office.geom is not None:
            geo = db.execute(
                GEOFENCE_SQL,
                {"lng": payload.longitude, "lat": payload.latitude, "office_id": str(office.id)},
            ).first()
    if geo and geo.inside:
        loc_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
    else:
        loc_name = _loc_name(db, payload.latitude, payload.longitude, emp.location_id if emp else None)
    loc = svc.upsert(email=email, latitude=payload.latitude, longitude=payload.longitude, location_name=loc_name)
    result = {
        "email": loc.email,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "location_name": loc.location_name,
        "office_name": office.name if office else None,
        "office_lat": office.latitude if office else None,
        "office_lng": office.longitude if office else None,
        "distance_km": round(geo.distance_m / 1000.0, 2) if geo else None,
        "is_inside_office": geo.inside if geo else None,
        "updated_at": loc.updated_at,
    }
    return result

@router.get("/{email}", response_model=LocationGetResponse)
async def get_location(
    email: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
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

    result = {
        "lat": loc.latitude,
        "lng": loc.longitude,
        "office_name": None,
        "office_lat": None,
        "office_lng": None,
        "distance_km": None,
        "is_inside_office": None,
        "updated_at": loc.updated_at,
    }

    emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == email).first()
    if emp and emp.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == emp.location_id).first()
        if office and office.geom is not None:
            result["office_name"] = office.name
            result["office_lat"] = office.latitude
            result["office_lng"] = office.longitude
            geo = db.execute(
                GEOFENCE_SQL,
                {"lng": loc.longitude, "lat": loc.latitude, "office_id": str(office.id)},
            ).first()
            if geo:
                result["is_inside_office"] = geo.inside
                result["distance_km"] = round(geo.distance_m / 1000.0, 2)

    return result
