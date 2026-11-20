"""Places to offer for a train-less stretch, and the two facts that decide them: how long
the walk is and whether the market is open that day.

Plain functions over the place catalog. No storage, no HTTP, no LLM. Callers (the chat
now, a plan screen later) get the same places for the same reason. A suggestion is never
put into a plan here: it is offered and the user decides.
"""

import datetime as dt
import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from neareast_api.modules.places.service import Place
from neareast_api.modules.recommend.schemas import Suggestion

RETURN_SLACK = 10  # minutes to spare when coming back from a suggested place
MAX_SUGGESTIONS_PER_GAP = 2
SUGGEST_STAY_MIN = 30
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


@dataclass(frozen=True)
class Walk:
    minutes: int | None
    reason: str | None = None  # why there is no time: "no_coordinates" | "too_far"


def walk_between(a: Place, b: Place) -> Walk:
    """Walking time between two places, or why it cannot be given. With coordinates on
    both ends it is the straight-line estimate of walk_minutes; nothing else is guessed."""
    if None in (a.lat, a.lng, b.lat, b.lng):
        return Walk(None, "no_coordinates")
    minutes = walk_minutes(a, b)
    return Walk(minutes) if minutes is not None else Walk(None, "too_far")


# What may be offered. A place whose category the tourism data does not give is never
# offered: nothing is guessed from a name.
VISIT_CATEGORIES = ("attraction", "culture", "market")  # somewhere to go and look
MEAL_CATEGORY = "restaurant"  # offered only when the gap covers a meal time, as a meal
MEAL_WINDOWS = (("11:00", "14:00", "점심"), ("17:00", "20:00", "저녁"))
MEAL_MIN_OVERLAP = 30  # minutes of the gap that must fall inside the meal time
MAX_MEALS_PER_GAP = 1
NEARBY_WALK_MIN = 20  # "nearby" means within this walk
MAX_NEARBY = 5


def within_walk(
    places: Mapping[str, Place], anchor: Place, categories: Collection[str],
    skip: Collection[str] = (),
) -> list[tuple[int, Place]]:
    """(minutes, place) for every place of those categories one can walk to from the
    anchor, nearest first. Every walking time in the service comes from walk_between, so
    the same pair of places never gets two different numbers."""
    found = []
    for place in places.values():
        if place.id == anchor.id or place.id in skip or place.category not in categories:
            continue
        walk = walk_between(anchor, place)
        if walk.minutes is not None:
            found.append((walk.minutes, place))
    return sorted(found, key=lambda f: (f[0], f[1].id))


def nearby(
    places: Mapping[str, Place], anchor_id: str, *, skip: Collection[str] = (),
    within: int = NEARBY_WALK_MIN, limit: int = MAX_NEARBY,
) -> list[tuple[int, Place]]:
    """Places worth a visit within a short walk of the anchor, nearest first."""
    anchor = places.get(anchor_id)
    if anchor is None:
        return []
    return [(minutes, place) for minutes, place in
            within_walk(places, anchor, VISIT_CATEGORIES, skip) if minutes <= within][:limit]


def meal_time(start: str, end: str) -> str | None:
    """"점심" or "저녁" when enough of the gap falls inside that meal time."""
    for opens, closes, name in MEAL_WINDOWS:
        overlap = min(_minutes(end), _minutes(closes)) - max(_minutes(start), _minutes(opens))
        if overlap >= MEAL_MIN_OVERLAP:
            return name
    return None


def suggest(
    places: Mapping[str, Place], anchor_id: str, start: str, end: str, *,
    skip: Collection[str] = (), date: dt.date | None = None, far_first: bool = False,
    fit_from: str | None = None, limit: int = MAX_SUGGESTIONS_PER_GAP,
) -> list[Suggestion]:
    """Places one can walk to, stay at and come back from within a gap at `anchor_id`:
    attractions, cultural places and markets, and one place to eat when the gap covers a
    meal time. `skip` holds what must not be offered (already pinned, or dismissed).
    `fit_from` (default `start`) is when the user is surely there in the worst case."""
    anchor = places.get(anchor_id)
    if anchor is None:
        return []
    room = _minutes(end) - _minutes(fit_from or start)

    def fitting(categories):
        return [(walk, place) for walk, place in within_walk(places, anchor, categories, skip)
                if 2 * walk + SUGGEST_STAY_MIN + RETURN_SLACK <= room]

    visits = fitting(VISIT_CATEGORIES)
    visits.sort(key=lambda f: (-f[0] if far_first else f[0], f[1].id))
    out = []
    for walk, place in visits[:limit]:
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
    meal = meal_time(fit_from or start, end)
    if meal is not None:
        for walk, place in fitting((MEAL_CATEGORY,))[:MAX_MEALS_PER_GAP]:
            out.append(Suggestion(
                target=place.id, gap=[start, end], purpose="meal",
                reason=(f"{anchor.name} 공백({start}–{end})이 {meal} 시간과 겹쳐요. "
                        f"식사하러 다녀올 수 있어요(걸어서 편도 {walk}분)"),
                notes=[], walk_min=walk, stay_min=SUGGEST_STAY_MIN))
    return out
