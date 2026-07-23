from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.features.auth.dependencies import require_admin
from app.utils.storage import storage_service
from app.services.database.system_setting_service import SystemSettingService
from app.features.redmine.constants import REDMINE_TO_IANA_TZ
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

ASSETS_BUCKET = settings.MINIO_ASSETS_BUCKET
LOGO_OBJECT_NAME = "logo"
LOGO_URL_PATH = "http://95.216.39.97:8086/server/api/settings/logo"

@router.get("")
async def get_settings(db: Session = Depends(get_db)):
    svc = SystemSettingService(db)
    doc = svc.fetch()
    if not doc:
        return {"id": "company", "company_name": "", "logo_url": ""}
    return {
        "id": "company",
        "company_name": doc.company_name,
        "logo_url": LOGO_URL_PATH,
        "default_shift_start_time": doc.default_shift_start_time.isoformat() if doc.default_shift_start_time else None,
        "default_shift_end_time": doc.default_shift_end_time.isoformat() if doc.default_shift_end_time else None,
        "default_timezone": doc.default_timezone,
        "grace_minutes": doc.grace_minutes,
        "updated_at": doc.updated_at,
    }


@router.get("/logo")
async def get_logo(db: Session = Depends(get_db)):
    svc = SystemSettingService(db)
    doc = svc.fetch()
    if not doc or not doc.logo_content_type:
        raise HTTPException(status_code=404, detail="Logo not found.")
    try:
        content, _ = await storage_service.get_file(LOGO_OBJECT_NAME, ASSETS_BUCKET)
    except Exception as e:
        logger.error(f"Error reading logo from MinIO: {e}")
        raise HTTPException(status_code=404, detail="Logo not found.")
    return Response(content=content, media_type=doc.logo_content_type)


@router.put("", status_code=status.HTTP_200_OK)
async def update_settings(
    company_name: str = Form(...),
    logo: UploadFile = File(None),
    default_shift_start_time: str = Form(None),
    default_shift_end_time: str = Form(None),
    default_timezone: str = Form(None),
    grace_minutes: int = Form(None),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    svc = SystemSettingService(db)
    now = datetime.now(timezone.utc)

    logo_content_type = None
    if logo and logo.filename:
        if not logo.content_type or not logo.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Only image files are allowed.")
        content = await logo.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size must not exceed 5MB.")
        logo_content_type = logo.content_type
        await storage_service.upload_file(
            content, LOGO_OBJECT_NAME,
            bucket_name=ASSETS_BUCKET,
            content_type=logo_content_type,
        )

    updated = svc.upsert(
        company_name=company_name,
        logo_content_type=logo_content_type,
        default_shift_start_time=default_shift_start_time,
        default_shift_end_time=default_shift_end_time,
        default_timezone=REDMINE_TO_IANA_TZ.get(default_timezone, default_timezone) if default_timezone else default_timezone,
        grace_minutes=grace_minutes,
    )
    settings = svc.fetch()
    return settings.to_dict() if settings else {"id": "company", "company_name": company_name}
