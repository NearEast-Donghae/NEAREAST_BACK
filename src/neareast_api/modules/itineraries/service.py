from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from neareast_api.db.entities import (
    ItineraryEntity,
    ItineraryRevisionEntity,
    PlaceEntity,
    PlanningRequestEntity,
    currentTime,
)
from neareast_api.modules.itineraries.dto import (
    ItineraryCreateDto,
    ItineraryDto,
    PlanningConditionsDto,
)
from neareast_api.modules.places.identity import canonicalPlaceId


def ownedItinerary(
    session: Session, itineraryId: str, userId: str, lock: bool = False
) -> ItineraryEntity:
    statement = select(ItineraryEntity).where(
        ItineraryEntity.id == itineraryId, ItineraryEntity.userId == userId
    )
    itinerary = session.scalar(statement.with_for_update() if lock else statement)
    if itinerary is None:
        raise HTTPException(404, "Itinerary not found")
    return itinerary


def ownedPlanningRequest(
    session: Session, requestId: str, userId: str, lock: bool = False
) -> PlanningRequestEntity:
    statement = select(PlanningRequestEntity).where(
        PlanningRequestEntity.id == requestId, PlanningRequestEntity.userId == userId
    )
    planningRequest = session.scalar(statement.with_for_update() if lock else statement)
    if planningRequest is None:
        raise HTTPException(404, "Planning request not found")
    return planningRequest


def validatePlaceIds(session: Session, placeIds: list[str]) -> None:
    requestedIds = set(placeIds)
    existingIds = set(
        session.scalars(select(PlaceEntity.id).where(PlaceEntity.id.in_(requestedIds)))
    )
    if requestedIds - existingIds:
        raise HTTPException(422, {"unknownPlaceIds": sorted(requestedIds - existingIds)})


def validateConditions(session: Session, conditions: PlanningConditionsDto) -> None:
    for field in ("departurePlaceId", "arrivalPlaceId"):
        identifier = getattr(conditions, field)
        if identifier:
            setattr(conditions, field, canonicalPlaceId(session, identifier))
    conditions.requiredPlaceIds = list(dict.fromkeys(
        canonicalPlaceId(session, identifier) for identifier in conditions.requiredPlaceIds
    ))
    for train in conditions.selectedTrains or []:
        train.stationId = canonicalPlaceId(session, train.stationId)
    placeIds = [
        placeId for placeId in [conditions.departurePlaceId, conditions.arrivalPlaceId] if placeId
    ]
    stationIds = [train.stationId for train in conditions.selectedTrains or []]
    validatePlaceIds(session, placeIds + conditions.requiredPlaceIds + stationIds)


class ItineraryService:
    @staticmethod
    def serialize(itinerary: ItineraryEntity) -> ItineraryDto:
        return ItineraryDto(
            **{**itinerary.payload, "planningRequestId": itinerary.planningRequestId},
            id=itinerary.id,
            revision=itinerary.revision,
            createdAt=itinerary.createdAt,
            updatedAt=itinerary.updatedAt,
        )

    @staticmethod
    def save(
        session: Session,
        userId: str,
        body: ItineraryCreateDto,
        itinerary: ItineraryEntity | None = None,
    ) -> ItineraryEntity:
        body = body.model_copy(deep=True)
        for item in body.timeline:
            if item.placeId:
                item.placeId = canonicalPlaceId(session, item.placeId)
        for leg in body.legs:
            leg.fromPlaceId = canonicalPlaceId(session, leg.fromPlaceId)
            leg.toPlaceId = canonicalPlaceId(session, leg.toPlaceId)
        if body.planningRequestId:
            ownedPlanningRequest(session, body.planningRequestId, userId)
        placeIds = [item.placeId for item in body.timeline if item.placeId]
        placeIds.extend(
            placeId for leg in body.legs for placeId in [leg.fromPlaceId, leg.toPlaceId]
        )
        validatePlaceIds(session, placeIds)
        payload = body.model_dump(mode="json", exclude={"expectedRevision"})
        if itinerary is None:
            itinerary = ItineraryEntity(userId=userId, revision=1)
            session.add(itinerary)
        else:
            itinerary.revision += 1
        itinerary.title = body.title
        itinerary.source = body.source
        itinerary.planningRequestId = body.planningRequestId
        itinerary.payload = payload
        itinerary.updatedAt = currentTime()
        session.flush()
        session.add(
            ItineraryRevisionEntity(
                itineraryId=itinerary.id, revision=itinerary.revision, payload=payload
            )
        )
        session.commit()
        return itinerary
