from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class RecordingBase(BaseModel):
    email: str
    ticket_id: Optional[str] = None
    project: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None

class RecordingCreate(RecordingBase):
    pass

class RecordingUpdate(BaseModel):
    is_played: Optional[bool] = None

class RecordingResponse(RecordingBase):
    id: str
    filename: str
    recording_url: str
    is_played: bool
    created_at: datetime

    class Config:
        from_attributes = True

class MarkPlayedRequest(BaseModel):
    email: str
    recordingUrl: str

class DeleteRecordingRequest(BaseModel):
    email: str
    recordingUrl: str
