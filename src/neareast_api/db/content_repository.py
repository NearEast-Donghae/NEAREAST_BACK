from sqlalchemy import select

from neareast_api.db.entities import (
    KnowledgeChunkEntity,
    KnowledgeDocumentEntity,
    PlaceEntity,
    TimetableSnapshotEntity,
    TrainRunEntity,
    TrainStopEntity,
)
from neareast_api.db.session import contentSession


class ContentRepository:
    @staticmethod
    def places():
        from neareast_api.modules.places.service import Place, PlacesError

        rows = list(contentSession().scalars(select(PlaceEntity).order_by(PlaceEntity.id)))
        if not rows:
            raise PlacesError("Place catalog has not been imported")
        return {
            row.id: Place(
                row.id, row.name, row.kind, row.passengerStop, tuple(row.aliases), row.lat, row.lng
            )
            for row in rows if not (row.operatingInfo or {}).get("canonicalPlaceId")
        }

    @staticmethod
    def knowledge():
        from neareast_api.modules.chat.retrieval import Content, ContentError, Doc, content_hash
        from neareast_api.modules.places.service import PlacesError

        session = contentSession()
        rows = list(session.scalars(
            select(KnowledgeDocumentEntity).order_by(KnowledgeDocumentEntity.id)
        ))
        verifiedPlaces = {placeId for row in rows
                          if row.id.startswith(("tourapi:", "historical:")) and row.sourceUrl
                          for placeId in row.places}
        documents = {
            row.id: Doc(row.id, row.title, row.kind, tuple(row.places), tuple(row.keywords), row.body)
            for row in rows
            if not (row.sourceUrl is None and (
                row.id.startswith("seed-preserved:") or row.id in verifiedPlaces
            ))
        }
        if not documents:
            raise ContentError("Knowledge has not been imported")
        try:
            places = ContentRepository.places()
        except PlacesError as exc:
            raise ContentError("Place catalog has not been imported") from exc
        chunks = list(
            session.scalars(select(KnowledgeChunkEntity).where(KnowledgeChunkEntity.position == 0))
        )
        vectors = {}
        model = None
        for chunk in chunks:
            document = documents.get(chunk.documentId)
            # A vector made from an older text (document, alias or place name changed since)
            # is left out until scripts/ingest.py embeds the document again.
            if (
                document
                and chunk.embedding is not None
                and chunk.contentHash == content_hash(document, places)
            ):
                if model is None:
                    model = chunk.embeddingModel
                if chunk.embeddingModel == model:
                    vectors[chunk.documentId] = tuple(float(v) for v in chunk.embedding)
        return Content(places, documents, model, vectors)

    @staticmethod
    def timetable():
        from neareast_api.modules.trains.service import Timetable, TimetableUnavailable

        session = contentSession()
        snapshot = session.scalar(
            select(TimetableSnapshotEntity).order_by(TimetableSnapshotEntity.id.desc()).limit(1)
        )
        if snapshot is None:
            raise TimetableUnavailable("No timetable snapshot has been imported")
        runs = list(
            session.scalars(
                select(TrainRunEntity)
                .where(TrainRunEntity.snapshotId == snapshot.id)
                .order_by(TrainRunEntity.trainNumber)
            )
        )
        stopsByRun = {run.id: [] for run in runs}
        for stop in session.scalars(
            select(TrainStopEntity)
            .join(TrainRunEntity)
            .where(
                TrainRunEntity.snapshotId == snapshot.id,
            )
            .order_by(TrainStopEntity.position)
        ):
            stopsByRun[stop.serviceId].append(stop.details)
        trains = tuple({**run.details, "stops": stopsByRun[run.id]} for run in runs)
        types = {run.trainNumber: run.trainType for run in runs if run.trainType}
        return Timetable(snapshot.metadataValue, snapshot.stations, trains, types)
