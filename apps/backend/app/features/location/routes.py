from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone
from app.core.mongodb import get_mongodb
from app.features.location.schemas.location import LocationSaveRequest, LocationResponse
from app.features.auth.dependencies import get_current_user

router = APIRouter()

@router.post("", response_model=LocationResponse)
async def save_location(
    payload: LocationSaveRequest,
    db = Depends(get_mongodb),
    current_user: dict = Depends(get_current_user)
):
    """
    Save or update user location metadata in MongoDB.
    Requires a valid session.
    """
    # Security: Ensure the user is updating their own location or is an admin
    if current_user["email"] != payload.email and "admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own location."
        )

    location_data = {
        "email": payload.email,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "updated_at": datetime.now(timezone.utc)
    }
    
    # Update if exists, else insert
    await db.locations.update_one(
        {"email": payload.email},
        {"$set": location_data},
        upsert=True
    )
    
    return location_data

@router.get("/{email}", response_model=LocationResponse)
async def get_location(
    email: str,
    db = Depends(get_mongodb),
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieve user location from MongoDB.
    Requires a valid session.
    """
    # Security: Ensure the user is viewing their own location or is an admin
    if current_user["email"] != email and "admin" not in current_user.get("roles", []):
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own location."
        )
    location = await db.locations.find_one({"email": email})
    
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Location not found for email: {email}"
        )
        
    return location
