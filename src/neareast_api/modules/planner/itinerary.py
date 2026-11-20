from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def timeStamp(moment):
    return {"iso": moment.isoformat(), "display": moment.strftime("%Y-%m-%d %H:%M")}


def generateItinerary(originId, startTime, optimization, locations):
    """Schedule a STEP 3 result in Korea time. Naive start times are local."""
    start = datetime.fromisoformat(startTime) if isinstance(startTime, str) else startTime
    if start.microsecond:
        raise ValueError("Start time must have whole-second precision")
    start = start.replace(tzinfo=KST) if start.tzinfo is None else start.astimezone(KST)
    deadline = start + timedelta(seconds=optimization["availableSeconds"])
    order = optimization["visitOrder"]
    names = {place["id"]: place["name"] for place in locations}
    result = {key: optimization[key] for key in (
        "status", "reason", "withinBudget", "availableSeconds", "overrunSeconds",
        "totalSeconds", "totalTravelSeconds", "totalStaySeconds", "visitOrder",
        "travelSource", "travelDataStatus", "mode",
    )}
    result.update(originId=originId, startTime=timeStamp(start), deadline=timeStamp(deadline),
                  endTime=None, remainingSeconds=None, visits=[], legs=[], conflicts=[])
    if order is None:
        return result
    if not order or order[0] != originId:
        raise ValueError("Origin must match the STEP 3 visit order")

    def recordConflict(activity, startMoment, endMoment, **identifiers):
        conflictStart = max(startMoment, deadline)
        if endMoment > conflictStart:
            result["conflicts"].append({
                "activity": activity, **identifiers, "start": timeStamp(conflictStart),
                "end": timeStamp(endMoment),
                "seconds": int((endMoment - conflictStart).total_seconds()),
            })

    current = start
    result["visits"].append({
        "placeId": originId, "name": names[originId], "arrival": timeStamp(start),
        "visitStart": None, "visitEnd": None, "staySeconds": 0, "staySource": None,
        "nextDeparture": timeStamp(start) if len(order) > 1 else None,
    })
    for previousId, placeId, leg in zip(order[:-1], order[1:], optimization["legs"], strict=True):
        if (leg["originId"], leg["destinationId"]) != (previousId, placeId):
            raise ValueError("Leg must match the STEP 3 visit order")
        arrival = current + timedelta(seconds=leg["durationSeconds"])
        result["legs"].append({
            **leg, "mode": result["mode"], "departure": timeStamp(current), "arrival": timeStamp(arrival),
        })
        recordConflict("travel", current, arrival, originId=previousId, destinationId=placeId)
        stay = optimization["stays"][placeId]
        current = arrival + timedelta(seconds=stay["seconds"])
        result["visits"].append({
            "placeId": placeId, "name": names[placeId], "arrival": timeStamp(arrival),
            "visitStart": timeStamp(arrival), "visitEnd": timeStamp(current),
            "staySeconds": stay["seconds"], "staySource": stay["source"],
            "nextDeparture": timeStamp(current) if placeId != order[-1] else None,
        })
        recordConflict("stay", arrival, current, placeId=placeId)
    result["endTime"] = timeStamp(current)
    result["remainingSeconds"] = max(0, int((deadline - current).total_seconds()))
    return result
