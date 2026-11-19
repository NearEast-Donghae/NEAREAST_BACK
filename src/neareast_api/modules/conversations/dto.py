from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from neareast_api.modules.chat.schemas import ChatContext, ChatResponse


class ConversationCreateDto(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="새 대화", min_length=1, max_length=100)


class ConversationDto(ConversationCreateDto):
    model_config = ConfigDict(from_attributes=True)
    id: str
    createdAt: datetime
    updatedAt: datetime


class MessageCreateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    message: str = Field(min_length=1, max_length=1000)
    context: ChatContext = Field(default_factory=ChatContext)


class MessageDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    requestId: str
    role: str
    content: str
    status: str
    response: ChatResponse | None
    createdAt: datetime
