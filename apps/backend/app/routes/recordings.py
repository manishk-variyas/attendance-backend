from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status as fastapi_status

from typing import List, Optional
import uuid
import os
from datetime import datetime, timezone

from app.core.mongodb import get_mongodb
from app.schemas.recording import RecordingResponse, MarkPlayedRequest, DeleteRecordingRequest
from app.utils.storage import storage_service
from app.features.redmine.service import redmine_service

router = APIRouter()


ALLOWED_AUDIO_TYPES = [
    "audio/mpeg",    # .mp3
    "audio/wav",     # .wav
    "audio/x-wav",   # .wav
    "audio/ogg",     # .ogg
    "audio/webm",    # .webm
    "audio/aac",     # .aac
    "audio/mp4",     # .m4a
]

@router.post("/upload", response_model=RecordingResponse)
async def upload_recording(
    email: str = Form(...),
    ticketId: Optional[str] = Form(None),
    project: Optional[str] = Form(None),
    priority: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    audio: UploadFile = File(...),
    db = Depends(get_mongodb)
):
    # Validate MIME type
    if audio.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=fastapi_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type: {audio.content_type}. Only audio files are allowed."
        )

    # Redmine Enrichment: Fetch official data if ticketId is provided
    redmine_data = {}
    if ticketId:
        try:
            issue = await redmine_service.get_issue_by_id(int(ticketId))
            if not issue:
                raise HTTPException(
                    status_code=fastapi_status.HTTP_404_NOT_FOUND,
                    detail=f"Redmine Ticket ID {ticketId} not found. Cannot associate recording."
                )
            # Override provided fields with official Redmine data
            project = issue["project"]["name"]
            priority = issue["priority"]["name"]
            status = issue["status"]["name"]
            redmine_data = {
                "redmine_project_id": issue["project"]["id"],
                "redmine_status_id": issue["status"]["id"],
                "redmine_priority_id": issue["priority"]["id"],
                "subject": issue["subject"]
            }
        except ValueError:
             raise HTTPException(status_code=fastapi_status.HTTP_400_BAD_REQUEST, detail="Invalid Ticket ID format. Must be numeric.")

    # Create a unique filename


    ext = os.path.splitext(audio.filename)[1] or ".mp3"
    unique_filename = f"{uuid.uuid4()}{ext}"
    object_name = f"{email}/{unique_filename}"

    # Read file content
    content = await audio.read()
    
    # Upload to MinIO
    try:
        recording_url = await storage_service.upload_file(content, object_name)
    except Exception as e:
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload to storage: {str(e)}"
        )

    # Save metadata to MongoDB
    recording_data = {
        "email": email,
        "ticket_id": ticketId,
        "project": project,
        "priority": priority,
        "status": status,
        "filename": audio.filename,
        "recording_url": recording_url,
        "is_played": False,
        "created_at": datetime.now(timezone.utc),
        "redmine_details": redmine_data
    }
    
    result = await db.recordings.insert_one(recording_data)
    recording_data["id"] = str(result.inserted_id) # Schema expects 'id'
    
    return recording_data

@router.get("/my-recordings/{email}", response_model=List[RecordingResponse])
async def get_user_recordings(email: str, db = Depends(get_mongodb)):
    cursor = db.recordings.find({"email": email})
    recordings = []
    async for doc in cursor:
        doc["id"] = str(doc["_id"])
        recordings.append(doc)
    return recordings

@router.get("/all-recordings", response_model=List[RecordingResponse])
async def get_all_recordings(db = Depends(get_mongodb)):
    cursor = db.recordings.find({})
    recordings = []
    async for doc in cursor:
        doc["id"] = str(doc["_id"])
        recordings.append(doc)
    return recordings

@router.post("/mark-played")
async def mark_played(payload: MarkPlayedRequest, db = Depends(get_mongodb)):
    result = await db.recordings.update_one(
        {"email": payload.email, "recording_url": payload.recordingUrl},
        {"$set": {"is_played": True}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    return {"message": "Recording marked as played"}

@router.delete("/delete-recording")
async def delete_recording(payload: DeleteRecordingRequest, db = Depends(get_mongodb)):
    recording = await db.recordings.find_one({
        "email": payload.email,
        "recording_url": payload.recordingUrl
    })
    
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # Extract object name from URL
    try:
        object_name = payload.recordingUrl.split(f"/{payload.email}/")[1]
        full_object_name = f"{payload.email}/{object_name}"
        await storage_service.delete_file(full_object_name)
    except Exception as e:
        pass
        
    await db.recordings.delete_one({"_id": recording["_id"]})
    return {"message": "Recording deleted successfully"}
