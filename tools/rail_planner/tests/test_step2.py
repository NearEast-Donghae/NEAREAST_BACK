import json
from urllib.parse import quote

import httpx
import pytest

from backend.app.services import public_data
from backend.app.services.routing import (
    RouteError,
    RoutePlace,
    RouteResult,
    buildTravelTimeMatrix,
    loadRoutePlaces,
)
from backend.app.services.tmap import TmapWalkingRouter, parseWalkingRoute


def test_load_route_places(tmp_path):
    template = {
        "name": "Station",
        "latitude": 37.5,
        "longitude": 129.1,
        "crs": "EPSG:4326",
        "coordinate_ready": True,
        "routing_ready": True,
    }
    locationPath = tmp_path / "locations.json"
    locationPath.write_text(json.dumps([
        dict(template, id="available"),
        dict(template, id="missing", latitude=None),
        dict(template, id="invalid", longitude=181),
        dict(template, id="unverified", coordinate_ready=False),
        dict(template, id="access_unknown", routing_ready=False),
    ]), encoding="utf-8")

    places, missing = loadRoutePlaces(locationPath)
    assert places == {"available": RoutePlace("available", "Station", (129.1, 37.5))}
    assert set(missing) == {"missing", "invalid", "unverified"}


@pytest.fixture
def routePlaces():
    return {
        "station": RoutePlace("station", "동해역", (129.12, 37.49)),
        "terminal": RoutePlace("terminal", "Terminal", (129.11, 37.51)),
    }


def test_walk_request_and_geometry(tmp_path, monkeypatch, routePlaces):
    monkeypatch.setattr(public_data, "ROOT", tmp_path)
    monkeypatch.delenv("TMAP_API_KEY", raising=False)
    (tmp_path / ".env").write_text("TMAP_API_KEY='dummy-key'\n", encoding="utf-8")
    requests = []
    coordinates = [[129.12, 37.49], [129.11, 37.51]]

    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1"
        assert request.headers["appKey"] == "dummy-key"
        assert json.loads(request.content) == {
            "startX": 129.12, "startY": 37.49, "endX": 129.11, "endY": 37.51,
            "startName": quote("동해역"), "endName": "Terminal",
            "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
        }
        return httpx.Response(200, json={"features": [
            {"properties": {"totalDistance": 1800, "totalTime": 1200},
             "geometry": {"type": "Point", "coordinates": coordinates[0]}},
            {"geometry": {"type": "LineString", "coordinates": coordinates}},
        ]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = TmapWalkingRouter(routePlaces, client).getRoute("station", "terminal")
    assert len(requests) == 1
    assert route == RouteResult("station", "terminal", "walk", "tmap", 1200, 1800, (
        ((129.12, 37.49), (129.11, 37.51)),
    ))


def test_missing_key_stops_request(tmp_path, monkeypatch, routePlaces):
    monkeypatch.setattr(public_data, "ROOT", tmp_path)
    monkeypatch.delenv("TMAP_API_KEY", raising=False)

    def unexpectedRequest(request):
        pytest.fail("Request made without a key")

    with httpx.Client(transport=httpx.MockTransport(unexpectedRequest)) as client:
        with pytest.raises(RouteError, match="missing_tmap_key"):
            TmapWalkingRouter(routePlaces, client).getRoute("station", "terminal")


@pytest.mark.parametrize(("status", "payload", "reason", "apiCode"), [
    (204, None, "no_route", None),
    (400, {"error": {"code": 3002}}, "no_route", "3002"),
    (403, {"error": {"code": "1100", "message": "sensitive body"}}, "tmap_api_error", "1100"),
    (200, {"features": [{"properties": {"totalDistance": 100, "totalTime": 40},
                         "geometry": {"type": "LineString", "coordinates": [[200, 37], [129, 37]]}}]},
     "invalid_tmap_response", None),
])
def test_tmap_response_errors(status, payload, reason, apiCode):
    with pytest.raises(RouteError, match=f"^{reason}$") as raised:
        parseWalkingRoute(httpx.Response(status, json=payload), "station", "terminal")
    assert raised.value.httpStatus == status
    assert raised.value.apiCode == apiCode


def test_directed_travel_time_matrix():
    placeIds = ("station", "terminal", "mukho")
    routes = [
        RouteResult("station", "terminal", "walk", "mock", 600, None),
        RouteResult("terminal", "station", "walk", "mock", 720, None),
    ]
    matrix = buildTravelTimeMatrix(placeIds, routes, mode="walk")
    assert matrix["placeIds"] == placeIds
    assert matrix["durationSeconds"] == {
        "station": {"station": 0, "terminal": 600, "mukho": None},
        "terminal": {"station": 720, "terminal": 0, "mukho": None},
        "mukho": {"station": None, "terminal": None, "mukho": 0},
    }
    with pytest.raises(ValueError, match="Duplicate directed matrix route"):
        buildTravelTimeMatrix(placeIds, routes + routes, mode="walk")
