from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from neareast_api.db.entities import UserEntity, currentTime
from neareast_api.db.session import getSession
from neareast_api.modules.auth.service import requireUser
from neareast_api.modules.saved.dto import (
    SavedDto,
    SavedPlaceCreateDto,
    SavedPlaceDto,
    SavedPlaceUpdateDto,
    SavedTrainCreateDto,
    SavedTrainDto,
    SavedTrainUpdateDto,
)
from neareast_api.modules.saved.service import (
    SavedService,
    loadTimetable,
    ownedSavedPlace,
    ownedSavedTrain,
)

router = APIRouter(prefix="/saved", tags=["Saved"])
SAVE_RESPONSES = {
    200: {"description": "Already saved; the existing item is returned unchanged"},
    409: {"description": "Limit of 50 reached"},
    422: {"description": "Unknown place or train, or riding where the train does not stop"},
}


@router.get("", response_model=SavedDto)
def listSaved(user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)):
    return SavedService.describe(session, user.id)


@router.post("/places", response_model=SavedPlaceDto, status_code=201, responses=SAVE_RESPONSES)
def savePlace(
    body: SavedPlaceCreateDto,
    response: Response,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    saved, created = SavedService.savePlace(session, user.id, body)
    if not created:
        response.status_code = 200
    return SavedService.describePlace(session, saved)


@router.patch("/places/{savedId}", response_model=SavedPlaceDto)
def updateSavedPlace(
    savedId: str,
    body: SavedPlaceUpdateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    saved = ownedSavedPlace(session, savedId, user.id)
    saved.priority = body.priority
    saved.updatedAt = currentTime()
    session.commit()
    return SavedService.describePlace(session, saved)


@router.delete("/places/{savedId}", status_code=204)
def deleteSavedPlace(
    savedId: str, user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)
):
    session.delete(ownedSavedPlace(session, savedId, user.id))
    session.commit()


@router.post("/trains", response_model=SavedTrainDto, status_code=201, responses=SAVE_RESPONSES)
def saveTrain(
    body: SavedTrainCreateDto,
    response: Response,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    saved, created = SavedService.saveTrain(session, user.id, body)
    if not created:
        response.status_code = 200
    return SavedService.describeTrain(session, saved, loadTimetable())


@router.patch("/trains/{savedId}", response_model=SavedTrainDto)
def updateSavedTrain(
    savedId: str,
    body: SavedTrainUpdateDto,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    saved = SavedService.updateTrain(session, ownedSavedTrain(session, savedId, user.id), body)
    return SavedService.describeTrain(session, saved, loadTimetable())


@router.delete("/trains/{savedId}", status_code=204)
def deleteSavedTrain(
    savedId: str, user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)
):
    session.delete(ownedSavedTrain(session, savedId, user.id))
    session.commit()
