from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request, status as fastapi_status
from fastapi.responses import StreamingResponse
from typing import List, Optional
import uuid
import os
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.features.recordings.schemas.recording import RecordingResponse, MarkPlayedRequest, DeleteRecordingRequest
from app.utils.storage import storage_service
from app.features.redmine.service import redmine_service
from app.features.auth.dependencies import get_current_user
from app.services.database.recording_service import RecordingService
from app.models.recording import Recording
from app.models.employee_master import EmployeeMaster
from sqlalchemy import text

router = APIRouter()


def _enrich_recordings(db: Session, recordings: list) -> list:
    if not recordings:
        return []
    emails = {r.user_email for r in recordings if r.user_email}
    ticket_ids = {r.ticket_id for r in recordings if r.ticket_id}

    emp_map = {}
    if emails:
        emps = db.query(EmployeeMaster).filter(EmployeeMaster.user_email.in_(emails)).all()
        emp_map = {e.user_email: e for e in emps}

    issue_map = {}
    if ticket_ids:
        rows = db.execute(
            text("SELECT id, subject FROM redmine.issues WHERE id = ANY(:ids)"),
            {"ids": list(ticket_ids)},
        ).fetchall()
        issue_map = {r[0]: r[1] for r in rows}

    result = []
    for r in recordings:
        emp = emp_map.get(r.user_email)
        result.append({
            "id": str(r.id),
            "email": r.user_email,
            "userName": f"{emp.first_name} {emp.last_name}".strip() if emp else None,
            "userDesignation": emp.designation if emp else None,
            "ticket_id": str(r.ticket_id) if r.ticket_id else None,
            "issue_subject": issue_map.get(r.ticket_id),
            "project": r.project,
            "priority": r.priority,
            "status": r.status,
            "filename": r.filename,
            "recording_url": r.recording_url,
            "is_played": r.is_played,
            "created_at": r.created_at,
        })
    return result


ALLOWED_AUDIO_TYPES = [
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/webm",
    "audio/aac",
    "audio/mp4",
]


@router.get("/recordings/{recording_id}/play")
async def play_recording(
    recording_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = RecordingService(db)
    recording = svc.fetch_one(Recording, id=recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    if current_user["email"] != recording.user_email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=fastapi_status.HTTP_403_FORBIDDEN,
            detail="You can only access your own recordings.",
        )

    recording_url = recording.recording_url or ""
    if "/recordings/" not in recording_url:
        raise HTTPException(status_code=500, detail="Invalid recording URL stored")
    object_name = recording_url.split("/recordings/", 1)[1]

    try:
        file_content, content_type = await storage_service.get_file(object_name)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read recording from storage: {str(e)}",
        )

    return StreamingResponse(
        iter([file_content]),
        media_type=content_type or "audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="{recording.filename}"'},
    )


@router.post("/upload", response_model=RecordingResponse)
@limiter.limit("2/minute")
async def upload_recording(
    request: Request,
    email: str = Form(...),
    ticketId: Optional[str] = Form(None),
    project: Optional[str] = Form(None),
    priority: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=fastapi_status.HTTP_403_FORBIDDEN,
            detail="You can only upload recordings for your own account."
        )

    if audio.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=fastapi_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type: {audio.content_type}. Only audio files are allowed."
        )

    ticket_id_int = None
    if ticketId:
        try:
            issue = await redmine_service.get_issue_by_id(int(ticketId))
            if not issue:
                raise HTTPException(
                    status_code=fastapi_status.HTTP_404_NOT_FOUND,
                    detail=f"Redmine Ticket ID {ticketId} not found."
                )
            project = issue["project"]["name"]
            priority = issue["priority"]["name"]
            status = issue["status"]["name"]
            ticket_id_int = int(ticketId)
        except ValueError:
             raise HTTPException(status_code=fastapi_status.HTTP_400_BAD_REQUEST, detail="Invalid Ticket ID format. Must be numeric.")

    ext = os.path.splitext(audio.filename)[1] or ".mp3"
    now = datetime.now(timezone.utc)
    date_folder = now.strftime("%Y-%m-%d")
    time_prefix = now.strftime("%H%M%S")
    username = current_user.get("username", email)
    short_uuid = uuid.uuid4().hex[:8]
    object_name = f"{username}/{date_folder}/{time_prefix}_{short_uuid}{ext}"
    content = await audio.read()

    try:
        recording_url = await storage_service.upload_file(content, object_name)
    except Exception as e:
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload to storage: {str(e)}"
        )

    svc = RecordingService(db)
    keycloak_user_id = current_user.get("sub", "")
    rec = svc.create(Recording,
        keycloak_user_id=keycloak_user_id,
        user_email=email,
        ticket_id=ticket_id_int,
        project=project,
        priority=priority,
        status=status,
        filename=audio.filename,
        recording_url=recording_url,
    )

    return {
        "id": str(rec.id),
        "email": email,
        "ticket_id": ticketId,
        "project": project,
        "priority": priority,
        "status": status,
        "filename": audio.filename,
        "recording_url": recording_url,
        "is_played": False,
        "created_at": rec.created_at,
    }


@router.get("/my-recordings/{email}", response_model=List[RecordingResponse])
async def get_user_recordings(
    email: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=fastapi_status.HTTP_403_FORBIDDEN,
            detail="You can only view your own recordings."
        )

    svc = RecordingService(db)
    recordings = svc.fetch_by_email(email)
    return _enrich_recordings(db, recordings)


@router.get("/all-recordings", response_model=List[RecordingResponse])
async def get_all_recordings(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if "Admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=fastapi_status.HTTP_403_FORBIDDEN,
            detail="Admin access required."
        )

    svc = RecordingService(db)
    recordings = svc.fetch_all()
    return _enrich_recordings(db, recordings)


@router.post("/mark-played")
async def mark_played(
    payload: MarkPlayedRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != payload.email and "Admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=fastapi_status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own recordings."
        )

    svc = RecordingService(db)
    rec = svc.fetch_by_url(payload.recordingUrl)
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    if "Admin" not in current_user.get("roles", []) and rec.user_email != payload.email:
        raise HTTPException(status_code=403, detail="You can only modify your own recordings.")

    svc.mark_played(rec.id)
    return {"message": "Recording marked as played"}


@router.delete("/delete-recording")
async def delete_recording(
    payload: DeleteRecordingRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["email"] != payload.email and "Admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=fastapi_status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own recordings."
        )

    svc = RecordingService(db)
    rec = svc.fetch_by_url(payload.recordingUrl)
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    if "Admin" not in current_user.get("roles", []) and rec.user_email != payload.email:
        raise HTTPException(status_code=403, detail="You can only delete your own recordings.")

    if "/recordings/" in payload.recordingUrl:
        full_object_name = payload.recordingUrl.split("/recordings/")[1]
        try:
            await storage_service.delete_file(full_object_name)
        except Exception as e:
            print(f"Delete error: {e}")

    svc.delete(Recording, rec.id)
    return {"message": "Recording deleted successfully"}
