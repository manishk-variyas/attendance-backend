import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import init_db


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Backend API...")
    init_db()
    # Note: The attendance background sweeper (auto-checkout + missed check-in
    # reminders) is NOT started here. It runs as a standalone cron job
    # (scripts/attendance_sweeper.py) so it executes exactly once per interval
    # regardless of how many gunicorn workers are running.

    yield

    logger.info("Shutting down Backend API...")
