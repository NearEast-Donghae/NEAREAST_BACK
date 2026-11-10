"""A bounded connectivity probe; no schema, migrations or persistent connections."""

from contextlib import closing

import psycopg2

from neareast_api.core.config import Settings


def database_is_ready(settings: Settings) -> bool:
    if not settings.db_password.get_secret_value():
        return False
    try:
        with closing(
            psycopg2.connect(
                host=settings.db_host,
                port=settings.db_port,
                dbname=settings.db_name,
                user=settings.db_user,
                password=settings.db_password.get_secret_value(),
                connect_timeout=3,
                options="-c statement_timeout=3000",
            )
        ) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except psycopg2.Error:
        # Credentials and driver diagnostics must not appear in health responses.
        return False
