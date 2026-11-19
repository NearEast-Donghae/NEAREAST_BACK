"""Chat tools as plain service-layer functions.

Train tools read the shared timetable (modules/trains), the same data the screens use.
Without a snapshot they fall back to the EXAMPLE data below, and every answer built on
it is labelled "(예시 데이터)". Walking minutes are still example data.
Every result carries `certainty` so the server (not the LLM) labels proposals.
"""

import datetime as dt
from dataclasses import dataclass

from neareast_api.modules.chat.schemas import Certainty
from neareast_api.modules.recommend.service import MARKET_ID, market_day  # noqa: F401 (used as tools.*)
from neareast_api.modules.trains import service as timetable_service
from neareast_api.modules.trains.service import Timetable

MOCK = "mock"
SNAPSHOT = "snapshot"
SOURCE = MOCK  # source of the walking-minute data
PASSING_BASIS = "통과 시각은 앞뒤 정차역 시각으로 어림했어요."
MOCK_GAP_MINUTES = 120


@dataclass(frozen=True)
class Train:
    time: str  # HH:MM at this station
    number: str
    kind: str  # type when known (KTX-이음 …), else the line name
    direction: str  # "강릉행"
    stops: bool  # False: passes without stopping (observation only)
    certainty: Certainty

    def label(self) -> str:
        return f"{self.time} {self.kind} {self.number} {self.direction}" + (
            "" if self.stops else " 통과(어림)")


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
    source: str = MOCK
    basis: str | None = None  # what the times rest on, shown to the user


@dataclass(frozen=True)
class Spot:
    place_id: str
    walk_minutes: int


# Example data, used only when no timetable snapshot is available. No freight trains:
# there is no official freight data, so none is made up.
_TIMETABLE: dict[str, tuple[Train, ...]] = {
    "station_donghae": (
        Train("07:40", "M-1601", "무궁화", "강릉 방면", True, "timetable"),
        Train("10:20", "M-1631", "ITX-마음", "강릉 방면", True, "timetable"),
        Train("10:35", "M-801", "KTX-이음", "서울 방면", True, "timetable"),
        Train("10:50", "M-1683", "누리로", "영주 방면", True, "timetable"),
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


def _from_snapshot(timetable: Timetable, station_id: str, after: str | None = None):
    """The shared timetable's trains at a station, in the chat's Train shape."""
    result = timetable_service.station_trains(timetable, station_id, after=after)
    trains = tuple(
        Train(t.time, t.no, t.type or t.line, f"{t.destination}행", not t.passing, "estimated")
        for t in result.trains
    )
    basis = f"{result.basis.summary}의 어림 시각이에요."  # a sentence the reply can end with
    if trains and not result.boarding:
        basis = f"{basis} {PASSING_BASIS}"
    return result, trains, basis


def station_rhythm(
    station_id: str, date: dt.date | None, timetable: Timetable | None = None
) -> StationRhythm | None:
    """Hourly train counts and gaps; None when nothing is known about the station."""
    if timetable is not None:
        result, trains, basis = _from_snapshot(timetable, station_id)
        if not trains:
            return None  # a real timetable with no trains here: do not invent example ones
        gaps = tuple(Gap(g.start, g.end, "estimated") for g in result.rhythm.gaps)
        hourly = {int(h): n for h, n in result.rhythm.hourly.items()}
        return StationRhythm(station_id, date, hourly, trains, gaps, SNAPSHOT, basis)
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
            "estimated" if "estimated" in (before.certainty, after.certainty) else "timetable",
        )
        for before, after in zip(trains, trains[1:], strict=False)
        if _minutes(after.time) - _minutes(before.time) >= MOCK_GAP_MINUTES
    )
    return StationRhythm(station_id, date, hourly, trains, gaps)


def search_trains(
    date: dt.date | None,
    station: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    after: str | None = None,
    timetable: Timetable | None = None,
) -> list[Train]:
    """Trains at `station`, optionally from a time on."""
    station_id = station or from_
    if not station_id:
        return []
    if timetable is not None:
        return list(_from_snapshot(timetable, station_id, after)[1])
    trains = _TIMETABLE.get(station_id, ())
    if after is not None:
        trains = tuple(t for t in trains if _minutes(t.time) >= _minutes(after))
    return list(trains)


def nearby_spots(place_id: str, walk_minutes: int = 20) -> list[Spot]:
    return [
        Spot(spot_id, minutes)
        for spot_id, minutes in _NEARBY.get(place_id, ())
        if minutes <= walk_minutes
    ]
