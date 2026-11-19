"""HTTP boundary for places. The detail view joins three modules' public functions:
the catalog (places), the description (chat knowledge) and the timetable (trains)."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from neareast_api.modules.chat import retrieval
from neareast_api.modules.places import service
from neareast_api.modules.places.schemas import Kind, PlaceDetail, PlaceList, PlaceSummary
from neareast_api.modules.trains import service as trains

router = APIRouter(prefix="/places", tags=["Places"])
UNAVAILABLE = {503: {"description": "Place catalog unavailable"}}


def _catalog(request: Request) -> dict[str, service.Place]:
    try:
        return service.all_places(request.app.state.settings.content_dir)
    except service.PlacesError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "places unavailable") from None


def _summary(place: service.Place) -> dict:
    return {**asdict(place), "aliases": list(place.aliases)}


@router.get("", response_model=PlaceList, summary="All places for the map", responses=UNAVAILABLE)
def list_places(
    request: Request, kind: Annotated[Kind | None, Query(description="이 종류만")] = None
) -> PlaceList:
    places = [p for p in _catalog(request).values() if kind is None or p.kind == kind]
    return PlaceList(places=[PlaceSummary(**_summary(p)) for p in places])


@router.get(
    "/{place_id}",
    response_model=PlaceDetail,
    summary="One place with its description",
    responses={404: {"description": "Unknown place id"}, **UNAVAILABLE},
)
def get_place(place_id: str, request: Request) -> PlaceDetail:
    catalog = _catalog(request)
    from neareast_api.db.session import contentSession
    from neareast_api.modules.places.identity import canonicalPlaceId
    try:
        place_id = canonicalPlaceId(contentSession(), place_id)
    except HTTPException:
        raise HTTPException(404, "place not found") from None
    place = catalog.get(place_id)
    if place is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "place not found")
    content_dir = request.app.state.settings.content_dir
    try:
        documents = retrieval.load_content(content_dir).docs
        doc = documents.get(place.id) or next(
            (entry for entry in documents.values() if place.id in entry.places), None
        )
    except retrieval.ContentError:
        doc = None  # the catalog still answers without descriptions
    timetable = trains.load_or_none(content_dir)
    has_trains = bool(timetable and any(
        s.get("place_id") == place.id for t in timetable.trains for s in t["stops"]))
    return PlaceDetail(
        **_summary(place),
        summary=doc.summary if doc else None,
        description=doc.body if doc else None,
        source=doc.id if doc else None,
        has_trains=has_trains,
    )
