from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from neareast_api.db.session import getSession
from neareast_api.modules.planner.repository import loadPlannerLocations
from neareast_api.modules.planner.routing import routePlacesFromRecords

router = APIRouter(tags=["Planning"])


class PlanningPlaceDto(BaseModel):
    id: str
    name: str
    canDepart: bool
    canVisit: bool
    reason: str | None


class PlanningOptionsDto(BaseModel):
    mode: str = "walk"
    maxDestinations: int = 5
    places: list[PlanningPlaceDto]


def buildPlanningOptions(records):
    eligible, missingCoordinates = routePlacesFromRecords(records)
    places = []
    for record in records:
        canDepart = record["id"] in eligible
        hasStay = any(record.get(field) is not None for field in (
            "observed_stay_minutes", "default_stay_minutes",
        ))
        reason = None
        if record["id"] in missingCoordinates:
            reason = "coordinates_unverified"
        elif not canDepart:
            reason = "public_access_unverified"
        elif not hasStay:
            reason = "stay_duration_missing"
        places.append(PlanningPlaceDto(
            id=record["id"], name=record["name"], canDepart=canDepart,
            canVisit=canDepart and hasStay, reason=reason,
        ))
    return PlanningOptionsDto(places=places)


@router.get("/planning-options", response_model=PlanningOptionsDto)
def getPlanningOptions(session: Session = Depends(getSession)):
    # Expose the same eligibility used by generation; coordinates alone never grant access.
    return buildPlanningOptions(loadPlannerLocations(session))
