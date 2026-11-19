from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from neareast_api.db.entities import ConversationEntity, MessageEntity, UserEntity, currentTime
from neareast_api.db.session import getSession
from neareast_api.modules.auth.service import requireUser
from neareast_api.modules.chat.router import _limiter
from neareast_api.modules.conversations.dto import (
    ConversationCreateDto,
    ConversationDto,
    MessageCreateDto,
    MessageDto,
)
from neareast_api.modules.conversations.service import ConversationService, ownedConversation

router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.post("", response_model=ConversationDto, status_code=201)
def createConversation(
    body: ConversationCreateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    conversation = ConversationEntity(userId=user.id, title=body.title)
    session.add(conversation)
    session.commit()
    return conversation


@router.get("", response_model=list[ConversationDto])
def listConversations(
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return list(
        session.scalars(
            select(ConversationEntity)
            .where(ConversationEntity.userId == user.id)
            .order_by(ConversationEntity.updatedAt.desc(), ConversationEntity.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/{conversationId}", response_model=ConversationDto)
def getConversation(
    conversationId: str,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    return ownedConversation(session, conversationId, user.id)


@router.patch("/{conversationId}", response_model=ConversationDto)
def renameConversation(
    conversationId: str,
    body: ConversationCreateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    conversation = ownedConversation(session, conversationId, user.id)
    conversation.title = body.title
    conversation.updatedAt = currentTime()
    session.commit()
    return conversation


@router.delete("/{conversationId}", status_code=204)
def deleteConversation(
    conversationId: str,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    session.delete(ownedConversation(session, conversationId, user.id))
    session.commit()


@router.get("/{conversationId}/messages", response_model=list[MessageDto])
def listMessages(
    conversationId: str,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    ownedConversation(session, conversationId, user.id)
    return list(
        session.scalars(
            select(MessageEntity)
            .where(MessageEntity.conversationId == conversationId)
            .order_by(MessageEntity.createdAt, MessageEntity.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.post("/{conversationId}/messages", response_model=MessageDto, status_code=201)
def sendMessage(
    conversationId: str,
    body: MessageCreateDto,
    request: Request,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    waitSeconds = _limiter(request).check(user.id)
    if waitSeconds:
        raise HTTPException(
            429, "Too many messages", headers={"Retry-After": str(int(waitSeconds) + 1)}
        )
    return ConversationService.sendMessage(
        session, conversationId, user.id, body, request.app.state.settings
    )
