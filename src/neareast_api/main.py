"""Application composition; importing this module does not start any services."""

from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from neareast_api.api.health import router as health_router
from neareast_api.api.router import build_api_router
from neareast_api.core.config import Settings
from neareast_api.modules.chat.service import warm_up as warm_up_chat


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # File reads are blocking: run them off the event loop.
    await anyio.to_thread.run_sync(warm_up_chat, app.state.settings)
    yield


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
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    app.include_router(health_router)
    app.include_router(build_api_router())
    return app
