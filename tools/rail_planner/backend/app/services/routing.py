import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.app.config import DATA

Coordinate = tuple[float, float]  # WGS84 (longitude, latitude)
TravelMode = Literal["walk", "transit"]


@dataclass(frozen=True)
class RoutePlace:
    placeId: str
    name: str
    coordinate: Coordinate


@dataclass(frozen=True)
class RouteLeg:
    mode: Literal["walk", "bus", "subway", "rail"]
    durationSeconds: int
    label: str | None = None


@dataclass(frozen=True)
class RouteResult:
    originId: str
    destinationId: str
    mode: TravelMode
    provider: str
    durationSeconds: int
    distanceMeters: int | None
    geometry: tuple[tuple[Coordinate, ...], ...] = ()
    legs: tuple[RouteLeg, ...] = ()


class RouteError(RuntimeError):
    def __init__(self, code: str, *, httpStatus: int | None = None, apiCode: str | None = None):
        super().__init__(code)
        self.code = code
        self.httpStatus = httpStatus
        self.apiCode = apiCode


def validCoordinate(longitude, latitude):
    return (
        type(longitude) in (int, float)
        and type(latitude) in (int, float)
        and -180 <= longitude <= 180
        and -90 <= latitude <= 90
    )


def loadRoutePlaces(path: Path = DATA / "processed" / "locations.json"):
    places = {}
    missing = {}
    for record in json.loads(path.read_text(encoding="utf-8")):
        placeId = record["id"]
        longitude, latitude = record.get("longitude"), record.get("latitude")
        if (
            record.get("crs") != "EPSG:4326"
            or record.get("coordinate_ready") is not True
            or not validCoordinate(longitude, latitude)
        ):
            missing[placeId] = "missing_or_unverified_coordinates"
        elif record.get("routing_ready") is True:
            places[placeId] = RoutePlace(placeId, record["name"], (longitude, latitude))
    return places, missing


def buildTravelTimeMatrix(placeIds, routes, *, mode: TravelMode):
    """Build a directed matrix in memory from supplied results without calling a provider."""
    orderedIds = tuple(placeIds)
    if len(set(orderedIds)) != len(orderedIds):
        raise ValueError("Duplicate matrix place ID")
    times = {
        originId: {
            destinationId: 0 if originId == destinationId else None
            for destinationId in orderedIds
        }
        for originId in orderedIds
    }
    for route in routes:
        if (
            route.mode != mode
            or route.originId not in times
            or route.destinationId not in times
            or route.originId == route.destinationId
            or type(route.durationSeconds) is not int
            or route.durationSeconds < 0
        ):
            raise ValueError("Invalid matrix route")
        if times[route.originId][route.destinationId] is not None:
            raise ValueError("Duplicate directed matrix route")
        times[route.originId][route.destinationId] = route.durationSeconds
    return {"mode": mode, "placeIds": orderedIds, "durationSeconds": times}
