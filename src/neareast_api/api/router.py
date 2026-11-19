"""Explicit registration point for future feature routers."""

from fastapi import APIRouter

from neareast_api.modules.chat.router import router as chat_router


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    # Register feature routers explicitly; no automatic module discovery.
    router.include_router(chat_router)
    return router
