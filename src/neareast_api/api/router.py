"""Explicit registration point for future feature routers."""

from fastapi import APIRouter


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    # Register feature routers here with router.include_router(...).
    # No business endpoints or automatic module discovery in the initial commit.
    return router
