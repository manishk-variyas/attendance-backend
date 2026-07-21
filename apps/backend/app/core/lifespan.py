import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import init_db


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Backend API...")
    init_db()
    
    yield
    
    logger.info("Shutting down Backend API...")
