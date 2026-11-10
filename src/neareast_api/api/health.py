"""Operational checks, separate from versioned business endpoints."""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from neareast_api.core.database import database_is_ready

router = APIRouter(prefix="/health", tags=["Health"])


class LiveStatus(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyStatus(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unavailable"]


@router.get("/live", response_model=LiveStatus, summary="Check API process")
def liveness() -> LiveStatus:
    return LiveStatus()


@router.get(
    "/ready",
    response_model=ReadyStatus,
    summary="Check API and PostgreSQL connectivity",
    responses={503: {"model": ReadyStatus, "description": "Database unavailable"}},
)
def readiness(request: Request, response: Response) -> ReadyStatus:
    # A synchronous handler runs blocking database I/O in FastAPI's thread pool.
    if database_is_ready(request.app.state.settings):
        return ReadyStatus(status="ok", database="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyStatus(status="error", database="unavailable")
