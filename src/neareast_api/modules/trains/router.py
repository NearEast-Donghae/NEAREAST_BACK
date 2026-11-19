"""HTTP boundary for the shared timetable."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from neareast_api.modules.places import service as places
from neareast_api.modules.trains import service
from neareast_api.modules.trains.schemas import (
    TIME,
    Direction,
    StationTrains,
    TrainDetail,
    TrainSearch,
)

router = APIRouter(tags=["Trains"])
After = Annotated[str | None, Query(pattern=TIME, description="HH:MM 이후만")]
UNAVAILABLE = {503: {"description": "Timetable snapshot unavailable"}}


def _timetable(request: Request) -> service.Timetable:
    try:
        return service.load(request.app.state.settings.content_dir)
    except service.TimetableUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "timetable unavailable") from None


def _station(request: Request, station_id: str) -> places.Place:
    try:
        place = places.get(request.app.state.settings.content_dir, station_id)
    except places.PlacesError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "places unavailable") from None
    if place is None or place.kind != "station":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "station not found")
    return place


@router.get(
    "/stations/{station_id}/trains",
    response_model=StationTrains,
    summary="Trains at a station in time order, with the hourly rhythm and gaps",
    responses={404: {"description": "Unknown station id"}, **UNAVAILABLE},
)
def get_station_trains(
    station_id: str, request: Request, after: After = None,
    direction: Annotated[Direction | None, Query(description="U 상행 / D 하행")] = None,
) -> StationTrains:
    station = _station(request, station_id)
    return service.station_trains(_timetable(request), station.id, station.name, after, direction)


@router.get(
    "/trains",
    response_model=TrainSearch,
    summary="Trains one can ride from one station to another",
    responses={404: {"description": "Unknown station id"}, **UNAVAILABLE},
)
def search_trains(
    request: Request,
    from_id: Annotated[str, Query(alias="from", description="출발역 id")],
    to_id: Annotated[str, Query(alias="to", description="도착역 id")],
    after: After = None,
) -> TrainSearch:
    _station(request, from_id)
    _station(request, to_id)
    return service.search(_timetable(request), from_id, to_id, after)


@router.get(
    "/trains/{train_no}",
    response_model=TrainDetail,
    summary="One train with every stop and estimated passing station",
    responses={404: {"description": "Unknown train number"}, **UNAVAILABLE},
)
def get_train(train_no: str, request: Request) -> TrainDetail:
    train = service.detail(_timetable(request), train_no)
    if train is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "train not found")
    return train
