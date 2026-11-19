from itertools import pairwise, permutations

from backend.app.services.routing import buildTravelTimeMatrix


def requireDuration(value):
    if type(value) is not int or value < 0:
        raise ValueError("Duration must be a non-negative integer")
    return value


def walkingMatrixFromRoutes(placeIds, routes, *, source):
    """Accept STEP 2 RouteResult objects from one provider, without making requests."""
    routes = tuple(routes)
    if source not in ("tmap", "mock") or any(route.provider != source for route in routes):
        raise ValueError("Expected a single matching walking-time source")
    return {"source": source, **buildTravelTimeMatrix(placeIds, routes, mode="walk")}


def resolveStay(place, staySeconds):
    placeId = place["id"]
    if placeId in staySeconds:
        return requireDuration(staySeconds[placeId]), "user_estimate"
    for field, source in (
        ("observed_stay_minutes", "observed"),
        ("default_stay_minutes", "service_estimate"),
    ):
        if place.get(field) is not None:
            return 60 * requireDuration(place[field]), source
    raise ValueError(f"Missing stay duration for {placeId}")


def optimizeWalkingRoute(originId, visitIds, availableSeconds, matrix, locations, *, staySeconds=None):
    """Optimize an open walk. Only destinations incur a stay. Ties retain input order."""
    requireDuration(availableSeconds)
    if matrix.get("mode") != "walk" or matrix.get("source") not in ("mock", "tmap"):
        raise ValueError("A walking matrix with explicit mock or tmap provenance is required")
    destinations = tuple(dict.fromkeys(placeId for placeId in visitIds if placeId != originId))
    if len(destinations) > 5:
        raise ValueError("At most five destinations are supported")
    catalog = {place["id"]: place for place in locations}
    for placeId in (originId, *destinations):
        if placeId not in catalog or placeId not in matrix["placeIds"]:
            raise ValueError(f"Unknown place or missing matrix ID: {placeId}")
    stays = {placeId: resolveStay(catalog[placeId], staySeconds or {}) for placeId in destinations}
    totalStay = sum(seconds for seconds, _ in stays.values())
    times = matrix["durationSeconds"]
    bestOrder = None
    bestTravel = None
    for order in permutations(destinations):
        candidate = (originId, *order)
        durations = [times.get(start, {}).get(end) for start, end in pairwise(candidate)]
        if None in durations:
            continue
        travel = sum(requireDuration(duration) for duration in durations)
        if bestTravel is None or travel < bestTravel:
            bestOrder, bestTravel = candidate, travel

    result = {
        "status": "infeasible", "reason": "no_complete_route", "visitOrder": None,
        "legs": [], "totalTravelSeconds": bestTravel, "totalStaySeconds": totalStay,
        "totalSeconds": None, "availableSeconds": availableSeconds, "withinBudget": False,
        "overrunSeconds": None, "mode": "walk", "travelSource": matrix["source"],
        "travelDataStatus": "simulated" if matrix["source"] == "mock" else "provider_reported",
        "stays": {placeId: {"seconds": seconds, "source": source}
                  for placeId, (seconds, source) in stays.items()},
    }
    if bestOrder is None:
        return result
    total = bestTravel + totalStay
    withinBudget = total <= availableSeconds
    return {
        **result,
        "status": "feasible" if withinBudget else "infeasible",
        "reason": None if withinBudget else "time_budget_exceeded",
        "visitOrder": list(bestOrder),
        "legs": [{"originId": start, "destinationId": end, "durationSeconds": times[start][end]}
                 for start, end in pairwise(bestOrder)],
        "totalSeconds": total, "withinBudget": withinBudget,
        "overrunSeconds": max(0, total - availableSeconds),
    }
