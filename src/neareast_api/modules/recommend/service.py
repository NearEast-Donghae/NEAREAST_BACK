"""Places to offer for a train-less stretch, and the two facts that decide them: how long
the walk is and whether the market is open that day.

Plain functions over the place catalog. No storage, no HTTP, no LLM. Callers (the chat
now, a plan screen later) get the same places for the same reason. A suggestion is never
put into a plan here: it is offered and the user decides.
"""

import datetime as dt
import math
from collections.abc import Collection, Mapping

from neareast_api.modules.places.service import Place
from neareast_api.modules.recommend.schemas import Suggestion

RETURN_SLACK = 10  # minutes to spare when coming back from a suggested place
MAX_SUGGESTIONS_PER_GAP = 2
SUGGEST_STAY_MIN = 30
SUGGEST_KINDS = ("poi", "heritage")
WALK_DETOUR = 1.3  # straight line -> streets
WALK_KMH = 4.5
MAX_WALK_MIN = 90  # longer than this is not offered as a walk
MARKET_ID = "poi_bukpyeong_market"
MARKET_DAY_ENDINGS = (3, 8)  # 북평민속시장: days of the month ending in 3 or 8


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def market_day(date: dt.date) -> tuple[bool, dt.date]:
    """(is `date` a market day, the nearest market day on or after it)."""
    day = date
    while day.day % 10 not in MARKET_DAY_ENDINGS:
        day += dt.timedelta(days=1)
    return day == date, day


def walk_minutes(a: Place, b: Place) -> int | None:
    """Walking time between two places: straight distance x 1.3 at 4.5 km/h, rounded up.
    None when a coordinate is missing or the walk is too long to offer."""
    if None in (a.lat, a.lng, b.lat, b.lng):
        return None
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b.lng - a.lng) / 2) ** 2)
    km = 2 * 6371.0088 * math.asin(math.sqrt(h))
    minutes = math.ceil(round(km * WALK_DETOUR / WALK_KMH * 60, 6))
    return minutes if minutes <= MAX_WALK_MIN else None


def suggest(
    places: Mapping[str, Place], anchor_id: str, start: str, end: str, *,
    skip: Collection[str] = (), date: dt.date | None = None, far_first: bool = False,
    fit_from: str | None = None, limit: int = MAX_SUGGESTIONS_PER_GAP,
) -> list[Suggestion]:
    """Places one can walk to, stay at and come back from within a gap at `anchor_id`.
    `skip` holds what must not be offered (already pinned, or dismissed by the user).
    `fit_from` (default `start`) is when the user is surely there in the worst case."""
    anchor = places.get(anchor_id)
    if anchor is None:
        return []
    room = _minutes(end) - _minutes(fit_from or start)
    found = []
    for place in places.values():
        if place.id == anchor_id or place.id in skip or place.kind not in SUGGEST_KINDS:
            continue
        walk = walk_minutes(anchor, place)
        if walk is None or 2 * walk + SUGGEST_STAY_MIN + RETURN_SLACK > room:
            continue
        found.append((walk, place))
    found.sort(key=lambda f: (-f[0] if far_first else f[0], f[1].id))
    out = []
    for walk, place in found[:limit]:
        notes = []
        if place.id == MARKET_ID and date is not None:
            is_day, nearest = market_day(date)
            notes.append(
                f"{date.month}/{date.day}은 장날이에요" if is_day else
                f"{date.month}/{date.day}은 장날이 아니에요(다음 장날 {nearest.month}/{nearest.day})")
        out.append(Suggestion(
            target=place.id, gap=[start, end],
            reason=(f"{anchor.name} 공백({start}–{end}) 안에 다녀올 수 있어요"
                    f"(걸어서 편도 {walk}분, 머무는 시간 {SUGGEST_STAY_MIN}분)"),
            notes=notes, walk_min=walk, stay_min=SUGGEST_STAY_MIN))
    return out
