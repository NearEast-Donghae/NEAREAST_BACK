import hashlib

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from neareast_api.db.entities import (
    KnowledgeChunkEntity,
    KnowledgeDocumentEntity,
    PlaceEntity,
    TimetableSnapshotEntity,
    TrainRunEntity,
    TrainStopEntity,
    currentTime,
)
from neareast_api.modules.chat.retrieval import EMBEDDING_DIMENSIONS, Doc, embedding_text
from neareast_api.modules.places.service import Place

# Train numbers the collected records carry that differ from the number shown for the
# target date: collected number -> (number to store, why). Applied on every import, so
# collecting the timetable again does not bring the old number back.
NUMBER_OVERRIDES = {"1814": ("1804", "11/21 레일블루 기준, 3주 기록은 1814")}


def applyNumberOverrides(trains: list[dict]) -> list[dict]:
    present = {train["no"] for train in trains}
    renamed = []
    for train in trains:
        number, note = NUMBER_OVERRIDES.get(train["no"], (None, None))
        if number is not None and number not in present:
            train = {**train, "no": number, "number_note": note}
        renamed.append(train)
    return renamed


class IngestionService:
    @staticmethod
    def embedDocuments(session: Session, modelName: str, embedBatch=None) -> dict:
        documents = list(
            session.scalars(select(KnowledgeDocumentEntity).order_by(KnowledgeDocumentEntity.id))
        )
        places = {
            row.id: Place(row.id, row.name, row.kind, aliases=tuple(row.aliases))
            for row in session.scalars(select(PlaceEntity))
        }
        pending = []
        for document in documents:
            # Title, other names (keywords, place aliases), related place names and body:
            # the same text the server hashes to decide whether a stored vector is current.
            text = embedding_text(
                Doc(document.id, document.title, document.kind, tuple(document.places),
                    tuple(document.keywords), document.body),
                places,
            )
            contentHash = hashlib.sha256(text.encode()).hexdigest()
            chunk = session.scalar(
                select(KnowledgeChunkEntity).where(
                    KnowledgeChunkEntity.documentId == document.id,
                    KnowledgeChunkEntity.position == 0,
                )
            )
            if (
                chunk
                and chunk.contentHash == contentHash
                and chunk.embeddingModel == modelName
                and chunk.embedding is not None
            ):
                continue
            pending.append((document, text, contentHash, chunk))
        if embedBatch is None:
            return {"pending": [entry[0].id for entry in pending], "embedded": 0}
        if not pending:
            return {"pending": [], "embedded": 0}
        vectors = embedBatch(modelName, [entry[1] for entry in pending])
        if len(vectors) != len(pending) or any(
            len(vector) != EMBEDDING_DIMENSIONS for vector in vectors
        ):
            raise ValueError(
                "Embedding response must contain one 1536-dimensional vector per document"
            )
        for (document, text, contentHash, chunk), vector in zip(pending, vectors, strict=True):
            if chunk is None:
                chunk = KnowledgeChunkEntity(documentId=document.id, position=0)
                session.add(chunk)
            chunk.body = text
            chunk.contentHash = contentHash
            chunk.embeddingModel = modelName
            chunk.embedding = vector
        session.commit()
        return {"pending": [], "embedded": len(pending)}

    @staticmethod
    def importDocument(
        session: Session,
        documentId: str,
        title: str,
        body: str,
        kind: str,
        places: list,
        keywords: list,
        sourceUrl: str | None,
    ) -> None:
        document = session.get(KnowledgeDocumentEntity, documentId)
        if document is None:
            document = KnowledgeDocumentEntity(id=documentId)
            session.add(document)
        document.title = title
        document.body = body
        document.kind = kind
        document.places = places
        document.keywords = keywords
        document.sourceUrl = sourceUrl
        document.updatedAt = currentTime()
        session.commit()

    @staticmethod
    def importTimetable(session: Session, snapshot: dict) -> None:
        snapshotId = snapshot["meta"]["target_date"]
        # Train types are confirmed by hand and are not in the collected data: whatever
        # is already known for a train number is carried over, with where it was confirmed.
        existingTypes = {
            run.trainNumber: (run.trainType, run.trainTypeSource)
            for run in session.scalars(select(TrainRunEntity).order_by(TrainRunEntity.snapshotId))
            if run.trainType
        }
        session.execute(
            delete(TimetableSnapshotEntity).where(TimetableSnapshotEntity.id == snapshotId)
        )
        session.add(
            TimetableSnapshotEntity(
                id=snapshotId, metadataValue=snapshot["meta"], stations=snapshot["stations"]
            )
        )
        session.flush()
        for train in applyNumberOverrides(snapshot["trains"]):
            serviceId = f"{snapshotId}:{train['no']}"
            session.add(
                TrainRunEntity(
                    id=serviceId,
                    snapshotId=snapshotId,
                    trainNumber=train["no"],
                    trainType=existingTypes.get(train["no"], (None, None))[0],
                    trainTypeSource=existingTypes.get(train["no"], (None, None))[1],
                    details={key: value for key, value in train.items() if key != "stops"},
                )
            )
            session.flush()
            for position, stop in enumerate(train["stops"]):
                session.add(
                    TrainStopEntity(
                        id=f"{serviceId}:{position}",
                        serviceId=serviceId,
                        position=position,
                        placeId=stop.get("place_id"),
                        details=stop,
                    )
                )
        session.commit()
