"""What the pictures a user chose say, and where in Donghae that taste leads.

Three things are read from the choice, strongest first:
1. the places the chosen pictures were taken at (a person recorded that when curating);
2. places whose own pictures carry the same moods;
3. places whose description is close in meaning to the chosen pictures (stored vectors,
   no model call at request time; skipped when vectors are missing or of another model).

The railway is the time axis, not the subject: every place says which station one gets
off at and how long the walk is, and where a train is in view (a station, or a picture
whose curator named the station) it lists the trains around sunrise and sunset.
Nothing is added to a plan or a saved list here. These are offers.
"""

import datetime as dt
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from neareast_api.db.entities import OnboardingPhotoEntity, UserTasteEntity, currentTime
from neareast_api.modules.chat.retrieval import Content
from neareast_api.modules.onboarding.dto import (
    MAX_PICKS,
    MIN_PICKS,
    AccessDto,
    GoldenTrainDto,
    LightDto,
    MoodDto,
    PhotoBoardDto,
    PhotoCreditDto,
    PhotoDto,
    TasteDto,
    TasteMoodDto,
    TastePlaceDto,
    WatchDto,
)
from neareast_api.modules.onboarding.light import SunTimes, sun_times
from neareast_api.modules.onboarding.moods import MOODS, RAIL_MOODS
from neareast_api.modules.places.service import Place
from neareast_api.modules.recommend.service import VISIT_CATEGORIES, walk_between
from neareast_api.modules.trains.service import Timetable, station_trains

MAX_PLACES = 8
MAX_SIMILAR = 3  # places offered on similarity of meaning alone
TOP_MOODS = 2  # moods named in the one-line summary
TOP_MOOD_SHARE = 0.3  # a mood is "chosen a lot" from this share of the pictures
GOLDEN_MINUTES = 40  # a train this close to sunrise or sunset is listed
MAX_GOLDEN = 4  # per sunrise and per sunset: the ones nearest to it
ACCESS_WALK_MINUTES = 30  # a walk from the station up to this is called walkable
# Cosine similarity below this is not a likeness worth offering. Measured on the 203 real
# documents with text-embedding-3-large (2026-11-20): places that fit a choice score
# 0.52-0.69, the median place 0.31-0.41, and the wrong ones that came closest (a valley
# for a choice of stations, a cave for the sea) 0.45-0.52.
MIN_SIMILARITY = 0.52
LIGHT_BASIS = "위도·경도와 날짜로 계산한 시각이에요(지형·날씨는 반영하지 않아요, 1~2분 오차)."


@dataclass(frozen=True)
class Photo:
    id: str
    title: str
    image_url: str
    photographer: str | None
    shot_month: str | None
    source: str
    license: str
    place_id: str | None
    view_station_id: str | None
    moods: tuple[str, ...]
    position: int = 0
    embedding_model: str | None = None
    embedding: tuple[float, ...] | None = None


# --- storage ---------------------------------------------------------------------------

def catalog(session: Session) -> list[Photo]:
    rows = session.scalars(select(OnboardingPhotoEntity).order_by(
        OnboardingPhotoEntity.position, OnboardingPhotoEntity.id))
    return [Photo(
        row.id, row.title, row.imageUrl, row.photographer, row.shotMonth, row.source,
        row.license, row.placeId, row.viewStationId,
        tuple(m for m in row.moods if m in MOODS), row.position, row.embeddingModel,
        tuple(float(v) for v in row.embedding) if row.embedding is not None else None,
    ) for row in rows]


def saved(session: Session, userId: str) -> UserTasteEntity | None:
    return session.get(UserTasteEntity, userId)


def save(session: Session, userId: str, photoIds: Sequence[str]) -> UserTasteEntity:
    taste = session.get(UserTasteEntity, userId)
    if taste is None:
        taste = UserTasteEntity(userId=userId)
        session.add(taste)
    taste.photoIds = list(photoIds)
    taste.updatedAt = currentTime()
    session.commit()
    return taste


def clear(session: Session, userId: str) -> None:
    taste = session.get(UserTasteEntity, userId)
    if taste is not None:
        session.delete(taste)
        session.commit()


# --- the board -------------------------------------------------------------------------

def _credit(photo: Photo) -> PhotoCreditDto:
    text = f"ⓒ{photo.source}" + (f"-{photo.photographer}" if photo.photographer else "")
    return PhotoCreditDto(photographer=photo.photographer, source=photo.source,
                          license=photo.license, text=text)


def board_order(photos: Sequence[Photo]) -> list[Photo]:
    """One picture of each mood in turn, so the first screen shows the whole range and
    not twenty pictures of the sea. Within a mood the curated order is kept."""
    lanes: dict[str, list[Photo]] = {}
    for photo in photos:
        lanes.setdefault(photo.moods[0] if photo.moods else "", []).append(photo)
    ordered, queues = [], [lanes[m] for m in (*MOODS, "") if m in lanes]
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


def board(photos: Sequence[Photo], places: Mapping[str, Place]) -> PhotoBoardDto:
    return PhotoBoardDto(
        photos=[PhotoDto(
            id=p.id, title=p.title, imageUrl=p.image_url, moods=list(p.moods),
            placeId=p.place_id if p.place_id in places else None,
            placeName=places[p.place_id].name if p.place_id in places else None,
            shotMonth=p.shot_month, credit=_credit(p)) for p in board_order(photos)],
        moods=[MoodDto(id=mood, label=label) for mood, label in MOODS.items()],
        minPicks=MIN_PICKS, maxPicks=MAX_PICKS)


# --- reading a taste -------------------------------------------------------------------

def mood_shares(picked: Sequence[Photo]) -> list[TasteMoodDto]:
    """Moods of the chosen pictures, the most chosen first (ties in the list's order)."""
    counted = Counter(mood for photo in picked for mood in photo.moods)
    order = list(MOODS)
    return [TasteMoodDto(id=mood, label=MOODS[mood], count=count,
                         share=round(count / len(picked), 2))
            for mood, count in sorted(counted.items(), key=lambda c: (-c[1], order.index(c[0])))]


def summary(moods: Sequence[TasteMoodDto], picked: Sequence[Photo]) -> str:
    top = [m for m in moods if m.share >= TOP_MOOD_SHARE][:TOP_MOODS]
    rail = sum(1 for photo in picked if RAIL_MOODS & set(photo.moods))
    # No mood stands out: saying one "was chosen a lot" would be untrue.
    first = (f"{', '.join(f'‘{m.label}’' for m in top)} 사진을 많이 고르셨어요." if top
             else "여러 분위기의 사진을 고루 고르셨어요.")
    second = (f"열차나 역이 담긴 사진은 {rail}장이에요." if rail
              else "열차가 담긴 사진은 없지만, 가는 길은 열차와 걷기로 안내해 드려요.")
    return f"{first} {second}"


def _mean(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    return tuple(sum(column) / len(vectors) for column in zip(*vectors, strict=True))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def similar_places(
    picked: Sequence[Photo], photos: Sequence[Photo], content: Content | None,
) -> dict[str, float]:
    """place id -> likeness (cosine) between the chosen pictures and what is written and
    pictured about the place. Empty when the vectors cannot be compared: a chosen picture
    without one, or pictures and documents embedded with different models."""
    vectors = [p.embedding for p in picked]
    models = {p.embedding_model for p in picked}
    if not vectors or None in vectors or len(models) != 1:
        return {}
    model = next(iter(models))
    chosen = {p.id for p in picked}
    about: dict[str, list[Sequence[float]]] = {}
    for photo in photos:  # other pictures of a place speak for it too
        if (photo.id not in chosen and photo.place_id and photo.embedding is not None
                and photo.embedding_model == model):
            about.setdefault(photo.place_id, []).append(photo.embedding)
    if content is not None and content.embedding_model == model:
        for doc_id, vector in content.vectors.items():
            doc = content.docs.get(doc_id)
            if doc is not None and len(doc.places) == 1:  # a document about that one place
                about.setdefault(doc.places[0], []).append(vector)
    taste = _mean(vectors)
    return {place_id: _cosine(taste, _mean(found)) for place_id, found in about.items()}


@dataclass(frozen=True)
class Ranked:
    place: Place
    match: str  # photo | mood | similar
    score: float
    photo_ids: tuple[str, ...]  # chosen pictures taken here
    moods: tuple[str, ...]  # moods shared with the taste
    cover: str | None


def _offerable(place: Place) -> bool:
    """Somewhere to go and look. Places to eat or sleep are not offered on a likeness."""
    return place.kind != "poi" or place.category in VISIT_CATEGORIES


def rank_places(
    picked: Sequence[Photo], photos: Sequence[Photo], places: Mapping[str, Place],
    content: Content | None = None, min_similarity: float = MIN_SIMILARITY,
) -> list[Ranked]:
    taste = {m.id: m.share for m in mood_shares(picked)}
    at_place: dict[str, list[Photo]] = {}
    for photo in photos:
        if photo.place_id in places:
            at_place.setdefault(photo.place_id, []).append(photo)
    chosen: dict[str, list[Photo]] = {}
    for photo in picked:
        if photo.place_id in places:
            chosen.setdefault(photo.place_id, []).append(photo)
    likeness = similar_places(picked, photos, content)

    ranked = []
    for place_id in {*at_place, *likeness} & set(places):
        place, shots = places[place_id], at_place.get(place_id, [])
        direct = chosen.get(place_id, [])
        # How much of the taste this place's own pictures answer: for each mood, the
        # share of the chosen pictures with it times the share of the place's with it.
        profile = Counter(mood for shot in shots for mood in shot.moods)
        shared = tuple(m for m in MOODS if taste.get(m) and profile.get(m))
        mood = sum(taste[m] * profile[m] / len(shots) for m in shared) if shots else 0.0
        like = likeness.get(place_id, 0.0)
        like = like if like >= min_similarity else 0.0
        if direct:
            match = "photo"
        elif mood > 0:
            match = "mood"
        elif like > 0 and _offerable(place):
            match = "similar"
        else:
            continue
        cover = (direct or shots or [None])[0]
        ranked.append(Ranked(
            place, match, round(len(direct) + 0.6 * mood + 0.4 * like, 4),
            tuple(p.id for p in direct), shared, cover.id if cover else None))
    ranked.sort(key=lambda r: (-r.score, r.place.id))
    out, similar = [], 0
    for row in ranked:
        if row.match == "similar":
            if similar >= MAX_SIMILAR:
                continue
            similar += 1
        out.append(row)
    return out[:MAX_PLACES]


def _reason(row: Ranked) -> str:
    if row.match == "photo":
        return f"고른 사진 {len(row.photo_ids)}장을 여기서 찍었어요."
    if row.match == "mood":
        return f"고른 사진과 분위기가 같은 곳이에요({', '.join(MOODS[m] for m in row.moods)})."
    return "고른 사진들의 설명과 이 장소 소개가 닮았어요."


# --- the railway side ------------------------------------------------------------------

def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def boarding_stations(places: Mapping[str, Place], timetable: Timetable | None) -> list[Place]:
    """Stations a passenger can get off at, by the timetable's own records."""
    if timetable is None:
        return []
    return [place for place in places.values() if place.kind == "station"
            and (timetable.stations.get(place.id) or {}).get("records")]


def access(place: Place, stations: Sequence[Place]) -> AccessDto | None:
    """The nearest station to get off at for this place. None for a station itself, and
    when no station is within a walk (or a coordinate is missing): nothing is guessed."""
    if place.kind == "station":
        return None
    walks = [(walk.minutes, station) for station in stations
             if (walk := walk_between(station, place)).minutes is not None]
    if not walks:
        return None
    minutes, station = min(walks, key=lambda w: (w[0], w[1].id))
    return AccessDto(stationId=station.id, stationName=station.name, walkMinutes=minutes,
                     withinWalk=minutes <= ACCESS_WALK_MINUTES)


def watch(
    place: Place, shots: Sequence[Photo], places: Mapping[str, Place],
    timetable: Timetable | None, sun: SunTimes,
) -> WatchDto | None:
    """Trains in view from the place. Only where that is known: the place is a station,
    or the curator of one of its pictures named the station whose trains are in frame."""
    if timetable is None:
        return None
    station_id = place.id if place.kind == "station" else next(
        (s.view_station_id for s in shots if s.view_station_id in places), None)
    if station_id is None:
        return None
    listed = station_trains(timetable, station_id, places[station_id].name)
    golden = []
    for near, at in (("sunrise", sun.sunrise), ("sunset", sun.sunset)):
        close = [GoldenTrainDto(
            trainNo=train.no, trainType=train.type, time=train.time, passing=train.passing,
            near=near, minutesFromSun=_minutes(train.time) - _minutes(at))
            for train in listed.trains
            if abs(_minutes(train.time) - _minutes(at)) <= GOLDEN_MINUTES]
        close.sort(key=lambda g: (abs(g.minutesFromSun), g.time))
        golden += sorted(close[:MAX_GOLDEN], key=lambda g: g.time)
    name = places[station_id].name
    basis = (f"{name}의 시각이에요." if station_id == place.id else
             f"{name} 기준 시각이에요. 이 지점을 지나는 시각은 몇 분 달라요.")
    return WatchDto(stationId=station_id, stationName=name,
                    boarding=listed.boarding, trainsPerDay=len(listed.trains),
                    golden=golden, basis=basis, note=listed.note)


def describe(
    photo_ids: Sequence[str], photos: Sequence[Photo], places: Mapping[str, Place],
    timetable: Timetable | None, date: dt.date, content: Content | None = None,
    updated_at=None,
) -> TasteDto | None:
    """The taste those pictures add up to. Ids no longer in the catalog are left out;
    None when none of them is left."""
    by_id = {photo.id: photo for photo in photos}
    picked = [by_id[i] for i in dict.fromkeys(photo_ids) if i in by_id]
    if not picked:
        return None
    moods = mood_shares(picked)
    sun = sun_times(date)
    stations = boarding_stations(places, timetable)
    picked_ids = {p.id for p in picked}
    offered = []
    for row in rank_places(picked, photos, places, content):
        shots = sorted((p for p in photos if p.place_id == row.place.id),
                       key=lambda p: p.id not in picked_ids)  # the chosen pictures first
        offered.append(TastePlaceDto(
            placeId=row.place.id, name=row.place.name, kind=row.place.kind,
            category=row.place.category, reason=_reason(row), match=row.match,
            photoIds=list(row.photo_ids), coverPhotoId=row.cover, moods=list(row.moods),
            access=access(row.place, stations),
            watch=watch(row.place, shots, places, timetable, sun)))
    return TasteDto(
        photoIds=[p.id for p in picked], moods=moods, summary=summary(moods, picked),
        places=offered,
        light=LightDto(date=date.isoformat(), sunrise=sun.sunrise, sunset=sun.sunset,
                       basis=LIGHT_BASIS),
        certainty="estimated" if timetable is not None else None,
        basis=timetable.meta["basis"]["summary"] if timetable is not None else None,
        updatedAt=updated_at)
