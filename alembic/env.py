"""Use the same validated settings and registered models as the application."""

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.util import CommandError
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

from neareast_api.core.config import Settings
from neareast_api.db.metadata import metadata
from neareast_api.db.url import build_database_url

config = context.config

if config.config_file_name is not None:
    config_path = Path(config.config_file_name).resolve()
    fileConfig(config_path, disable_existing_loggers=False)
    # Explicit project file only; Compose environment variables take precedence.
    load_dotenv(config_path.parent / ".env", override=False, encoding="utf-8")

target_metadata = metadata

# The CLI must not interpret the initial empty registry as "drop all tables".
if getattr(config.cmd_opts, "autogenerate", False) and not target_metadata.tables:
    raise CommandError(
        "No models are registered. Add a model and import it in "
        "neareast_api.db.metadata before using --autogenerate."
    )

settings = Settings.from_env()
database_url = build_database_url(settings)


def run_migrations_offline() -> None:
    """Produce SQL without connecting to PostgreSQL."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Use a short-lived migration connection, separate from HTTP requests."""
    if not settings.db_password.get_secret_value():
        raise CommandError("Set NEAREAST_DB_PASSWORD in the environment or project .env.")

    engine = create_engine(
        database_url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
