"""Construct a SQLAlchemy URL without string interpolation or ini secrets."""

from sqlalchemy import URL

from neareast_api.core.config import Settings


def build_database_url(settings: Settings) -> URL:
    return URL.create(
        drivername="postgresql+psycopg2",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )
