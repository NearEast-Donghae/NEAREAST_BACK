"""Shared place catalog loaded from PostgreSQL."""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

KINDS = ("station", "poi", "heritage", "spot")
MARKET_ID = "poi_bukpyeong_market"
MARKET_DAY_ENDINGS = (3, 8)  # 북평민속시장: days of the month ending in 3 or 8


class PlacesError(Exception):
    """The place catalog is unavailable."""


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
    from neareast_api.db.content_repository import ContentRepository
    return ContentRepository.places()


def all_places(content_dir: str) -> dict[str, Place]:
    from neareast_api.db.content_repository import ContentRepository
    return ContentRepository.places()


def get(content_dir: str, place_id: str) -> Place | None:
    return all_places(content_dir).get(place_id)


def market_day(date: dt.date) -> tuple[bool, dt.date]:
    """(is `date` a market day, the nearest market day on or after it)."""
    day = date
    while day.day % 10 not in MARKET_DAY_ENDINGS:
        day += dt.timedelta(days=1)
    return day == date, day
