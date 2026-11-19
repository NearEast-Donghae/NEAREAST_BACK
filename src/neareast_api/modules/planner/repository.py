from sqlalchemy import select

from neareast_api.core.geography import validCoordinates
from neareast_api.db.entities import PlaceEntity
from neareast_api.db.planner_entities import PlannerDatasetEntity, PlannerLocationEntity


def loadPlannerLocations(session):
    datasetId = session.scalar(select(PlannerDatasetEntity.id).order_by(
        PlannerDatasetEntity.importedAt.desc(), PlannerDatasetEntity.id,
    ).limit(1))
    rows = session.scalars(select(PlannerLocationEntity).where(
        PlannerLocationEntity.datasetId == datasetId,
    ).order_by(PlannerLocationEntity.position))
    records = {}
    for row in rows:
        place = session.get(PlaceEntity, row.canonicalPlaceId) if row.canonicalPlaceId else None
        if place is None:
            continue
        # Access eligibility and coordinates are separate; a coordinate never grants access.
        allowed = row.visitAllowed is True and row.routingReady
        record = {
            **row.payload, "id": place.id, "name": place.name,
            "latitude": place.lat, "longitude": place.lng, "crs": "EPSG:4326",
            "coordinate_ready": validCoordinates(place.lat, place.lng),
            "routing_ready": allowed,
        }
        previous = records.get(place.id)
        if previous is None or (record["routing_ready"] and not previous["routing_ready"]):
            records[place.id] = record
    return list(records.values())
