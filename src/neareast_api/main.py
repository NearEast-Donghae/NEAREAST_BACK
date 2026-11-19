"""Application composition; importing this module does not start any services."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from neareast_api.api.health import router as health_router
from neareast_api.api.router import build_api_router
from neareast_api.core.config import Settings


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        yield
    finally:
        engine = getattr(app.state, "databaseEngine", None)
        if engine is not None:
            engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an independent app after the process environment is configured."""
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(
        title="NearEast API",
        version="0.1.0",
        debug=False,
        lifespan=_lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    @app.exception_handler(SQLAlchemyError)
    async def databaseError(request, exception):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.middleware("http")
    async def preventPrivateCaching(request, callNext):
        response = await callNext(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(health_router)
    app.include_router(build_api_router())
    return app
