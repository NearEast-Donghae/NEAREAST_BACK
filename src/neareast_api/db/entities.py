from datetime import datetime, timezone
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from neareast_api.db.base import Base

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")
EMBEDDING_VALUE = Vector(1536).with_variant(JSON(), "sqlite")


def currentTime() -> datetime:
    return datetime.now(timezone.utc)


def createId() -> str:
    return str(uuid4())


class UserEntity(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    nickname: Mapped[str] = mapped_column(String(40))
    passwordHash: Mapped[str] = mapped_column("password_hash", String(255))
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )


class AuthSessionEntity(Base):
    __tablename__ = "auth_sessions"
    tokenHash: Mapped[str] = mapped_column("token_hash", String(64), primary_key=True)
    userId: Mapped[str] = mapped_column(
        "user_id", ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expiresAt: Mapped[datetime] = mapped_column("expires_at", DateTime(timezone=True), index=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )


class PlaceEntity(Base):
    __tablename__ = "places"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), index=True)
    passengerStop: Mapped[bool | None] = mapped_column("passenger_stop")
    aliases: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    operatingInfo: Mapped[dict] = mapped_column("operating_info", JSON_VALUE, default=dict)
    sourceUrl: Mapped[str | None] = mapped_column("source_url", Text)


class KnowledgeDocumentEntity(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))
    places: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    keywords: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    body: Mapped[str] = mapped_column(Text)
    sourceUrl: Mapped[str | None] = mapped_column("source_url", Text)
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), default=currentTime
    )


class KnowledgeChunkEntity(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("document_id", "position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    documentId: Mapped[str] = mapped_column(
        "document_id", ForeignKey("knowledge_documents.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text)
    contentHash: Mapped[str] = mapped_column("content_hash", String(64))
    embeddingModel: Mapped[str | None] = mapped_column("embedding_model", String(100))
    embedding: Mapped[list | None] = mapped_column(EMBEDDING_VALUE)


class TimetableSnapshotEntity(Base):
    __tablename__ = "timetable_snapshots"
    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    metadataValue: Mapped[dict] = mapped_column("metadata", JSON_VALUE)
    stations: Mapped[dict] = mapped_column(JSON_VALUE)


class TrainRunEntity(Base):
    __tablename__ = "train_services"
    __table_args__ = (UniqueConstraint("snapshot_id", "train_number"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshotId: Mapped[str] = mapped_column(
        "snapshot_id", ForeignKey("timetable_snapshots.id", ondelete="CASCADE")
    )
    trainNumber: Mapped[str] = mapped_column("train_number", String(20), index=True)
    trainType: Mapped[str | None] = mapped_column("train_type", String(40))
    details: Mapped[dict] = mapped_column(JSON_VALUE)


class TrainStopEntity(Base):
    __tablename__ = "train_stops"
    __table_args__ = (UniqueConstraint("service_id", "position"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    serviceId: Mapped[str] = mapped_column(
        "service_id", ForeignKey("train_services.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    placeId: Mapped[str | None] = mapped_column("place_id", ForeignKey("places.id"), index=True)
    details: Mapped[dict] = mapped_column(JSON_VALUE)


class ConversationEntity(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    userId: Mapped[str] = mapped_column(
        "user_id", ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), default="새 대화")
    busyUntil: Mapped[datetime | None] = mapped_column("busy_until", DateTime(timezone=True))
    activeRequestId: Mapped[str | None] = mapped_column("active_request_id", String(36))
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), default=currentTime
    )


class MessageEntity(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "request_id", "role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    conversationId: Mapped[str] = mapped_column(
        "conversation_id", ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    requestId: Mapped[str] = mapped_column("request_id", String(36))
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    response: Mapped[dict | None] = mapped_column(JSON_VALUE)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )


class PlanningRequestEntity(Base):
    __tablename__ = "planning_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    userId: Mapped[str] = mapped_column(
        "user_id", ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    conversationId: Mapped[str | None] = mapped_column(
        "conversation_id", ForeignKey("conversations.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(20))
    conditions: Mapped[dict] = mapped_column(JSON_VALUE)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), default=currentTime
    )


class ItineraryEntity(Base):
    __tablename__ = "itineraries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    userId: Mapped[str] = mapped_column(
        "user_id", ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    planningRequestId: Mapped[str | None] = mapped_column(
        "planning_request_id", ForeignKey("planning_requests.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON_VALUE)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), default=currentTime
    )


class ItineraryRevisionEntity(Base):
    __tablename__ = "itinerary_revisions"
    __table_args__ = (UniqueConstraint("itinerary_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    itineraryId: Mapped[str] = mapped_column(
        "itinerary_id", ForeignKey("itineraries.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON_VALUE)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )


class SavedPlaceEntity(Base):
    __tablename__ = "saved_places"
    __table_args__ = (UniqueConstraint("user_id", "place_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    userId: Mapped[str] = mapped_column(
        "user_id", ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    placeId: Mapped[str] = mapped_column("place_id", ForeignKey("places.id", ondelete="CASCADE"))
    priority: Mapped[str] = mapped_column(String(20))
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), default=currentTime
    )


class SavedTrainEntity(Base):
    __tablename__ = "saved_trains"
    __table_args__ = (UniqueConstraint("user_id", "train_no", "action", "station_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=createId)
    userId: Mapped[str] = mapped_column(
        "user_id", ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # A train number, not a key into one snapshot: the timetable is replaced as a whole.
    trainNo: Mapped[str] = mapped_column("train_no", String(20))
    action: Mapped[str] = mapped_column(String(10))
    stationId: Mapped[str] = mapped_column(
        "station_id", ForeignKey("places.id", ondelete="CASCADE")
    )
    priority: Mapped[str] = mapped_column(String(20))
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime(timezone=True), default=currentTime
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), default=currentTime
    )
