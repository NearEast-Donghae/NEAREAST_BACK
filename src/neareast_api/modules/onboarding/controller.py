from fastapi import APIRouter, HTTPException, Query, Request, status

from neareast_api.modules.onboarding import service
from neareast_api.modules.onboarding.dto import OnboardingOptionsDto, OriginsDto
from neareast_api.modules.places import service as places
from neareast_api.modules.trains import service as trains

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])
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
