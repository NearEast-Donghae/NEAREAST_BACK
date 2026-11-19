"""HTTP boundary for chat: validation, settings lookup and error mapping only."""

from fastapi import APIRouter, HTTPException, Request, status

from neareast_api.modules.chat import service
from neareast_api.modules.chat.schemas import ChatRequest, ChatResponse, ChatUnavailable

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post(
    "/messages",
    response_model=ChatResponse,
    summary="Ask the assistant with the current map context",
    responses={503: {"model": ChatUnavailable, "description": "LLM or tool unavailable"}},
)
def post_message(body: ChatRequest, request: Request) -> ChatResponse:
    # Synchronous handler: FastAPI runs it in the thread pool, so the blocking
    # OpenAI SDK added in step 3 can be called here directly.
    try:
        return service.answer(body, request.app.state.settings)
    except service.ChatUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="chat unavailable"
        ) from None
