"""Explicit registration point for future feature routers."""

from fastapi import APIRouter

from neareast_api.modules.chat.router import router as chat_router
from neareast_api.modules.places.router import router as places_router
from neareast_api.modules.plans.router import router as plans_router
from neareast_api.modules.trains.router import router as trains_router


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    # Register feature routers explicitly; no automatic module discovery.
    router.include_router(chat_router)
    router.include_router(places_router)
    router.include_router(plans_router)
    router.include_router(trains_router)
    return router
