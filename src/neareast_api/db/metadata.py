"""Explicit model registration used by Alembic."""

from neareast_api.db.base import Base

# Import each feature's models here after implementing them, for example:
# from neareast_api.modules.feature import models as feature_models
# Every model must inherit the shared Base; this file defines no tables itself.

metadata = Base.metadata
