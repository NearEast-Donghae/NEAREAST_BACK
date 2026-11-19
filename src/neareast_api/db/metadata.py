"""Register entities before Alembic inspects the shared metadata."""

from neareast_api.db import entities as entities
from neareast_api.db.base import Base

metadata = Base.metadata
