import json

from neareast_api.modules.planner.route_optimizer import (
    optimizeWalkingRoute,
    walkingMatrixFromRoutes,
)
from neareast_api.modules.planner.routing import RouteResult


def plan(visitIds, budget, edges, **options):
    placeIds = ["donghae", "mukho", "terminal"]
    locations = [{"id": placeId, "default_stay_minutes": minutes}
                 for placeId, minutes in zip(placeIds, [20, 20, 15], strict=True)]
    routes = [RouteResult(start, end, "walk", "mock", seconds, None)
              for start, end, seconds in edges]
    matrix = walkingMatrixFromRoutes(placeIds, routes, source="mock")
    return optimizeWalkingRoute("donghae", visitIds, budget, matrix, locations, **options)


EDGES = [
    ("donghae", "mukho", 600), ("donghae", "terminal", 720),
    ("mukho", "terminal", 960), ("terminal", "mukho", 1200),
]


def test_normal_route():
    result = plan(["terminal", "mukho"], 3660, EDGES)
    assert json.loads(json.dumps(result)) == result
    assert result["visitOrder"] == ["donghae", "mukho", "terminal"]
    assert [leg["durationSeconds"] for leg in result["legs"]] == [600, 960]
    assert (result["totalTravelSeconds"], result["totalStaySeconds"], result["totalSeconds"]) == (1560, 2100, 3660)
    assert result["withinBudget"] and result["travelDataStatus"] == "simulated"
    assert result["stays"]["mukho"]["source"] == "service_estimate"


def test_time_exceeded():
    result = plan(["mukho", "terminal"], 3600, EDGES)
    assert result["status"] == "infeasible" and result["overrunSeconds"] == 60
    assert result["visitOrder"] == ["donghae", "mukho", "terminal"]


def test_duplicate_destinations():
    result = plan(["mukho", "donghae", "mukho"], 600, EDGES, staySeconds={"mukho": 0})
    assert result["visitOrder"] == ["donghae", "mukho"]
    assert result["totalSeconds"] == 600
    assert result["stays"]["mukho"]["source"] == "user_estimate"


def test_unavailable_direction():
    visits = ["mukho", "terminal"]
    remaining = [edge for edge in EDGES if edge[:2] != ("mukho", "terminal")]
    assert plan(visits, 5000, remaining)["visitOrder"] == ["donghae", "terminal", "mukho"]
    blocked = plan(visits, 5000, remaining[:2])
    assert blocked["reason"] == "no_complete_route"
    assert blocked["visitOrder"] is None and blocked["overrunSeconds"] is None
