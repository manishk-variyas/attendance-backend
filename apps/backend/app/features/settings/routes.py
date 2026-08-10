from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_admin
from app.utils.storage import storage_service
from app.services.database.system_setting_service import SystemSettingService
from app.features.redmine.constants import REDMINE_TO_IANA_TZ
from datetime import datetime, timezone
import io
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
        "checkout_reminder_grace_hours": doc.checkout_reminder_grace_hours,
        "auto_checkout_enabled": doc.auto_checkout_enabled,
        "updated_at": doc.updated_at,
    }


@router.get("/timezones")
async def get_timezones(
    current_user: dict = Depends(get_current_user),
):
    return {"timezones": sorted(set(REDMINE_TO_IANA_TZ.values()))}


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
    response = Response(content=content, media_type=doc.logo_content_type)
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@router.put("", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def update_settings(
    request: Request,
    company_name: str = Form(...),
    logo: UploadFile = File(None),
    default_shift_start_time: str = Form(None),
    default_shift_end_time: str = Form(None),
    default_timezone: str = Form(None),
    grace_minutes: int = Form(None),
    checkout_reminder_grace_hours: int = Form(None),
    auto_checkout_enabled: bool = Form(None),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    if len(company_name) > 255:
        raise HTTPException(status_code=400, detail="Company name must be at most 255 characters.")
    if grace_minutes is not None and (grace_minutes < 1 or grace_minutes > 120):
        raise HTTPException(status_code=400, detail="Grace minutes must be between 1 and 120.")
    if checkout_reminder_grace_hours is not None and (checkout_reminder_grace_hours < 1 or checkout_reminder_grace_hours > 24):
        raise HTTPException(status_code=400, detail="Checkout reminder grace hours must be between 1 and 24.")

    svc = SystemSettingService(db)
    now = datetime.now(timezone.utc)

    logo_content_type = None
    if logo and logo.filename:
        if not logo.content_type or not logo.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Only image files are allowed.")
        content = await logo.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size must not exceed 5MB.")

        # Resize to max 600x200 (logo proportions), preserve aspect ratio, optimize JPEG
        from PIL import Image
        img = Image.open(io.BytesIO(content))
        img.thumbnail((600, 200), Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        content = out.getvalue()
        logo_content_type = "image/jpeg"

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
        checkout_reminder_grace_hours=checkout_reminder_grace_hours,
        auto_checkout_enabled=auto_checkout_enabled if auto_checkout_enabled is not None else None,
    )
    settings = svc.fetch()
    return settings.to_dict() if settings else {"id": "company", "company_name": company_name}
