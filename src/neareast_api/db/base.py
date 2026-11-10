"""One declarative base for models across all business modules."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Do not add mandatory IDs or timestamps to every table here."""
