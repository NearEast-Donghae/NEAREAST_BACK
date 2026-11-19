"""Chat tools as plain service-layer functions.

Places come from data/places.json (see retrieval.py); ids here must exist there.
MOCK DATA: train times/numbers and walking minutes below are placeholders so the
contract works without keys. Step 4 replaces them with data/timetable_*.json.
Every result carries `certainty` so the server (not the LLM) labels proposals.
"""

import datetime as dt
from dataclasses import dataclass

from neareast_api.modules.chat.schemas import Certainty

SOURCE = "mock"
GAP_MINUTES = 120


@dataclass(frozen=True)
class Train:
    time: str  # HH:MM at this station
    number: str
    kind: str  # TIDS notation: KTX-이음, ITX-마음, 누리로, 무궁화, 화물
    direction: str
    stops: bool
    certainty: Certainty


@dataclass(frozen=True)
class Gap:
    start: str
    end: str
    certainty: Certainty


@dataclass(frozen=True)
class StationRhythm:
    station_id: str
    date: dt.date | None
    hourly: dict[int, int]
    trains: tuple[Train, ...]
    gaps: tuple[Gap, ...]
    source: str = SOURCE


@dataclass(frozen=True)
class Spot:
    place_id: str
    walk_minutes: int


_TIMETABLE: dict[str, tuple[Train, ...]] = {
    "station_donghae": (
        Train("07:40", "M-1601", "무궁화", "강릉 방면", True, "timetable"),
        Train("10:20", "M-1631", "ITX-마음", "강릉 방면", True, "timetable"),
        Train("10:35", "M-801", "KTX-이음", "서울 방면", True, "timetable"),
        Train("10:50", "M-1683", "누리로", "영주 방면", True, "timetable"),
        Train("11:05", "F-3021", "화물", "삼척 방면", False, "planned"),
        Train("11:15", "M-1603", "무궁화", "강릉 방면", True, "timetable"),
        Train("16:00", "M-803", "KTX-이음", "서울 방면", True, "timetable"),
        Train("18:10", "M-1605", "무궁화", "강릉 방면", True, "timetable"),
    ),
    "station_mukho": (
        Train("10:30", "M-1631", "ITX-마음", "강릉 방면", True, "timetable"),
        Train("11:25", "M-1603", "무궁화", "강릉 방면", True, "timetable"),
        Train("16:10", "M-803", "KTX-이음", "서울 방면", True, "timetable"),
    ),
    "station_chuam": (
        Train("08:05", "M-1691", "무궁화", "삼척 방면", True, "timetable"),
        Train("13:40", "M-1693", "무궁화", "삼척 방면", True, "timetable"),
        Train("17:20", "M-1695", "무궁화", "동해 방면", True, "timetable"),
    ),
    "station_mangsang": (
        Train("10:26", "M-1631", "ITX-마음", "강릉 방면", False, "estimated"),
        Train("11:20", "M-1603", "무궁화", "강릉 방면", False, "estimated"),
    ),
}

_NEARBY: dict[str, tuple[tuple[str, int], ...]] = {
    "station_donghae": (("poi_bukpyeong_market", 10), ("poi_cheongok_cave", 25)),
    "station_mukho": (("poi_nongoldam", 10), ("poi_dojjebigol", 15)),
    "station_chuam": (("poi_chotdaebawi", 5),),
    "station_mangsang": (("poi_mangsang_beach", 5),),
    "poi_nongoldam": (("station_mukho", 10), ("poi_dojjebigol", 10)),
}


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def gap_minutes(gap: Gap) -> int:
    return _minutes(gap.end) - _minutes(gap.start)


def station_rhythm(station_id: str, date: dt.date | None) -> StationRhythm | None:
    """Hourly train counts and gaps of at least GAP_MINUTES; None for unknown stations."""
    trains = _TIMETABLE.get(station_id)
    if trains is None:
        return None
    hourly: dict[int, int] = {}
    for train in trains:
        hour = _minutes(train.time) // 60
        hourly[hour] = hourly.get(hour, 0) + 1
    gaps = tuple(
        Gap(
            before.time,
            after.time,
            "planned" if "planned" in (before.certainty, after.certainty)
            else "estimated" if "estimated" in (before.certainty, after.certainty)
            else "timetable",
        )
        for before, after in zip(trains, trains[1:])
        if _minutes(after.time) - _minutes(before.time) >= GAP_MINUTES
    )
    return StationRhythm(station_id, date, hourly, trains, gaps)


def search_trains(
    date: dt.date | None,
    station: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    after: str | None = None,
) -> list[Train]:
    """Trains at `station` (from_/to are accepted for the final signature; mock ignores routes)."""
    station_id = station or from_
    trains = _TIMETABLE.get(station_id, ()) if station_id else ()
    if after is not None:
        trains = tuple(t for t in trains if _minutes(t.time) >= _minutes(after))
    return list(trains)


def nearby_spots(place_id: str, walk_minutes: int = 20) -> list[Spot]:
    return [
        Spot(spot_id, minutes)
        for spot_id, minutes in _NEARBY.get(place_id, ())
        if minutes <= walk_minutes
    ]
