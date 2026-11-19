"""Explicit registration point for future feature routers."""

from fastapi import APIRouter, Depends

from neareast_api.db.session import getSession
from neareast_api.modules.chat.router import router as chat_router
from neareast_api.modules.places.router import router as places_router
from neareast_api.modules.trains.router import router as trains_router


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    for publicRouter in (places_router, trains_router):
        router.include_router(publicRouter, dependencies=[Depends(getSession)])
    for protectedRouter in (
        chat_router,
    ):
        router.include_router(protectedRouter, dependencies=[Depends(getSession)])
    return router
