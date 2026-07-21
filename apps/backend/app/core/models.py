"""
Base model for SQLAlchemy ORM.

This module provides the base class for all database models.
All other models should inherit from the `Base` class defined here.

What's included:
- Base: The declarative base class for SQLAlchemy models
- Common imports that are typically needed for model definitions

To create a new model:
    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)
        ...

Note: This backend currently uses Redis for sessions, not PostgreSQL.
This base is here for future database models.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func


# Base class for all SQLAlchemy models
# Models that inherit from this will have their tables created by init_db()
Base = declarative_base()
