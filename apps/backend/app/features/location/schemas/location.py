from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class LocationSaveRequest(BaseModel):
    email: str
    latitude: float
    longitude: float


class LocationSaveResponse(BaseModel):
    email: str
    latitude: float
    longitude: float
    location_name: str
    office_name: Optional[str] = None
    office_lat: Optional[float] = None
    office_lng: Optional[float] = None
    distance_km: Optional[float] = None
    is_inside_office: Optional[bool] = None
    updated_at: datetime

    class Config:
        from_attributes = True


class LocationGetResponse(BaseModel):
    lat: float
    lng: float
    office_name: Optional[str] = None
    office_lat: Optional[float] = None
    office_lng: Optional[float] = None
    distance_km: Optional[float] = None
    is_inside_office: Optional[bool] = None
    updated_at: datetime
