"""The place catalog (data/places.json): one list of ids for map markers, knowledge
documents, the timetable and the chat. No HTTP here."""

import datetime as dt
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PLACES_FILE = Path("data") / "places.json"
KINDS = ("station", "poi", "heritage", "spot")
MARKET_ID = "poi_bukpyeong_market"
MARKET_DAY_ENDINGS = (3, 8)  # 북평민속시장: days of the month ending in 3 or 8


class PlacesError(Exception):
    """data/places.json is missing or malformed."""


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    kind: str  # station | poi | heritage | spot
    passenger_stop: bool | None = None  # False: pass/freight, observe only; None: unknown
    aliases: tuple[str, ...] = ()  # other ways people say it ("묵호" for 묵호역)
    lat: float | None = None
    lng: float | None = None


def load_places(root: Path) -> dict[str, Place]:
    try:
        raw = json.loads((root / PLACES_FILE).read_text("utf-8"))["places"]
        places = [
            Place(p["id"], p["name"], p["kind"], p.get("passenger_stop"),
                  tuple(p.get("aliases", ())), p.get("lat"), p.get("lng"))
            for p in raw
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PlacesError(f"cannot load {root / PLACES_FILE}: {exc}") from exc
    by_id = {p.id: p for p in places}
    if len(by_id) != len(places):
        raise PlacesError(f"{PLACES_FILE}: duplicate place id")
    return by_id


@lru_cache(maxsize=4)
def _load(root: Path) -> dict[str, Place]:
    return load_places(root)


def all_places(content_dir: str) -> dict[str, Place]:
    """Read once per directory; raises PlacesError."""
    return _load(Path(content_dir).resolve())


def get(content_dir: str, place_id: str) -> Place | None:
    return all_places(content_dir).get(place_id)


def market_day(date: dt.date) -> tuple[bool, dt.date]:
    """(is `date` a market day, the nearest market day on or after it)."""
    day = date
    while day.day % 10 not in MARKET_DAY_ENDINGS:
        day += dt.timedelta(days=1)
    return day == date, day
