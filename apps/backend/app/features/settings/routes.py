from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user, require_admin
from PIL import Image, UnidentifiedImageError
from app.utils.storage import storage_service
from app.services.database.system_setting_service import SystemSettingService
from app.features.redmine.constants import REDMINE_TO_IANA_TZ
from datetime import datetime, timezone, date, time
import hashlib
import io
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

ASSETS_BUCKET = settings.MINIO_ASSETS_BUCKET
LOGO_OBJECT_NAME = "logo"
BACKGROUND_OBJECT_NAME = "background"

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _process_image_upload(upload: UploadFile, max_dims: tuple[int, int]) -> tuple[bytes, str]:
    """Validate, sanitize and resize an uploaded image. Returns (jpeg_bytes, content_type)."""
    if not upload.content_type or not upload.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed.")
    if upload.size is not None and upload.size > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File size must not exceed 5MB.")
    content = upload.file.read()
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File size must not exceed 5MB.")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="Invalid or corrupted image file.")
    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=400, detail="Image dimensions too large.")
    img.thumbnail(max_dims, Image.LANCZOS)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue(), "image/jpeg"

@router.get("")
async def get_settings(db: Session = Depends(get_db)):
    svc = SystemSettingService(db)
    doc = svc.fetch()
    if not doc:
        return {"id": "company", "company_name": "", "logo_url": "", "background_url": ""}
    version = int(doc.updated_at.timestamp()) if doc.updated_at else 0
    return {
        "id": "company",
        "company_name": doc.company_name,
        "logo_url": f"/api/settings/logo?v={version}",
        "background_url": f"/api/settings/background?v={version}",
        "default_shift_start_time": doc.default_shift_start_time.isoformat() if doc.default_shift_start_time else None,
        "default_shift_end_time": doc.default_shift_end_time.isoformat() if doc.default_shift_end_time else None,
        "default_timezone": doc.default_timezone,
        "grace_minutes": doc.grace_minutes,
        "checkout_reminder_grace_hours": doc.checkout_reminder_grace_hours,
        "auto_checkout_enabled": doc.auto_checkout_enabled,
        "auto_checkout_cutoff_time": doc.auto_checkout_cutoff_time.isoformat() if doc.auto_checkout_cutoff_time else "22:00",
        "incorporation_date": doc.incorporation_date.isoformat() if doc.incorporation_date else "2016-06-09",
        "updated_at": doc.updated_at,
    }


@router.get("/timezones")
async def get_timezones(
    current_user: dict = Depends(get_current_user),
):
    return {"timezones": sorted(set(REDMINE_TO_IANA_TZ.values()))}


async def _cached_image_response(request: Request, object_name: str, not_found_detail: str) -> Response:
    """Serve an asset from MinIO with ETag/304 support and immutable caching. No DB involved."""
    try:
        content, content_type = await storage_service.get_file(object_name, ASSETS_BUCKET)
    except Exception as e:
        logger.error(f"Error reading {object_name} from MinIO: {e}")
        raise HTTPException(status_code=404, detail=not_found_detail)
    etag = f'"{hashlib.sha1(content).hexdigest()}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304)
    response = Response(content=content, media_type=content_type)
    response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    response.headers["ETag"] = etag
    return response


@router.get("/logo")
async def get_logo(request: Request):
    return await _cached_image_response(request, LOGO_OBJECT_NAME, "Logo not found.")


@router.get("/background")
async def get_background(request: Request):
    return await _cached_image_response(request, BACKGROUND_OBJECT_NAME, "Background image not found.")


@router.put("", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def update_settings(
    request: Request,
    _: None = Depends(require_admin),
    company_name: str = Form(...),
    logo: UploadFile = File(None),
    background: UploadFile = File(None),
    default_shift_start_time: str = Form(None),
    default_shift_end_time: str = Form(None),
    default_timezone: str = Form(None),
    grace_minutes: int = Form(None),
    checkout_reminder_grace_hours: int = Form(None),
    auto_checkout_enabled: bool = Form(None),
    auto_checkout_cutoff_time: str = Form(None),
    incorporation_date: str = Form(None),
    db: Session = Depends(get_db),
):
    if len(company_name) > 255:
        raise HTTPException(status_code=400, detail="Company name must be at most 255 characters.")
    if grace_minutes is not None and (grace_minutes < 1 or grace_minutes > 120):
        raise HTTPException(status_code=400, detail="Grace minutes must be between 1 and 120.")
    if checkout_reminder_grace_hours is not None and (checkout_reminder_grace_hours < 1 or checkout_reminder_grace_hours > 24):
        raise HTTPException(status_code=400, detail="Checkout reminder grace hours must be between 1 and 24.")
    if auto_checkout_cutoff_time:
        try:
            time.fromisoformat(auto_checkout_cutoff_time)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid auto_checkout_cutoff_time format. Use HH:MM.")
    if incorporation_date:
        try:
            inc = date.fromisoformat(incorporation_date)
            if inc > date.today():
                raise HTTPException(status_code=400, detail="Incorporation date cannot be in the future.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid incorporation_date format. Use YYYY-MM-DD.")

    svc = SystemSettingService(db)
    now = datetime.now(timezone.utc)

    logo_content_type = None
    if logo and logo.filename:
        content, logo_content_type = _process_image_upload(logo, (600, 200))
        await storage_service.upload_file(
            content, LOGO_OBJECT_NAME,
            bucket_name=ASSETS_BUCKET,
            content_type=logo_content_type,
        )

    background_content_type = None
    if background and background.filename:
        content, background_content_type = _process_image_upload(background, (1920, 1080))
        await storage_service.upload_file(
            content, BACKGROUND_OBJECT_NAME,
            bucket_name=ASSETS_BUCKET,
            content_type=background_content_type,
        )

    updated = svc.upsert(
        company_name=company_name,
        logo_content_type=logo_content_type,
        background_content_type=background_content_type,
        default_shift_start_time=default_shift_start_time,
        default_shift_end_time=default_shift_end_time,
        default_timezone=REDMINE_TO_IANA_TZ.get(default_timezone, default_timezone) if default_timezone else default_timezone,
        grace_minutes=grace_minutes,
        checkout_reminder_grace_hours=checkout_reminder_grace_hours,
        auto_checkout_enabled=auto_checkout_enabled if auto_checkout_enabled is not None else None,
        auto_checkout_cutoff_time=auto_checkout_cutoff_time,
        incorporation_date=incorporation_date,
    )
    settings = svc.fetch()
    return settings.to_dict() if settings else {"id": "company", "company_name": company_name}
