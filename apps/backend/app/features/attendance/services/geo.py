"""Geo/geofence helpers for attendance check-in/out location resolution."""
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.office_location import OfficeLocation


GEOFENCE_SQL = text("""
    SELECT ST_DWithin(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography,
        :radius
    )
""")

DISTANCE_SQL = text("""
    SELECT ROUND(ST_Distance(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography
    )::INT / 1000.0, 1) AS distance_km
""")


def find_office_at(db: Session, lat: float, lng: float):
    """Office whose geofence contains the point (nearest/smallest radius first)."""
    if lat is None or lng is None:
        return None
    row = db.execute(
        text("""
            SELECT id FROM office_locations
            WHERE geom IS NOT NULL
              AND ST_DWithin(
                    geom::geography,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    radius_meters)
            ORDER BY radius_meters ASC
            LIMIT 1
        """),
        {"lng": float(lng), "lat": float(lat)},
    ).fetchone()
    return row[0] if row else None


def determine_work_location(db: Session, user_lat: float, user_lng: float, location_id) -> str:
    if user_lat is None or user_lng is None:
        return "WFH"
    if not location_id:
        return "WFH"
    office = db.execute(select(OfficeLocation).where(OfficeLocation.id == location_id)).scalars().first()
    if not office:
        return "WFH"
    inside = db.execute(
        GEOFENCE_SQL,
        {"lng": user_lng, "lat": user_lat, "office_id": str(location_id), "radius": office.radius_meters},
    ).scalar()
    return "OFFICE" if inside else "OUT_OF_OFFICE"


def location_name(db: Session, lat: float, lng: float, location_id) -> str:
    if lat is None or lng is None:
        return None
    if not location_id:
        return f"{lat:.4f}, {lng:.4f}"
    office = db.execute(select(OfficeLocation).where(OfficeLocation.id == location_id)).scalars().first()
    if not office:
        return f"{lat:.4f}, {lng:.4f}"
    inside = db.execute(
        GEOFENCE_SQL,
        {"lng": lng, "lat": lat, "office_id": str(location_id), "radius": office.radius_meters},
    ).scalar()
    if inside:
        return f"{office.name}, {office.address}" if office.address else office.name
    try:
        dist = db.execute(
            DISTANCE_SQL,
            {"lng": lng, "lat": lat, "office_id": str(location_id)},
        ).scalar()
        if dist is not None:
            return f"{dist} km from {office.name}"
    except Exception:
        pass
    return f"{lat:.4f}, {lng:.4f}"
