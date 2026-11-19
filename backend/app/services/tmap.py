from urllib.parse import quote

import httpx

from backend.app.services.public_data import DataApiError, readSettings
from backend.app.services.routing import RouteError, RoutePlace, RouteResult, validCoordinate

TMAP_ENDPOINT = "https://apis.openapi.sk.com/tmap/routes/pedestrian"
TMAP_KEY_NAME = "TMAP_API_KEY"
TMAP_COORDINATE_TYPE = "WGS84GEO"


def readTmapKey():
    try:
        key = readSettings({TMAP_KEY_NAME})[TMAP_KEY_NAME]
    except DataApiError:
        raise RouteError("invalid_tmap_configuration") from None
    if not key:
        raise RouteError("missing_tmap_key")
    if not key.isascii() or any(not ("!" <= character <= "~") for character in key):
        raise RouteError("invalid_tmap_configuration")
    return key


def parseWalkingRoute(response: httpx.Response, originId: str, destinationId: str):
    status = response.status_code
    if status == 204:
        raise RouteError("no_route", httpStatus=status)
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    rawCode = error.get("code") if isinstance(error, dict) else None
    apiCode = str(rawCode) if type(rawCode) is int else rawCode
    if not isinstance(apiCode, str) or not (apiCode.isascii() and apiCode.isdigit() and len(apiCode) == 4):
        apiCode = None
    if not response.is_success or error is not None:
        reason = "no_route" if apiCode == "3002" else "tmap_api_error"
        raise RouteError(reason, httpStatus=status, apiCode=apiCode)

    features = payload.get("features") if isinstance(payload, dict) else None
    if features == []:
        raise RouteError("no_route", httpStatus=status)
    if not isinstance(features, list) or not all(isinstance(feature, dict) for feature in features):
        raise RouteError("invalid_tmap_response", httpStatus=status)
    properties = features[0].get("properties")
    distance = properties.get("totalDistance") if isinstance(properties, dict) else None
    duration = properties.get("totalTime") if isinstance(properties, dict) else None
    if any(type(metric) is not int or metric < 0 for metric in (distance, duration)):
        raise RouteError("invalid_tmap_response", httpStatus=status)

    segments = []
    for feature in features:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise RouteError("invalid_tmap_response", httpStatus=status)
        if geometry.get("type") != "LineString":
            continue
        points = geometry.get("coordinates")
        if not isinstance(points, list) or len(points) < 2 or not all(
            isinstance(point, list) and len(point) == 2 and validCoordinate(*point)
            for point in points
        ):
            raise RouteError("invalid_tmap_response", httpStatus=status)
        segments.append(tuple((longitude, latitude) for longitude, latitude in points))
    if not segments:
        raise RouteError("invalid_tmap_response", httpStatus=status)
    return RouteResult(originId, destinationId, "walk", "tmap", duration, distance, tuple(segments))


class TmapWalkingRouter:
    def __init__(self, places: dict[str, RoutePlace], client: httpx.Client):
        self.places = places
        self.client = client

    def getRoute(self, originId: str, destinationId: str):
        if originId == destinationId:
            raise RouteError("same_place")
        try:
            origin, destination = self.places[originId], self.places[destinationId]
        except KeyError:
            raise RouteError("unknown_or_ineligible_place") from None
        key = readTmapKey()
        try:
            response = self.client.post(
                TMAP_ENDPOINT,
                params={"version": 1},
                headers={"appKey": key, "Accept": "application/json"},
                json={
                    "startX": origin.coordinate[0],
                    "startY": origin.coordinate[1],
                    "endX": destination.coordinate[0],
                    "endY": destination.coordinate[1],
                    "startName": quote(origin.name, safe=""),
                    "endName": quote(destination.name, safe=""),
                    "reqCoordType": TMAP_COORDINATE_TYPE,
                    "resCoordType": TMAP_COORDINATE_TYPE,
                },
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise RouteError("tmap_timeout") from None
        except httpx.RequestError:
            raise RouteError("tmap_connection_error") from None
        return parseWalkingRoute(response, originId, destinationId)
