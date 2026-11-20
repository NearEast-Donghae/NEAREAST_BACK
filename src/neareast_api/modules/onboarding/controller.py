import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from neareast_api.db.entities import UserEntity
from neareast_api.db.session import getSession
from neareast_api.modules.auth.service import requireUser
from neareast_api.modules.chat import retrieval
from neareast_api.modules.onboarding import service, taste
from neareast_api.modules.onboarding.dto import (
    OnboardingOptionsDto,
    OriginsDto,
    PhotoBoardDto,
    TasteDto,
    TasteRequestDto,
)
from neareast_api.modules.places import service as places
from neareast_api.modules.trains import service as trains

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])
KOREA = ZoneInfo("Asia/Seoul")
UNAVAILABLE = {503: {"description": "Timetable snapshot or places unavailable"}}


def _timetable(request: Request) -> trains.Timetable:
    try:
        return trains.load(request.app.state.settings.content_dir)
    except trains.TimetableUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "timetable unavailable") from None


def _stations(request: Request) -> dict[str, str]:
    try:
        catalog = places.all_places(request.app.state.settings.content_dir)
    except places.PlacesError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "places unavailable") from None
    return {p.id: p.name for p in catalog.values() if p.kind == "station"}


@router.get("/options", response_model=OnboardingOptionsDto, responses=UNAVAILABLE,
            summary="Choices for the planner's first screen: stations to go to, companions")
def getOptions(request: Request):
    return service.options(_timetable(request), _stations(request))


@router.get(
    "/origins", response_model=OriginsDto,
    summary="Stations with a train to the chosen station, with trains a day and train types",
    responses={404: {"description": "Unknown station id"},
               422: {"description": "No passenger train stops at that station"}, **UNAVAILABLE})
def getOrigins(request: Request, destination: str = Query(min_length=1, max_length=64)):
    stations = _stations(request)
    if destination not in stations:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "station not found")
    timetable = _timetable(request)
    if not service.arrivals(timetable, destination):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "no passenger train stops at this station")
    return service.origins(timetable, destination, stations[destination])


# --- Choosing pictures -------------------------------------------------------------

def _places(request: Request) -> dict:
    try:
        return places.all_places(request.app.state.settings.content_dir)
    except places.PlacesError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "places unavailable") from None


def _taste(request: Request, session: Session, photoIds, date: dt.date | None, updatedAt):
    """The answer for a choice of pictures. A missing timetable or missing knowledge
    vectors only leave their part out: the taste itself still answers."""
    settings = request.app.state.settings
    timetable = trains.load_or_none(settings.content_dir)
    try:
        content = retrieval.load_content(settings.content_dir)
    except retrieval.ContentError:
        content = None
    if date is None:
        date = (dt.date.fromisoformat(timetable.meta["target_date"]) if timetable is not None
                else dt.datetime.now(KOREA).date())
    return taste.describe(photoIds, taste.catalog(session), _places(request), timetable, date,
                          content, updatedAt)


@router.get("/photos", response_model=PhotoBoardDto, responses=UNAVAILABLE,
            summary="Pictures to choose from on the first screen, with their moods and credits")
def getPhotos(request: Request, session: Session = Depends(getSession)):
    return taste.board(taste.catalog(session), _places(request))


@router.put(
    "/taste", response_model=TasteDto,
    summary="Keep the pictures the user chose; answers with the taste and places they lead to",
    responses={422: {"description": "Fewer than 3 or more than 12 pictures, a repeated id, "
                                    "or an id that is not on the board"}, **UNAVAILABLE})
def putTaste(
    body: TasteRequestDto, request: Request, date: dt.date | None = None,
    user: UserEntity = Depends(requireUser), session: Session = Depends(getSession),
):
    known = {photo.id for photo in taste.catalog(session)}
    unknown = [photoId for photoId in body.photoIds if photoId not in known]
    if unknown or len(set(body.photoIds)) != len(body.photoIds):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "message": "unknown or repeated photo ids", "unknown": unknown})
    kept = taste.save(session, user.id, body.photoIds)
    return _taste(request, session, kept.photoIds, date, kept.updatedAt)


@router.get("/taste", response_model=TasteDto, responses={
    404: {"description": "The user has not chosen pictures yet"}, **UNAVAILABLE},
    summary="The user's kept choice, read against the catalog and timetable as they are now")
def getTaste(
    request: Request, date: dt.date | None = None,
    user: UserEntity = Depends(requireUser), session: Session = Depends(getSession),
):
    kept = taste.saved(session, user.id)
    answer = kept and _taste(request, session, kept.photoIds, date, kept.updatedAt)
    if not answer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no pictures chosen yet")
    return answer


@router.delete("/taste", status_code=204, summary="Forget the user's choice of pictures")
def deleteTaste(user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)):
    taste.clear(session, user.id)
