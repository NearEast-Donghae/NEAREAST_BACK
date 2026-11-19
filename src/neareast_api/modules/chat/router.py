"""HTTP boundary for chat: validation, settings lookup and error mapping only."""

import math
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from neareast_api.db.entities import UserEntity
from neareast_api.db.session import getSession
from neareast_api.modules.auth.service import requireUser
from neareast_api.modules.chat import service
from neareast_api.modules.chat.limits import RateLimiter
from neareast_api.modules.chat.schemas import (
    ChatRequest,
    ChatResponse,
    ChatUnavailable,
    PersonaResponse,
)
from neareast_api.modules.saved.service import SavedService

router = APIRouter(prefix="/chat", tags=["Chat"])
_limiter_lock = threading.Lock()


def _limiter(request: Request) -> RateLimiter:
    """One limiter per app (not per process), created on first use."""
    state = request.app.state
    with _limiter_lock:
        if not hasattr(state, "chat_limiter"):
            state.chat_limiter = RateLimiter(state.settings.chat_rate_limit_per_minute)
        return state.chat_limiter


@router.get(
    "/persona",
    response_model=PersonaResponse,
    summary="Assistant name, greeting and loading texts for the chat UI",
)
def get_persona() -> PersonaResponse:
    return service.persona()


@router.post(
    "/messages",
    response_model=ChatResponse,
    summary="Ask the assistant with the current map context",
    responses={
        429: {"description": "Too many requests from this client; see Retry-After"},
        503: {"model": ChatUnavailable, "description": "Chat content unavailable"},
    },
)
def post_message(
    body: ChatRequest,
    request: Request,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
) -> ChatResponse:
    client = request.client.host if request.client else "unknown"
    wait = _limiter(request).check(client)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many requests",
            headers={"Retry-After": str(math.ceil(wait))},
        )
    # Synchronous handler: FastAPI runs it in the thread pool, so the blocking
    # OpenAI SDK added in step 3 can be called here directly.
    try:
        # What the user has saved counts as pins, whatever the request carries.
        saved = SavedService.selection(session, user.id)
        return service.answer(body, request.app.state.settings, saved)
    except service.ChatUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="chat unavailable"
        ) from None
