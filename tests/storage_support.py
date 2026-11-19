from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from neareast_api.core.config import Settings
from neareast_api.db.metadata import metadata
from neareast_api.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def createStorageApp(settings=None, seed=True):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enableForeignKeys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    if seed:
        with engine.connect() as connection:
            connection.connection.driver_connection.executescript(
                (PROJECT_ROOT / "alembic/seed/0002_reference.sql").read_text("utf-8")
            )
            connection.commit()
    app = create_app(settings or Settings(chat_mock=True, llm_usage_log="NUL"))
    app.state.sessionFactory = sessionmaker(engine, expire_on_commit=False)
    app.state.databaseEngine = engine
    return app
