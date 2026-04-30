"""
Application lifespan management.

This module defines the startup and shutdown logic for the FastAPI app.
Using FastAPI's lifespan context manager (newer approach than startup/shutdown events).

What happens on startup:
1. Initialize the database (create tables if needed)
2. Ready to accept requests

What happens on shutdown:
1. Close the Redis connection gracefully
2. App stops accepting requests
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import init_db
from app.services.session import close_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager - handles startup and shutdown.
    
    How it works:
    1. Code before 'yield' runs on startup
    2. 'yield' is where the app runs and handles requests
    3. Code after 'yield' runs on shutdown
    """
    logger.info("Starting up Backend API...")
    # Startup: Initialize database tables
    init_db()
    
    # Hand over control to the app - it's now ready to handle requests
    yield
    
    # Shutdown: Clean up resources
    logger.info("Shutting down Backend API...")
    await close_redis()
