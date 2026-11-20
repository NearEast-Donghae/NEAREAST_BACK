from fastapi import HTTPException
from sqlalchemy import select

from neareast_api.db.entities import PlaceEntity
from neareast_api.db.planner_entities import PlannerLocationEntity


def canonicalPlaceId(session, identifier):
    existing = session.get(PlaceEntity, identifier)
    if existing is not None:
        return (existing.operatingInfo or {}).get("canonicalPlaceId", identifier)
    matches = set(session.scalars(select(PlannerLocationEntity.canonicalPlaceId).where(
        PlannerLocationEntity.id == identifier,
        PlannerLocationEntity.canonicalPlaceId.is_not(None),
    )))
    if len(matches) != 1:
        raise HTTPException(422, {"unknownOrAmbiguousPlaceId": identifier})
    return matches.pop()
