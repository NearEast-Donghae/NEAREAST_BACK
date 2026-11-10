"""Application composition; importing this module does not start any services."""

from fastapi import FastAPI

from neareast_api.api.health import router as health_router
from neareast_api.api.router import build_api_router
from neareast_api.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an independent app after the process environment is configured."""
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(
        title="NearEast API",
        version="0.1.0",
        debug=False,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(build_api_router())
    return app
