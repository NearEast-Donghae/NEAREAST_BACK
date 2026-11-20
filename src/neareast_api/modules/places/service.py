"""Shared place catalog loaded from PostgreSQL."""

from dataclasses import dataclass
from pathlib import Path

KINDS = ("station", "poi", "heritage", "spot")

# What a place is, as the Korea Tourism Organization's data (TourAPI) records it. Read,
# never guessed: a place the data does not classify has no category.
TOUR_API_CATEGORIES = {  # places.operating_info.tourApi.contentTypeId
    "12": "attraction", "14": "culture", "15": "festival", "25": "course",
    "28": "leisure", "32": "lodging", "38": "shopping", "39": "restaurant",
}
PLANNER_CATEGORIES = {  # planner_locations.payload.place_type, when it came from TourAPI
    "attraction": "attraction", "cultural_facility": "culture", "event": "festival",
    "travel_course": "course", "leisure": "leisure", "accommodation": "lodging",
    "shopping": "shopping", "dining": "restaurant",
}
# TourAPI files a market under shopping, together with shops. These are the markets,
# listed by hand after looking at each one; nothing is inferred from a name.
MARKET_IDS = frozenset({"poi_bukpyeong_market"})


def category_of(place_id: str, operating_info: dict | None, planner_payload: dict | None) -> str | None:
    if place_id in MARKET_IDS:
        return "market"
    content_type = ((operating_info or {}).get("tourApi") or {}).get("contentTypeId")
    if content_type is not None:
        return TOUR_API_CATEGORIES.get(str(content_type))
    payload = planner_payload or {}
    from_tour_api = any(s.get("dataset") == "tourapi" for s in payload.get("sources") or [])
    return PLANNER_CATEGORIES.get(payload.get("place_type")) if from_tour_api else None


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
    # attraction | culture | market | restaurant | lodging | shopping | leisure | festival |
    # course, or None when the tourism data does not say
    category: str | None = None


def load_places(root: Path) -> dict[str, Place]:
    from neareast_api.db.content_repository import ContentRepository
    return ContentRepository.places()


def all_places(content_dir: str) -> dict[str, Place]:
    from neareast_api.db.content_repository import ContentRepository
    return ContentRepository.places()


def get(content_dir: str, place_id: str) -> Place | None:
    return all_places(content_dir).get(place_id)
