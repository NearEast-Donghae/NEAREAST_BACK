"""Shared place catalog loaded from PostgreSQL."""

from dataclasses import dataclass
from pathlib import Path

KINDS = ("station", "poi", "heritage", "spot")


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
