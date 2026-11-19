import json

from neareast_api.modules.planner.itinerary import generateItinerary
from neareast_api.modules.planner.route_optimizer import optimizeWalkingRoute


def schedule(startTime, budget, **options):
    locations = [
        {"id": "donghae", "name": "동해역", "default_stay_minutes": 20},
        {"id": "mukho", "name": "묵호역", "default_stay_minutes": 20},
        {"id": "terminal", "name": "동해시종합버스터미널", "default_stay_minutes": 15},
    ]
    ids = [place["id"] for place in locations]
    durations = ((0, 600, 720), (840, 0, 960), (1080, 1200, 0))
    matrix = {"mode": "walk", "source": "mock", "placeIds": ids,
              "durationSeconds": {placeId: dict(zip(ids, row, strict=True)) for placeId, row in zip(ids, durations, strict=True)}}
    plan = optimizeWalkingRoute(ids[0], ids[1:], budget, matrix, locations, **options)
    return generateItinerary(ids[0], startTime, plan, locations)


def test_normal_schedule():
    result = schedule("2026-11-19T09:00:00", 4200)
    assert json.loads(json.dumps(result)) == result
    assert result["status"] == "feasible" and result["travelDataStatus"] == "simulated"
    origin, station, terminal = result["visits"]
    assert origin["nextDeparture"]["display"].endswith("09:00")
    assert station["arrival"]["display"].endswith("09:10")
    assert station["nextDeparture"]["display"].endswith("09:30")
    assert station["staySource"] == "service_estimate"
    assert terminal["arrival"]["display"].endswith("09:46")
    assert terminal["visitEnd"]["display"].endswith("10:01") and terminal["nextDeparture"] is None
    assert result["remainingSeconds"] == 540 and not result["conflicts"]


def test_over_budget():
    result = schedule("2026-11-19T09:00", 2400)
    assert result["status"] == "infeasible" and result["overrunSeconds"] == 1260
    assert result["visitOrder"] == ["donghae", "mukho", "terminal"]
    assert [(item["activity"], item["seconds"]) for item in result["conflicts"]] == [
        ("travel", 360), ("stay", 900),
    ]


def test_midnight():
    result = schedule("2026-11-19T23:30", 3660)
    assert result["legs"][1]["departure"]["display"] == "2026-11-20 00:00"
    assert result["endTime"]["iso"] == "2026-11-20T00:31:00+09:00"


def test_second_precision():
    for budget, remaining, overrun in [(3660, 0, 1), (3662, 1, 0)]:
        result = schedule("2026-11-19T09:00:59", budget, staySeconds={"terminal": 901})
        assert result["endTime"]["iso"] == "2026-11-19T10:02:00+09:00"
        assert (result["remainingSeconds"], result["overrunSeconds"]) == (remaining, overrun)
