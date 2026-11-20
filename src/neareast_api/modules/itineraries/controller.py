from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from neareast_api.db.entities import (
    ItineraryEntity,
    ItineraryRevisionEntity,
    PlanningRequestEntity,
    UserEntity,
    currentTime,
)
from neareast_api.db.session import getSession
from neareast_api.modules.auth.service import requireUser
from neareast_api.modules.conversations.service import ownedConversation
from neareast_api.modules.itineraries.dto import (
    ItineraryCreateDto,
    ItineraryDto,
    ItineraryUpdateDto,
    PlanningRequestCreateDto,
    PlanningRequestDto,
    PlanningRequestUpdateDto,
)
from neareast_api.modules.itineraries.service import (
    ItineraryService,
    ownedItinerary,
    ownedPlanningRequest,
    validateConditions,
)

router = APIRouter(tags=["Itineraries"])


@router.post("/itineraries", response_model=ItineraryDto, status_code=201)
def createItinerary(
    body: ItineraryCreateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    return ItineraryService.serialize(ItineraryService.save(session, user.id, body))


@router.get("/itineraries", response_model=list[ItineraryDto])
def listItineraries(
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    itineraries = session.scalars(
        select(ItineraryEntity)
        .where(ItineraryEntity.userId == user.id)
        .order_by(ItineraryEntity.createdAt.desc(), ItineraryEntity.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return [ItineraryService.serialize(itinerary) for itinerary in itineraries]


@router.get("/itineraries/{itineraryId}", response_model=ItineraryDto)
def getItinerary(
    itineraryId: str,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    return ItineraryService.serialize(ownedItinerary(session, itineraryId, user.id))


@router.put("/itineraries/{itineraryId}", response_model=ItineraryDto)
def replaceItinerary(
    itineraryId: str,
    body: ItineraryUpdateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    itinerary = ownedItinerary(session, itineraryId, user.id, lock=True)
    if itinerary.revision != body.expectedRevision:
        raise HTTPException(409, "Itinerary changed; reload before saving")
    return ItineraryService.serialize(ItineraryService.save(session, user.id, body, itinerary))


@router.delete("/itineraries/{itineraryId}", status_code=204)
def deleteItinerary(
    itineraryId: str,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    session.delete(ownedItinerary(session, itineraryId, user.id))
    session.commit()


@router.get("/itineraries/{itineraryId}/revisions")
def listRevisions(
    itineraryId: str,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    ownedItinerary(session, itineraryId, user.id)
    revisions = session.scalars(
        select(ItineraryRevisionEntity)
        .where(ItineraryRevisionEntity.itineraryId == itineraryId)
        .order_by(ItineraryRevisionEntity.revision.desc())
    )
    return [
        {
            "revision": revision.revision,
            "itinerary": revision.payload,
            "createdAt": revision.createdAt,
        }
        for revision in revisions
    ]


@router.post("/planning-requests", response_model=PlanningRequestDto, status_code=201)
def createPlanningRequest(
    body: PlanningRequestCreateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    if body.conversationId:
        ownedConversation(session, body.conversationId, user.id)
    validateConditions(session, body.conditions)
    planningRequest = PlanningRequestEntity(
        userId=user.id,
        source=body.source,
        conversationId=body.conversationId,
        conditions=body.conditions.model_dump(mode="json"),
    )
    session.add(planningRequest)
    session.commit()
    return planningRequest


@router.get("/planning-requests", response_model=list[PlanningRequestDto])
def listPlanningRequests(
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return list(
        session.scalars(
            select(PlanningRequestEntity)
            .where(PlanningRequestEntity.userId == user.id)
            .order_by(PlanningRequestEntity.updatedAt.desc(), PlanningRequestEntity.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/planning-requests/{requestId}", response_model=PlanningRequestDto)
def getPlanningRequest(
    requestId: str, user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)
):
    return ownedPlanningRequest(session, requestId, user.id)


@router.patch("/planning-requests/{requestId}", response_model=PlanningRequestDto)
def updatePlanningRequest(
    requestId: str,
    body: PlanningRequestUpdateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    planningRequest = ownedPlanningRequest(session, requestId, user.id, lock=True)
    if planningRequest.revision != body.expectedRevision:
        raise HTTPException(409, "Travel conditions changed; reload before saving")
    validateConditions(session, body.conditions)
    planningRequest.conditions = body.conditions.model_dump(mode="json")
    planningRequest.revision += 1
    planningRequest.updatedAt = currentTime()
    session.commit()
    return planningRequest


@router.delete("/planning-requests/{requestId}", status_code=204)
def deletePlanningRequest(
    requestId: str, user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)
):
    session.delete(ownedPlanningRequest(session, requestId, user.id))
    session.commit()


@router.post(
    "/planning-requests/{requestId}/generations",
    responses={503: {"description": "Route generator not connected"}},
)
def generateItineraries(
    requestId: str,
    request: Request,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    planningRequest = ownedPlanningRequest(session, requestId, user.id)
    generator = getattr(request.app.state, "itineraryGenerator", None)
    if generator is not None:
        return generator(planningRequest.conditions)
    from neareast_api.modules.planner.service import PlannerService
    result = PlannerService.generate(session, planningRequest.conditions, request.app.state.settings)
    if result["itinerary"] is not None:
        body = ItineraryCreateDto(**{**result["itinerary"], "planningRequestId": requestId})
        itinerary = ItineraryService.save(session, user.id, body)
        result["itinerary"] = ItineraryService.serialize(itinerary).model_dump(mode="json")
    return result
