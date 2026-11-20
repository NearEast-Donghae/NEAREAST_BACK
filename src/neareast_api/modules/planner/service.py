import math
from datetime import datetime
from itertools import pairwise, permutations

import httpx
from fastapi import HTTPException

from neareast_api.modules.itineraries.dto import ItineraryCreateDto
from neareast_api.modules.planner.itinerary import KST, generateItinerary
from neareast_api.modules.planner.repository import loadPlannerLocations
from neareast_api.modules.planner.route_optimizer import (
    optimizeWalkingRoute,
    walkingMatrixFromRoutes,
)
from neareast_api.modules.planner.routing import RouteError, routePlacesFromRecords
from neareast_api.modules.planner.tmap import TmapWalkingRouter


def checkConditions(conditions):
    required = ("travelDate", "startTime", "endTime", "departurePlaceId")
    missing = [field for field in required if not conditions.get(field)]
    if missing:
        raise HTTPException(422, {"missingConditions": missing})
    unsupported = [field for field in ("selectedTrainIds", "preferredTrainTypes")
                   if conditions.get(field)]
    if conditions.get("endDate") not in (None, conditions["travelDate"]):
        unsupported.append("endDate")
    if unsupported:
        raise HTTPException(422, {"unsupportedWalkingConditions": unsupported})
    originId = conditions["departurePlaceId"]
    visitIds = list(dict.fromkeys(identifier for identifier in conditions.get("requiredPlaceIds", [])
                                 if identifier != originId))
    arrivalId = conditions.get("arrivalPlaceId")
    if arrivalId and arrivalId != originId and arrivalId not in visitIds:
        visitIds.append(arrivalId)
    if not visitIds or len(visitIds) > 5:
        raise HTTPException(422, "Walking planning requires one to five destinations")
    start = datetime.fromisoformat(f"{conditions['travelDate']}T{conditions['startTime']}").replace(tzinfo=KST)
    end = datetime.fromisoformat(f"{conditions['travelDate']}T{conditions['endTime']}").replace(tzinfo=KST)
    return originId, visitIds, start, int((end - start).total_seconds())


def fetchRoutes(identifiers, places, apiKey, routeProvider):
    with httpx.Client(timeout=10) as client:
        provider = routeProvider or TmapWalkingRouter(places, client, apiKey)
        routes = []
        for origin in identifiers:
            for destination in identifiers:
                if origin == destination:
                    continue
                try:
                    routes.append(provider.getRoute(origin, destination))
                except RouteError as exception:
                    if exception.code != "no_route":
                        raise HTTPException(502, {"routeProviderError": exception.code}) from None
    return routes


def optimizePlan(originId, visitIds, available, matrix, records, arrivalId):
    optimization = optimizeWalkingRoute(originId, visitIds, available, matrix, records)
    if arrivalId:
        candidates = []
        middleIds = [identifier for identifier in visitIds if identifier != arrivalId]
        for order in permutations(middleIds):
            suffix = [arrivalId] if arrivalId != originId else []
            routeOrder = [originId, *order, *suffix]
            edges = list(pairwise(routeOrder))
            if arrivalId == originId:
                edges.append((routeOrder[-1], originId))
            if any(matrix["durationSeconds"][left][right] is None for left, right in edges):
                continue
            candidates.append((sum(matrix["durationSeconds"][left][right]
                                   for left, right in edges), routeOrder, edges))
        if not candidates:
            raise HTTPException(422, "No walking route to the requested arrival place")
        _, routeOrder, edges = min(candidates, key=lambda entry: entry[0])
        permitted = set(pairwise(routeOrder))
        constrained = {**matrix, "durationSeconds": {
            left: {right: duration if left == right or (left, right) in permitted else None
                   for right, duration in destinations.items()}
            for left, destinations in matrix["durationSeconds"].items()
        }}
        optimization = optimizeWalkingRoute(originId, visitIds, available, constrained, records)
        if arrivalId == originId:
            previousId = optimization["visitOrder"][-1]
            duration = matrix["durationSeconds"][previousId][originId]
            optimization["visitOrder"].append(originId)
            optimization["legs"].append({"originId": previousId, "destinationId": originId,
                                         "durationSeconds": duration})
            optimization["stays"][originId] = {"seconds": 0, "source": "arrival"}
            optimization["totalTravelSeconds"] += duration
            optimization["totalSeconds"] += duration
            optimization["overrunSeconds"] = max(0, optimization["totalSeconds"] - available)
            optimization["withinBudget"] = optimization["overrunSeconds"] == 0
            optimization["status"] = "feasible" if optimization["withinBudget"] else "infeasible"
            optimization["reason"] = None if optimization["withinBudget"] else "time_budget_exceeded"
    return optimization


def serializePlan(plan, conditions, routes):
    byPair = {(route.originId, route.destinationId): route for route in routes}
    visits = plan["visits"]
    proposal = None
    if plan["status"] == "feasible":
        proposal = ItineraryCreateDto(
            source="planner", title="동해 도보 여행", travelDate=conditions["travelDate"],
            travelDateLabel=conditions["travelDate"],
            timeRange=f"{conditions['startTime']}–{plan['endTime']['display'][-5:]}",
            activities=[visit["name"] for visit in visits[1:]],
            timeline=[{
                "time": visit["arrival"]["display"][-5:], "description": visit["name"],
                "type": "start" if index == 0 else "visit", "placeId": visit["placeId"],
                "startsAt": visit["arrival"]["iso"],
                "endsAt": (visit["visitEnd"] or visit["arrival"])["iso"],
            } for index, visit in enumerate(visits)],
            legs=[{
                "fromPlaceId": leg["originId"], "toPlaceId": leg["destinationId"],
                "mode": "walk", "durationMinutes": math.ceil(leg["durationSeconds"] / 60),
                "distanceMeters": byPair[leg["originId"], leg["destinationId"]].distanceMeters,
                "coordinates": [point for segment in byPair[
                    leg["originId"], leg["destinationId"]].geometry for point in segment],
                "source": "TMAP pedestrian route", "certainty": "estimated",
            } for leg in plan["legs"]],
        ).model_dump(mode="json")
    return proposal


class PlannerService:
    @staticmethod
    def generate(session, conditions, settings, routeProvider=None):
        originId, visitIds, start, available = checkConditions(conditions)
        records = loadPlannerLocations(session)
        places, _ = routePlacesFromRecords(records)
        identifiers = [originId, *visitIds]
        unavailable = [identifier for identifier in identifiers if identifier not in places]
        if unavailable:
            raise HTTPException(422, {"unavailablePlaces": unavailable,
                                      "reason": "Coordinates or verified public access missing"})
        catalog = {record["id"]: record for record in records}
        missingStay = [identifier for identifier in visitIds if all(
            catalog[identifier].get(field) is None
            for field in ("observed_stay_minutes", "default_stay_minutes")
        )]
        if missingStay:
            raise HTTPException(422, {"missingStayDurations": missingStay})
        apiKey = settings.tmapApiKey.get_secret_value()
        if routeProvider is None and not apiKey:
            raise HTTPException(503, "TMAP API key is not configured")
        routes = fetchRoutes(identifiers, places, apiKey, routeProvider)
        matrix = walkingMatrixFromRoutes(identifiers, routes, source="tmap")
        optimization = optimizePlan(originId, visitIds, available, matrix, records,
                                    conditions.get("arrivalPlaceId"))
        plan = generateItinerary(originId, start, optimization, records)
        walkingLimit = conditions.get("maxWalkingMinutes")
        if walkingLimit is not None and (plan["totalTravelSeconds"] or 0) > walkingLimit * 60:
            plan.update(status="infeasible", reason="walking_limit_exceeded", withinBudget=False)
        return {"status": plan["status"], "plan": plan,
                "itinerary": serializePlan(plan, conditions, routes)}
