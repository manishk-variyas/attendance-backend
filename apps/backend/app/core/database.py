"""
Database setup for SQLAlchemy ORM.

This module configures the connection to PostgreSQL using SQLAlchemy.
It provides:
- engine: The database engine that manages connections
- SessionLocal: A session factory for creating database sessions
- init_db(): Creates all tables defined in models
- get_db(): FastAPI dependency that provides a database session

Note: Currently the database is mainly used by Keycloak (user data).
This backend stores sessions in Redis, not PostgreSQL.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings
from app.models.base import Base


# Create the database engine with connection pooling
# pool_pre_ping=True checks connections are alive before using them
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# Create a session factory - each session is a new database transaction
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all database tables based on SQLAlchemy models."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """
    FastAPI dependency that provides a database session.
    
    How it works:
    1. Creates a new database session
    2. Yields it to the endpoint function
    3. Closes the session after the request is done (even if there's an error)
    
    Usage:
        @app.get("/users")
        def get_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
