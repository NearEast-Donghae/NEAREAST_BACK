"""Choices for the first screen of the AI planner, computed from the timetable snapshot.

No model is involved: which stations have a train to the chosen station, how many a day
and of what kind are counted from the stops of each train.
"""

from collections import Counter

from neareast_api.modules.onboarding.dto import (
    CompanionOptionDto,
    DestinationOptionDto,
    OnboardingOptionsDto,
    OnboardingStationDto,
    OriginOptionDto,
    OriginsDto,
)
from neareast_api.modules.recommend.companions import RULES
from neareast_api.modules.trains.service import Timetable

# The three stations the first question names; other stations with passenger trains
# follow under "그 외".
FEATURED_DESTINATIONS = ("station_mukho", "station_donghae", "station_chuam")
FEATURED_ORIGINS = 4
DAY = 24 * 60


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _boards(stop: dict) -> bool:
    """Passengers can get on or off here (a passing time is not a stop)."""
    return "pass" not in stop and not stop["stop_type"].startswith("통과")


def _types(timetable: Timetable, numbers) -> list[str]:
    """Train types of those trains, the most frequent first. Unknown types are left out."""
    counted = Counter(t for no in numbers if (t := timetable.types.get(no)))
    return [name for name, _ in sorted(counted.items(), key=lambda c: (-c[1], c[0]))]


def _label(count: int, types: list[str]) -> str:
    return f"하루 {count}편" + (f" · {', '.join(types)}" if types else "")


def arrivals(timetable: Timetable, stationId: str) -> list[str]:
    """Numbers of the trains one can get off at the station."""
    return [train["no"] for train in timetable.trains
            if any(s.get("place_id") == stationId and _boards(s) and s["arr"]
                   for s in train["stops"])]


def options(timetable: Timetable, stationNames: dict[str, str]) -> OnboardingOptionsDto:
    """`stationNames`: place id -> name of the stations on the service map."""
    destinations = []
    for placeId, name in stationNames.items():
        numbers = arrivals(timetable, placeId)
        if not numbers:
            continue  # no passenger train stops there: not somewhere to travel to by train
        types = _types(timetable, numbers)
        destinations.append(DestinationOptionDto(
            placeId=placeId, name=name, trainsPerDay=len(numbers), trainTypes=types,
            featured=placeId in FEATURED_DESTINATIONS, label=_label(len(numbers), types)))
    order = {placeId: i for i, placeId in enumerate(FEATURED_DESTINATIONS)}
    destinations.sort(key=lambda d: (order.get(d.placeId, len(order)), -d.trainsPerDay, d.name))
    companions = [
        CompanionOptionDto(id=key, label=rule.label, maxWalkingMinutes=rule.maxWalkingMinutes,
                           indoorFirst=rule.indoorFirst, reason=rule.reason)
        for key, rule in RULES.items()]
    return OnboardingOptionsDto(destinations=destinations, companions=companions,
                                targetDate=timetable.meta["target_date"],
        basis=timetable.meta["basis"]["summary"])


def origins(timetable: Timetable, destinationId: str, destinationName: str) -> OriginsDto:
    """Stations with a train one can board there and leave at the destination."""
    found: dict[str, dict] = {}
    for train in timetable.trains:
        stops = train["stops"]
        at = next((i for i, s in enumerate(stops)
                   if s.get("place_id") == destinationId and _boards(s) and s["arr"]), None)
        if at is None:
            continue
        for stop in stops[:at]:
            if not (_boards(stop) and stop["dep"]):
                continue
            key = stop.get("code") or stop["name"]
            row = found.setdefault(key, {"stop": stop, "trains": [], "starts": False})
            row["trains"].append(
                (train["no"], stop["dep"],
                 (_minutes(stops[at]["arr"]) - _minutes(stop["dep"])) % DAY))
            row["starts"] = row["starts"] or stop["stop_type"] == "시발"
    rows = []
    for row in found.values():
        stop, trains = row["stop"], row["trains"]
        types = _types(timetable, [no for no, _, _ in trains])
        departures = sorted(dep for _, dep, _ in trains)
        rows.append(OriginOptionDto(
            stationName=stop["name"], stationCode=stop.get("code"),
            placeId=stop.get("place_id"), trainsPerDay=len(trains), trainTypes=types,
            firstDeparture=departures[0], lastDeparture=departures[-1],
            fastestMinutes=min(minutes for _, _, minutes in trains),
            startsHere=row["starts"], featured=False, label=_label(len(trains), types)))
    # Stations where a train starts its run come first (서울, 청량리, 강릉…): they are where
    # a trip to the area usually begins. Then by how many trains a day.
    rows.sort(key=lambda r: (not r.startsHere, -r.trainsPerDay, r.fastestMinutes, r.stationName))
    starting = [r for r in rows if r.startsHere and r.placeId is None][:FEATURED_ORIGINS]
    for row in starting:
        row.featured = True
    rows.sort(key=lambda r: not r.featured)  # stable: featured first, order kept otherwise
    return OriginsDto(
        destination=OnboardingStationDto(placeId=destinationId, name=destinationName),
        origins=rows, certainty="estimated", targetDate=timetable.meta["target_date"],
        basis=timetable.meta["basis"]["summary"])
