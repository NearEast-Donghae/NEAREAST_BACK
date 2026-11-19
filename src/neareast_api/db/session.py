from collections.abc import AsyncIterator
from contextvars import ContextVar

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from neareast_api.db.url import build_database_url

CONTENT_SESSION: ContextVar[Session | None] = ContextVar("contentSession", default=None)


async def getSession(request: Request) -> AsyncIterator[Session]:
    sessionFactory = getattr(request.app.state, "sessionFactory", None)
    if sessionFactory is None:
        engine = create_engine(
            build_database_url(request.app.state.settings),
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
        request.app.state.databaseEngine = engine
        sessionFactory = sessionmaker(engine, expire_on_commit=False)
        request.app.state.sessionFactory = sessionFactory
    session = sessionFactory()
    contextToken = CONTENT_SESSION.set(session)
    try:
        yield session
    finally:
        CONTENT_SESSION.reset(contextToken)
        session.close()


def contentSession() -> Session:
    session = CONTENT_SESSION.get()
    if session is None:
        raise RuntimeError("Content access requires a database session")
    return session
