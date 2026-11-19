from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from neareast_api.core.config import Settings
from neareast_api.db.entities import ConversationEntity, MessageEntity, currentTime
from neareast_api.modules.chat import service as chatService
from neareast_api.modules.chat.schemas import ChatRequest, ChatTurn
from neareast_api.modules.conversations.dto import MessageCreateDto
from neareast_api.modules.saved.service import SavedService


def ownedConversation(session: Session, conversationId: str, userId: str) -> ConversationEntity:
    conversation = session.scalar(
        select(ConversationEntity).where(
            ConversationEntity.id == conversationId,
            ConversationEntity.userId == userId,
        )
    )
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    return conversation


class ConversationService:
    @staticmethod
    def sendMessage(
        session: Session,
        conversationId: str,
        userId: str,
        body: MessageCreateDto,
        settings: Settings,
    ) -> MessageEntity:
        conversation = ownedConversation(session, conversationId, userId)
        requestId = str(body.requestId)
        now = currentTime()
        claimId = str(uuid4())
        claim = session.execute(
            update(ConversationEntity)
            .where(
                ConversationEntity.id == conversationId,
                ConversationEntity.userId == userId,
                or_(ConversationEntity.busyUntil.is_(None), ConversationEntity.busyUntil < now),
            )
            .values(
                busyUntil=now + timedelta(seconds=settings.chat_deadline_seconds + 60),
                activeRequestId=claimId,
            )
        )
        if not claim.rowcount:
            session.rollback()
            raise HTTPException(409, "Another message is still being answered")

        # Re-read under the conversation lock so a completed simultaneous retry is reused.
        existingMessages = list(
            session.scalars(
                select(MessageEntity).where(
                    MessageEntity.conversationId == conversationId,
                    MessageEntity.requestId == requestId,
                )
            )
        )
        previousUserMessage = next(
            (message for message in existingMessages if message.role == "user"), None
        )
        if previousUserMessage and previousUserMessage.content != body.message:
            session.rollback()
            raise HTTPException(409, "requestId was already used for a different message")
        previousAnswer = next(
            (message for message in existingMessages if message.role == "assistant"), None
        )
        if previousAnswer:
            session.rollback()
            return previousAnswer

        session.execute(
            update(MessageEntity)
            .where(
                MessageEntity.conversationId == conversationId,
                MessageEntity.status == "pending",
            )
            .values(status="failed")
        )
        historyRows = list(
            session.scalars(
                select(MessageEntity)
                .where(
                    MessageEntity.conversationId == conversationId,
                    MessageEntity.status == "completed",
                )
                .order_by(MessageEntity.createdAt.desc(), MessageEntity.id.desc())
                .limit(10)
            )
        )
        userMessage = previousUserMessage or MessageEntity(
            conversationId=conversationId,
            requestId=requestId,
            role="user",
            content=body.message,
        )
        userMessage.status = "pending"
        session.add(userMessage)
        if conversation.title == "새 대화":
            conversation.title = body.message[:100]
        conversation.updatedAt = now
        session.commit()

        try:
            answer = chatService.answer(
                ChatRequest(
                    message=body.message,
                    context=body.context,
                    history=[
                        ChatTurn(role=row.role, content=row.content[:2000])
                        for row in reversed(historyRows)
                    ],
                ),
                settings,
                SavedService.selection(session, userId),
            )
        except chatService.ChatUnavailableError:
            ConversationService.finishFailedRequest(session, conversationId, requestId, claimId)
            raise HTTPException(503, "Chat content unavailable") from None
        except Exception:
            # A failed provider call must not permanently lock the conversation.
            ConversationService.finishFailedRequest(session, conversationId, requestId, claimId)
            raise

        currentClaim = session.scalar(
            select(ConversationEntity)
            .where(
                ConversationEntity.id == conversationId,
                ConversationEntity.activeRequestId == claimId,
            )
            .with_for_update()
        )
        if currentClaim is None:
            session.rollback()
            raise HTTPException(409, "Conversation changed while answering")
        userMessage.status = "completed"
        assistantMessage = MessageEntity(
            conversationId=conversationId,
            requestId=requestId,
            role="assistant",
            content=answer.reply,
            response=answer.model_dump(mode="json"),
        )
        session.add(assistantMessage)
        currentClaim.busyUntil = None
        currentClaim.activeRequestId = None
        currentClaim.updatedAt = currentTime()
        session.commit()
        return assistantMessage

    @staticmethod
    def finishFailedRequest(
        session: Session, conversationId: str, requestId: str, claimId: str
    ) -> None:
        session.rollback()
        currentClaim = session.scalar(
            select(ConversationEntity)
            .where(
                ConversationEntity.id == conversationId,
                ConversationEntity.activeRequestId == claimId,
            )
            .with_for_update()
        )
        if currentClaim is None:
            session.rollback()
            return
        session.execute(
            update(MessageEntity)
            .where(
                MessageEntity.conversationId == conversationId,
                MessageEntity.requestId == requestId,
                MessageEntity.role == "user",
            )
            .values(status="failed")
        )
        session.execute(
            update(ConversationEntity)
            .where(
                ConversationEntity.id == conversationId,
                ConversationEntity.activeRequestId == claimId,
            )
            .values(busyUntil=None, activeRequestId=None)
        )
        session.commit()
