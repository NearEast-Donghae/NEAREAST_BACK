"""Timetable queries over the snapshot built by scripts/fetch_timetable.py.

Plain functions (no HTTP): the trains API and the chat tools call the same ones.
The snapshot is `estimated` by construction; `basis()` says why and is attached to
every answer.
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from neareast_api.modules.trains.schemas import (
    Basis,
    Gap,
    Rhythm,
    StationTrain,
    StationTrains,
    Stop,
    TrainDetail,
    TrainSearch,
    TrainSummary,
    TripOption,
)

logger = logging.getLogger(__name__)

GAP_MINUTES = 60
PASSING_NOTE = "여객열차가 서지 않아요. 통과 시각은 앞뒤 정차역 시각으로 어림했어요."
NO_RECORD_NOTE = "이 역의 여객열차 운행 기록이 없어요. 화물열차 시각은 공식 데이터가 없어요."


class TimetableUnavailable(Exception):
    """No snapshot file to answer from."""


@dataclass(frozen=True)
class Timetable:
    meta: dict
    stations: dict[str, dict]  # place id -> {name, code, records}
    trains: tuple[dict, ...]
    types: dict[str, str]  # train no -> type, only filled rows

    def basis(self) -> Basis:
        b = self.meta["basis"]
        return Basis(summary=b["summary"], target_date=self.meta["target_date"],
                     record_dates=b["record_dates"], plan_date=b["plan_date"],
                     limits=self.meta["limits"])


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


@lru_cache(maxsize=4)
def _load(root: Path) -> Timetable:
    files = sorted((root / "data").glob("timetable_*.json"))
    if not files:
        raise TimetableUnavailable(f"no data/timetable_*.json under {root}")
    try:
        snapshot = json.loads(files[-1].read_text("utf-8"))
        meta, stations, trains = snapshot["meta"], snapshot["stations"], tuple(snapshot["trains"])
    except (OSError, ValueError, KeyError) as exc:
        raise TimetableUnavailable(f"cannot read {files[-1]}: {exc}") from exc
    types = {}
    try:
        table = json.loads((root / "data" / "train_types.json").read_text("utf-8"))["trains"]
        types = {no: row["type"].strip() for no, row in table.items() if row.get("type", "").strip()}
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        logger.warning("train types table not used: %s", exc)
    return Timetable(meta, stations, trains, types)


def load(content_dir: str) -> Timetable:
    """Read once per directory; raises TimetableUnavailable."""
    return _load(Path(content_dir).resolve())


def load_or_none(content_dir: str) -> Timetable | None:
    try:
        return load(content_dir)
    except TimetableUnavailable:
        return None


def _summary(timetable: Timetable, train: dict) -> dict:
    return dict(
        no=train["no"], type=timetable.types.get(train["no"]), line=train["line"],
        direction=train["direction"], origin=train["origin"], destination=train["destination"],
        weekly_saturday=train["weekly_saturday"], in_plan=train["in_plan"],
        spread_min=train["spread_min"],
    )


def _stop_at(train: dict, station_id: str) -> dict | None:
    return next((s for s in train["stops"] if s.get("place_id") == station_id), None)


def _stop_time(stop: dict) -> str:
    return stop.get("pass") or stop["dep"] or stop["arr"]


def rhythm(times: list[str]) -> Rhythm:
    hourly: dict[str, int] = {}
    for t in times:
        hourly[t[:2]] = hourly.get(t[:2], 0) + 1
    ordered = sorted(times, key=_minutes)
    gaps = [
        Gap(start=a, end=b, minutes=_minutes(b) - _minutes(a))
        for a, b in zip(ordered, ordered[1:])
        if _minutes(b) - _minutes(a) >= GAP_MINUTES
    ]
    busiest = max(sorted(hourly), key=lambda h: hourly[h]) if hourly else None
    return Rhythm(hourly=dict(sorted(hourly.items())), busiest_hour=busiest, gaps=gaps)


def station_trains(
    timetable: Timetable, station_id: str, name: str | None = None,
    after: str | None = None, direction: str | None = None,
) -> StationTrains:
    """Trains at a station in time order, with the rhythm of the whole (filtered) day.
    A station without records (freight only) answers with an empty list and a note."""
    info = timetable.stations.get(station_id)
    rows = []
    for train in timetable.trains:
        stop = _stop_at(train, station_id)
        if stop is None or (direction and train["direction"] != direction):
            continue
        time = _stop_time(stop)
        if after and _minutes(time) < _minutes(after):
            continue
        rows.append(StationTrain(
            **_summary(timetable, train), time=time, arr=stop["arr"], dep=stop["dep"],
            passing="pass" in stop, stop_type=stop["stop_type"], time_basis=stop.get("basis"),
        ))
    rows.sort(key=lambda r: _minutes(r.time))
    boarding = bool(info and info["records"])
    note = None if boarding else PASSING_NOTE if rows else NO_RECORD_NOTE
    return StationTrains(
        station_id=station_id, station_name=(info or {}).get("name") or name or station_id,
        boarding=boarding, note=note, trains=rows, rhythm=rhythm([r.time for r in rows]),
        basis=timetable.basis(),
    )


def search(timetable: Timetable, from_id: str, to_id: str, after: str | None = None) -> TrainSearch:
    """Trains one can ride from one station to another (both must be real stops)."""
    options = []
    for train in timetable.trains:
        names = [s.get("place_id") for s in train["stops"]]
        if from_id not in names or to_id not in names or names.index(from_id) >= names.index(to_id):
            continue
        start, end = train["stops"][names.index(from_id)], train["stops"][names.index(to_id)]
        if "pass" in start or "pass" in end or not start["dep"] or not end["arr"]:
            continue  # cannot board or alight at a passing station
        if after and _minutes(start["dep"]) < _minutes(after):
            continue
        options.append(TripOption(
            **_summary(timetable, train), dep=start["dep"], arr=end["arr"],
            minutes=(_minutes(end["arr"]) - _minutes(start["dep"])) % (24 * 60),
        ))
    options.sort(key=lambda o: _minutes(o.dep))
    return TrainSearch(from_id=from_id, to_id=to_id, after=after, trains=options,
                       basis=timetable.basis())


def detail(timetable: Timetable, train_no: str) -> TrainDetail | None:
    number = train_no.lstrip("0") or "0"
    train = next((t for t in timetable.trains if t["no"] == number), None)
    if train is None:
        return None
    stops = [Stop(name=s["name"], place_id=s.get("place_id"), arr=s["arr"], dep=s["dep"],
                  pass_time=s.get("pass"), stop_type=s["stop_type"], time_basis=s.get("basis"))
             for s in train["stops"]]
    return TrainDetail(**_summary(timetable, train), stops=stops, basis=timetable.basis())


def stop_spread(timetable: Timetable, train_no: str, station_id: str) -> int:
    """How many minutes this train's time at a station differed between the recorded weeks.
    The listed arrival is the middle week, so `arrival + spread` is no earlier than the
    latest arrival seen and `arrival - spread` no later than the earliest. A passing or
    unrecorded stop has no spread of its own and uses the train's largest."""
    train = next((t for t in timetable.trains if t["no"] == train_no), None)
    if train is None:
        return 0
    spread = (_stop_at(train, station_id) or {}).get("spread_min")
    if spread is None:
        spread = train["spread_min"]
    return min(spread, 24 * 60 - spread)  # times either side of midnight are minutes apart


def describe(train: TrainSummary) -> str:
    """How a train is named to people: its type when known, else number and line."""
    label = f"{train.type} {train.no}" if train.type else f"{train.no}({train.line})"
    return f"{label} {train.destination}행"
