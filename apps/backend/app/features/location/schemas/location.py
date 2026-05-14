from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class LocationBase(BaseModel):
    email: str
    latitude: float
    longitude: float

class LocationSaveRequest(LocationBase):
    pass

class LocationResponse(LocationBase):
    updated_at: datetime

    class Config:
        from_attributes = True
