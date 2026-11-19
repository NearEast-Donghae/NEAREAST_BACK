"""HTTP boundary for the day planner. Everything is computed from the request body."""

from fastapi import APIRouter, HTTPException, Request, status

from neareast_api.modules.places import service as places
from neareast_api.modules.plans import service
from neareast_api.modules.plans.schemas import (
    CompareRequest,
    Comparison,
    Plan,
    PlanRequest,
    Variants,
)
from neareast_api.modules.trains import service as trains

router = APIRouter(prefix="/plans", tags=["Plans"])
RESPONSES = {
    422: {"description": "Invalid request, more than 12 items, or unknown start/end place"},
    503: {"description": "Timetable snapshot or place catalog unavailable"},
}


def _run(request: Request, compute, *bodies):
    content_dir = request.app.state.settings.content_dir
    try:
        catalog = places.all_places(content_dir)
        timetable = trains.load(content_dir)
    except places.PlacesError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "places unavailable") from None
    except trains.TimetableUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "timetable unavailable") from None
    try:
        return compute(*bodies, catalog, timetable)
    except service.UnknownPlace as exc:
        raise HTTPException(422,
                            f"unknown start/end place: {exc}") from None


@router.post("", response_model=Plan, responses=RESPONSES,
             summary="Weave the pinned trains and places into one day")
def make_plan(body: PlanRequest, request: Request) -> Plan:
    return _run(request, service.make_plan, body)


@router.post("/variants", response_model=Variants, responses=RESPONSES,
             summary="The same request as train-first, visit-first and balanced plans")
def make_variants(body: PlanRequest, request: Request) -> Variants:
    return _run(request, service.variants, body)


@router.post("/compare", response_model=Comparison, responses=RESPONSES,
             summary="Two requests side by side: what the change gains and loses")
def compare_plans(body: CompareRequest, request: Request) -> Comparison:
    return _run(request, service.compare, body.base, body.changed)
