from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from neareast_api.db.entities import PlaceEntity, SavedPlaceEntity, SavedTrainEntity, currentTime
from neareast_api.modules.chat.service import SavedSelection
from neareast_api.modules.itineraries.service import validatePlaceIds
from neareast_api.modules.places.identity import canonicalPlaceId
from neareast_api.modules.saved.dto import (
    SavedDto,
    SavedPlaceCreateDto,
    SavedPlaceDto,
    SavedTrainCreateDto,
    SavedTrainDto,
    SavedTrainUpdateDto,
)
from neareast_api.modules.trains import service as trainService

SAVED_LIMIT = 50  # places and trains each, per user
PASSING_ONLY = "이 역은 열차가 서지 않아 관찰만 할 수 있어요"
NOT_AT_STATION = "이 열차는 이 역을 지나지 않아요"
TRAIN_FIELDS = ("line", "direction", "origin", "destination", "time", "arr", "dep", "passing")


def ownedSavedPlace(session: Session, savedId: str, userId: str) -> SavedPlaceEntity:
    saved = session.scalar(
        select(SavedPlaceEntity).where(
            SavedPlaceEntity.id == savedId, SavedPlaceEntity.userId == userId
        )
    )
    if saved is None:
        raise HTTPException(404, "Saved place not found")
    return saved


def ownedSavedTrain(session: Session, savedId: str, userId: str) -> SavedTrainEntity:
    saved = session.scalar(
        select(SavedTrainEntity).where(
            SavedTrainEntity.id == savedId, SavedTrainEntity.userId == userId
        )
    )
    if saved is None:
        raise HTTPException(404, "Saved train not found")
    return saved


def loadTimetable() -> trainService.Timetable:
    try:
        return trainService.load(".")
    except trainService.TimetableUnavailable:
        raise HTTPException(503, "Timetable unavailable") from None


def trainNumber(trainNo: str) -> str:
    # The timetable keeps numbers without leading zeros ("01861" is train 1861).
    return trainNo.strip().lstrip("0") or "0"


def checkTrain(session: Session, trainNo: str, action: str, stationId: str) -> None:
    """A train can be saved only where it really comes by, and ridden only where it stops."""
    validatePlaceIds(session, [stationId])
    station = session.get(PlaceEntity, stationId)
    if station.kind != "station":
        raise HTTPException(422, "stationId must be a station")
    atStation = trainService.station_trains(loadTimetable(), stationId).trains
    stop = next((train for train in atStation if train.no == trainNo), None)
    if stop is None:
        raise HTTPException(422, NOT_AT_STATION)
    if action == "ride" and (stop.passing or station.passengerStop is False):
        raise HTTPException(422, PASSING_ONLY)


def checkRoom(session: Session, entity: type, userId: str, label: str) -> None:
    count = session.scalar(select(func.count()).select_from(entity).where(entity.userId == userId))
    if count >= SAVED_LIMIT:
        raise HTTPException(409, f"Saved {label} limit reached ({SAVED_LIMIT})")


class SavedService:
    @staticmethod
    def savePlace(
        session: Session, userId: str, body: SavedPlaceCreateDto
    ) -> tuple[SavedPlaceEntity, bool]:
        """Returns the item and whether it was created now (an existing one is returned as is)."""
        body = body.model_copy(update={"placeId": canonicalPlaceId(session, body.placeId)})
        same = select(SavedPlaceEntity).where(
            SavedPlaceEntity.userId == userId, SavedPlaceEntity.placeId == body.placeId
        )
        existing = session.scalar(same)
        if existing is not None:
            return existing, False
        validatePlaceIds(session, [body.placeId])
        checkRoom(session, SavedPlaceEntity, userId, "place")
        saved = SavedPlaceEntity(userId=userId, placeId=body.placeId, priority=body.priority)
        session.add(saved)
        try:
            session.commit()
        except IntegrityError:  # saved by a simultaneous request
            session.rollback()
            return session.scalar(same), False
        return saved, True

    @staticmethod
    def saveTrain(
        session: Session, userId: str, body: SavedTrainCreateDto
    ) -> tuple[SavedTrainEntity, bool]:
        body = body.model_copy(update={"stationId": canonicalPlaceId(session, body.stationId)})
        trainNo = trainNumber(body.trainNo)
        same = select(SavedTrainEntity).where(
            SavedTrainEntity.userId == userId,
            SavedTrainEntity.trainNo == trainNo,
            SavedTrainEntity.action == body.action,
            SavedTrainEntity.stationId == body.stationId,
        )
        existing = session.scalar(same)
        if existing is not None:
            return existing, False
        checkTrain(session, trainNo, body.action, body.stationId)
        checkRoom(session, SavedTrainEntity, userId, "train")
        saved = SavedTrainEntity(
            userId=userId,
            trainNo=trainNo,
            action=body.action,
            stationId=body.stationId,
            priority=body.priority,
        )
        session.add(saved)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return session.scalar(same), False
        return saved, True

    @staticmethod
    def updateTrain(
        session: Session, saved: SavedTrainEntity, body: SavedTrainUpdateDto
    ) -> SavedTrainEntity:
        if body.action is not None and body.action != saved.action:
            checkTrain(session, saved.trainNo, body.action, saved.stationId)
            saved.action = body.action
        if body.priority is not None:
            saved.priority = body.priority
        saved.updatedAt = currentTime()
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "The same train is already saved that way") from None
        return saved

    @staticmethod
    def places(session: Session, userId: str) -> list[SavedPlaceEntity]:
        return list(
            session.scalars(
                select(SavedPlaceEntity)
                .where(SavedPlaceEntity.userId == userId)
                .order_by(SavedPlaceEntity.createdAt, SavedPlaceEntity.id)
            )
        )

    @staticmethod
    def trains(session: Session, userId: str) -> list[SavedTrainEntity]:
        return list(
            session.scalars(
                select(SavedTrainEntity)
                .where(SavedTrainEntity.userId == userId)
                .order_by(SavedTrainEntity.createdAt, SavedTrainEntity.id)
            )
        )

    @staticmethod
    def describePlace(session: Session, saved: SavedPlaceEntity) -> SavedPlaceDto:
        place = session.get(PlaceEntity, saved.placeId)
        return SavedPlaceDto(
            id=saved.id,
            placeId=saved.placeId,
            name=place.name,
            kind=place.kind,
            priority=saved.priority,
            createdAt=saved.createdAt,
            updatedAt=saved.updatedAt,
        )

    @staticmethod
    def describeTrain(
        session: Session, saved: SavedTrainEntity, timetable: trainService.Timetable | None
    ) -> SavedTrainDto:
        """The saved train with what the timetable says about it at that station now."""
        stop = None
        if timetable is not None:
            atStation = trainService.station_trains(timetable, saved.stationId).trains
            stop = next((train for train in atStation if train.no == saved.trainNo), None)
        return SavedTrainDto(
            id=saved.id,
            trainNo=saved.trainNo,
            action=saved.action,
            stationId=saved.stationId,
            stationName=session.get(PlaceEntity, saved.stationId).name,
            priority=saved.priority,
            available=stop is not None,
            trainType=stop.type if stop else None,
            timeBasis=stop.time_basis if stop else None,
            certainty=timetable.meta.get("certainty", "estimated") if stop else None,
            **{name: getattr(stop, name) if stop else None for name in TRAIN_FIELDS},
            createdAt=saved.createdAt,
            updatedAt=saved.updatedAt,
        )

    @staticmethod
    def describe(session: Session, userId: str) -> SavedDto:
        timetable = trainService.load_or_none(".")
        return SavedDto(
            places=[
                SavedService.describePlace(session, saved)
                for saved in SavedService.places(session, userId)
            ],
            trains=[
                SavedService.describeTrain(session, saved, timetable)
                for saved in SavedService.trains(session, userId)
            ],
            basis=timetable.basis().summary if timetable else None,
        )

    @staticmethod
    def selection(session: Session, userId: str) -> SavedSelection:
        """What the chat needs to know about the user's saved items."""
        places = SavedService.places(session, userId)
        return SavedSelection(
            placeIds=tuple(saved.placeId for saved in places),
            mustPlaceIds=tuple(saved.placeId for saved in places if saved.priority == "must"),
            trains=tuple(
                (saved.trainNo, saved.action, saved.stationId)
                for saved in SavedService.trains(session, userId)
            ),
        )
