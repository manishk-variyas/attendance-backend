from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import Response
from app.core.config import settings
from app.core.mongodb import get_mongodb
from app.features.auth.dependencies import require_admin
from app.utils.storage import storage_service
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

ASSETS_BUCKET = settings.MINIO_ASSETS_BUCKET
LOGO_OBJECT_NAME = "logo"
BACKEND_URL = settings.BACKEND_URL.rstrip("/")
LOGO_URL_PATH = f"http://95.216.39.97:8086/server/api/settings/logo"

@router.get("")
async def get_settings(db=Depends(get_mongodb)):
    """Get company settings. Public."""
    doc = await db.system_settings.find_one({"_id": "company"})
    if not doc:
        return {"id": "company", "company_name": "", "logo_url": ""}
    return {
        "id": "company",
        "company_name": doc.get("company_name", ""),
        "logo_url": LOGO_URL_PATH,
        "updated_at": doc.get("updated_at"),
    }


@router.get("/logo")
async def get_logo(db=Depends(get_mongodb)):
    """Serve the company logo image. Public."""
    doc = await db.system_settings.find_one({"_id": "company"})
    if not doc or not doc.get("logo_content_type"):
        raise HTTPException(status_code=404, detail="Logo not found.")

    content_type = doc["logo_content_type"]
    try:
        content, _ = await storage_service.get_file(LOGO_OBJECT_NAME, ASSETS_BUCKET)
    except Exception as e:
        logger.error(f"Error reading logo from MinIO: {e}")
        raise HTTPException(status_code=404, detail="Logo not found.")

    return Response(content=content, media_type=content_type)


@router.put("", status_code=status.HTTP_200_OK)
async def update_settings(
    company_name: str = Form(...),
    logo: UploadFile = File(None),
    db=Depends(get_mongodb),
    _: None = Depends(require_admin),
):
    """Update company settings (name + logo). Admin only."""
    now = datetime.now(timezone.utc)
    update = {"company_name": company_name, "updated_at": now}

    if logo and logo.filename:
        if not logo.content_type or not logo.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Only image files are allowed.")

        content = await logo.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size must not exceed 5MB.")

        content_type = logo.content_type
        await storage_service.upload_file(
            content, LOGO_OBJECT_NAME,
            bucket_name=ASSETS_BUCKET,
            content_type=content_type,
        )
        update["logo_content_type"] = content_type

    await db.system_settings.update_one(
        {"_id": "company"},
        {"$set": update},
        upsert=True,
    )

    return {"id": "company", "company_name": company_name, "logo_url": LOGO_URL_PATH, "updated_at": now}
