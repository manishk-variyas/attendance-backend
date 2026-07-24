from fastapi import Request
from fastapi.responses import JSONResponse
import logging
from urllib.parse import urlparse
from app.core.config import settings

logger = logging.getLogger(__name__)

async def origin_validation_middleware(request: Request, call_next):
    # We only care about state-changing methods
    if request.method not in ["POST", "PUT", "DELETE", "PATCH"]:
        return await call_next(request)

    # Ensure origins are a list (Pydantic can sometimes load .env arrays as a literal string)
    allowed_origins = settings.CORS_ORIGINS
    if isinstance(allowed_origins, str):
        import json
        try:
            allowed_origins = json.loads(allowed_origins)
        except Exception:
            allowed_origins = [x.strip() for x in allowed_origins.strip("[]").split(",")]
            
    # Normalize allowed origins
    trusted_origins = {origin.strip('"\'').rstrip('/') for origin in allowed_origins}

    # 1. Check Origin Header first
    origin = request.headers.get("origin")
    if origin:
        if origin not in trusted_origins:
            logger.warning(f"CSRF Blocked: Invalid Origin header '{origin}'")
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        return await call_next(request)

    # 2. Fallback to Referer Header if Origin is missing
    referer = request.headers.get("referer")
    if referer:
        parsed_referer = urlparse(referer)
        base_referer = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
        
        if base_referer not in trusted_origins:
            logger.warning(f"CSRF Blocked: Invalid Referer header '{base_referer}'")
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        return await call_next(request)

    # 3. If BOTH are missing, block the request
    logger.warning("CSRF Blocked: Missing both Origin and Referer headers")
    return JSONResponse(status_code=403, content={"detail": "Forbidden"})
