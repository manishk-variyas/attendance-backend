from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db
from app.features.auth.dependencies import get_current_user

router = APIRouter(prefix="/countries", tags=["countries"])


@router.get("")
async def list_countries(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = db.execute(text("SELECT code, name FROM countries ORDER BY name"))
    return [{"code": r[0], "name": r[1]} for r in result]
