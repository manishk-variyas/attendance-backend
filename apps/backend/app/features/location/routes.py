from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text
import httpx

from app.core.database import get_db
from app.features.location.schemas.location import LocationSaveRequest, LocationResponse
from app.features.auth.dependencies import get_current_user
from app.services.database.location_service import LocationService
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation

router = APIRouter()

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_GEO_CACHE: dict = {}
GEOFENCE_SQL = text("""
    SELECT
        ST_DWithin(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, o.geom::geography, o.radius_meters) AS inside,
        ROUND(ST_Distance(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, o.geom::geography))::INT AS distance_m
    FROM office_locations o
    WHERE o.id = :office_id
""")


async def _reverse_geocode(lat: float, lng: float) -> str:
    cache_key = (round(lat, 3), round(lng, 3))
    if cache_key in _GEO_CACHE:
        return _GEO_CACHE[cache_key]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                NOMINATIM_URL,
                params={"format": "json", "lat": lat, "lon": lng},
                headers={"User-Agent": "AttendanceApp/1.0"},
            )
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                name = data.get("display_name", "")[:500]
                if name:
                    _GEO_CACHE[cache_key] = name
                return name
    except Exception:
        pass
    return ""

@router.post("", response_model=LocationResponse)
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
    emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == payload.email).first()
    if emp and emp.location_id:
        geo = db.execute(
            GEOFENCE_SQL,
            {"lng": payload.longitude, "lat": payload.latitude, "office_id": str(emp.location_id)},
        ).first()
        if geo and geo.inside:
            office = db.query(OfficeLocation).filter(OfficeLocation.id == emp.location_id).first()
            loc_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
        else:
            loc_name = await _reverse_geocode(payload.latitude, payload.longitude)
    else:
        loc_name = await _reverse_geocode(payload.latitude, payload.longitude)
    loc = svc.upsert(email=payload.email, latitude=payload.latitude, longitude=payload.longitude, location_name=loc_name)
    return {
        "email": loc.email,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "location_name": loc.location_name,
        "updated_at": loc.updated_at,
    }

@router.get("/{email}")
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
        "email": loc.email,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "location_name": loc.location_name,
        "updated_at": loc.updated_at,
        "office_name": None,
        "distance_from_office": None,
        "is_inside_office": None,
    }

    emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == email).first()
    if emp and emp.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == emp.location_id).first()
        if office and office.geom is not None:
            result["office_name"] = office.name
            geo = db.execute(
                GEOFENCE_SQL,
                {"lng": loc.longitude, "lat": loc.latitude, "office_id": str(office.id)},
            ).first()
            if geo:
                result["is_inside_office"] = geo.inside
                result["distance_from_office"] = geo.distance_m

    return result
