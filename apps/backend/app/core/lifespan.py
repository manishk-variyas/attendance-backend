import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import init_db
from app.services.attendance_background_worker import attendance_sweep_loop


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Backend API...")
    init_db()
    
    # Start background attendance sweeper (runs every 15 minutes)
    sweep_task = asyncio.create_task(attendance_sweep_loop(interval_seconds=900))
    
    yield
    
    logger.info("Shutting down Backend API...")
    sweep_task.cancel()
    try:
        await sweep_task
    except asyncio.CancelledError:
        logger.info("[Background Sweeper] Attendance background task shutdown cleanly.")
