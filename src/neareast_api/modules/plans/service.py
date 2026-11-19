"""Weaving a day: the user's pins (trains to see or ride, places to stay) into a timeline.

Plain functions over the place catalog and the shared timetable (modules/trains). No
storage, no HTTP, no LLM.

Rules kept here, not in any prompt:
- A `must` item is never dropped or edited. If the musts cannot all be kept there is no
  plan, only the reason and options the server has re-checked.
- Empty time stays empty (`gap`); places to go are `suggestions` and never timeline steps.
- Nobody boards or alights at a passing station; watching there is fine.
- Judgement uses the worst recorded week: one must be at the platform MIN_XFER before the
  earliest departure, a ride ends at its latest arrival, and a train to watch may come as
  early as it ever did. Shown times are the typical ones; `margin_min` is the slack left.
"""

import bisect
import datetime as dt
import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from neareast_api.modules.places.service import MARKET_ID, Place, market_day
from neareast_api.modules.plans.schemas import (
    Change,
    Comparison,
    Conflict,
    Delta,
    Diff,
    EndStep,
    GapStep,
    Missed,
    ObserveItem,
    ObserveStep,
    Option,
    Outside,
    Passing,
    Plan,
    PlanRequest,
    RideItem,
    RideStep,
    StartStep,
    StayItem,
    Suggestion,
    Summary,
    Variant,
    Variants,
    VisitStep,
    WaitStep,
    WalkStep,
)
from neareast_api.modules.trains import service as trains
from neareast_api.modules.trains.service import Timetable

MIN_XFER = 5  # minutes at the platform before a departure
READY = 2  # minutes in place before a train to watch
GAP_MIN = 30  # a train-less stretch this long is a gap
RETURN_SLACK = 10  # minutes to spare when coming back from a suggested place
MAX_SUGGESTIONS_PER_GAP = 2
SUGGEST_STAY_MIN = 30
SUGGEST_KINDS = ("poi", "heritage")
WALK_DETOUR = 1.3  # straight line -> streets
WALK_KMH = 4.5
MAX_WALK_MIN = 90  # longer than this is not offered as a walk
MAX_OPTIONS = 2
MAX_OPTION_TRIES = 6

# (train items, visit items): the mode only changes which optional items win.
WEIGHTS = {"train_first": (13, 1), "visit_first": (1, 13), "balanced": (1, 1)}
MODE_LABELS = {"train_first": "열차 우선", "visit_first": "방문지 우선", "balanced": "균형"}
TRAIN_KINDS = ("observe", "ride", "station")


class UnknownPlace(Exception):
    """The start or end place is not in the catalog."""


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def walk_minutes(a: Place, b: Place) -> int | None:
    """Walking time between two places: straight distance x 1.3 at 4.5 km/h, rounded up.
    None when a coordinate is missing or the walk is too long to plan."""
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
    The one rule for filling empty time: the planner and the chat both call this.
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


# --- the world: places, walking, rides --------------------------------------------


@dataclass(frozen=True)
class Ride:
    no: str
    origin: str
    dest: str
    dep: int  # earliest departure of the recorded weeks
    arr: int  # typical arrival (shown)
    late: int  # minutes the arrival may run later (judged)


@dataclass(frozen=True)
class Walk:
    origin: str
    dest: str
    minutes: int


class World:
    """One request's view of the catalog and timetable, with what gets asked repeatedly."""

    def __init__(self, places: Mapping[str, Place], timetable: Timetable):
        self.places = places
        self.timetable = timetable
        self.basis = timetable.basis()
        self.stations = {sid: trains.station_trains(timetable, sid)
                         for sid in timetable.stations if sid in places}
        self.boardable = [sid for sid, s in self.stations.items() if s.boarding]
        self._rows = {sid: sorted(((_minutes(r.time), r) for r in s.trains), key=lambda x: x[0])
                      for sid, s in self.stations.items()}
        self.rides: dict[tuple[str, str], list[Ride]] = {}
        for a in self.boardable:
            for b in self.boardable:
                if a != b:
                    self.rides[(a, b)] = [
                        Ride(t.no, a, b, _minutes(t.dep), _minutes(t.arr),
                             trains.stop_spread(timetable, t.no, b))
                        for t in trains.search(timetable, a, b).trains
                        if _minutes(t.arr) >= _minutes(t.dep)  # not past midnight
                    ]
        self._walks: dict[tuple[str, str], int | None] = {}
        self._profiles: dict[tuple[str, str], tuple] = {}

    def name(self, place_id: str) -> str:
        place = self.places.get(place_id)
        return place.name if place else place_id

    def rows(self, place_id: str):
        """(minute, StationTrain) for every train at a station, in time order."""
        return self._rows.get(place_id, ())

    def walk(self, a: str, b: str) -> int | None:
        if a == b:
            return 0
        key = (a, b)
        if key not in self._walks:
            pa, pb = self.places.get(a), self.places.get(b)
            self._walks[key] = walk_minutes(pa, pb) if pa and pb else None
        return self._walks[key]

    def _profile(self, a: str, b: str):
        """Train journeys a -> b worth taking: (leave by, safe arrival, legs), each later
        one arriving later. At most two rides, walking only at the ends."""
        profile = self._profiles.get((a, b))
        if profile is not None:
            return profile
        found = []
        for s1 in self.boardable:
            w1 = self.walk(a, s1)
            if w1 is None:
                continue
            head = (Walk(a, s1, w1),) if a != s1 else ()
            for s2 in self.boardable:
                w2 = self.walk(s2, b)
                if s2 == s1 or w2 is None:
                    continue
                tail = (Walk(s2, b, w2),) if b != s2 else ()
                for r in self.rides[(s1, s2)]:
                    found.append((r.dep - MIN_XFER - w1, r.arr + r.late + w2, (*head, r, *tail)))
                for via in self.boardable:
                    if via in (s1, s2):
                        continue
                    for r1 in self.rides[(s1, via)]:
                        ready = r1.arr + r1.late + MIN_XFER
                        r2 = min((r for r in self.rides[(via, s2)]
                                  if r.dep >= ready and r.no != r1.no),
                                 key=lambda r: r.arr + r.late, default=None)
                        if r2 is not None:
                            found.append((r1.dep - MIN_XFER - w1, r2.arr + r2.late + w2,
                                          (*head, r1, r2, *tail)))
        found.sort(key=lambda j: (-j[0], j[1], len(j[2])))
        kept, best = [], math.inf
        for journey in found:
            if journey[1] < best:
                kept.append(journey)
                best = journey[1]
        kept.reverse()
        profile = (self.walk(a, b), [j[0] for j in kept], kept)
        self._profiles[(a, b)] = profile
        return profile

    def journey(self, a: str, t: int, b: str) -> tuple[int, tuple] | None:
        """Earliest safe arrival at b when free at a from t, and the legs to get there."""
        if a == b:
            return t, ()
        walk, leaves, kept = self._profile(a, b)
        best = None
        i = bisect.bisect_left(leaves, t)
        if i < len(kept):
            best = (kept[i][1], kept[i][2])
        if walk is not None and (best is None or t + walk <= best[0]):
            best = (t + walk, (Walk(a, b, walk),))
        return best

    def arrival(self, a: str, t: int, b: str) -> int | None:
        found = self.journey(a, t, b)
        return found[0] if found else None


# --- items as things to schedule ---------------------------------------------------


@dataclass(frozen=True)
class Alt:
    """One way to do an item. Fixed (a train): be at `place` by `ready_by`, free at
    `free_at` in `out`. A stay has `stay` minutes and no fixed time."""
    place: str
    out: str
    stay: int = 0
    ready_by: int | None = None
    at: int | None = None  # shown: when the train comes / leaves
    free_at: int | None = None
    arrive: int | None = None  # shown arrival of a ride
    late: int = 0
    passing: bool = False
    basis: str | None = None


def _popcount(value: int) -> int:
    return bin(value).count("1")


class Planner:
    """Every way to order the items is searched once; modes only pick among the results."""

    def __init__(self, world: World, request: PlanRequest):
        for anchor in (request.start, request.end):
            if anchor.place not in world.places:
                raise UnknownPlace(anchor.place)
        self.w = world
        self.r = request
        self.items = request.items
        self.t0, self.t1 = _minutes(request.start.time), _minutes(request.end.time)
        self.invalid: dict[int, str] = {}
        self.alts = [self._alternatives(i, item) for i, item in enumerate(self.items)]
        self.must = sum(1 << i for i, item in enumerate(self.items) if item.must)
        self.states, self.finals = self._search()

    # ----- items -> alternatives

    def _alternatives(self, index: int, item) -> list[Alt]:
        w = self.w
        if isinstance(item, StayItem):
            if item.place not in w.places:
                self.invalid[index] = f"모르는 장소예요({item.place})"
                return []
            return [Alt(item.place, item.place, stay=item.stay_min)]
        if trains.detail(w.timetable, item.train) is None:
            self.invalid[index] = f"{item.train} 열차는 시간표에 없어요"
            return []
        if isinstance(item, RideItem):
            for place in (item.from_, item.to):
                if place not in w.places:
                    self.invalid[index] = f"모르는 장소예요({place})"
                    return []
                if place not in w.boardable:
                    self.invalid[index] = (
                        f"{w.name(place)}에서는 열차를 타고 내릴 수 없어요"
                        "(여객열차가 서지 않는 통과역·화물역, 관찰만 가능)")
                    return []
            ride = next((r for r in w.rides.get((item.from_, item.to), ())
                         if r.no == item.train), None)
            if ride is None:
                self.invalid[index] = (f"{item.train} 열차로는 {w.name(item.from_)}에서 "
                                       f"{w.name(item.to)}까지 갈 수 없어요")
                return []
            return [Alt(ride.origin, ride.dest, ready_by=ride.dep - MIN_XFER, at=ride.dep,
                        free_at=ride.arr + ride.late, arrive=ride.arr, late=ride.late)]
        if item.place is not None and item.place not in w.places:
            self.invalid[index] = f"모르는 장소예요({item.place})"
            return []
        alts = [alt for sid in ([item.place] if item.place else w.stations)
                if (alt := self._sighting(item.train, sid)) is not None]
        if not alts:
            where = f"{w.name(item.place)} 앞을" if item.place else "이 지역 역을"
            self.invalid[index] = f"{item.train} 열차는 {where} 지나지 않아요"
        return alts

    def _sighting(self, train_no: str, station_id: str) -> Alt | None:
        w = self.w
        row = next((r for _, r in w.rows(station_id) if r.no == train_no), None)
        if row is None:
            return None
        if row.passing or row.arr:
            # may come as early, or as late, as the recorded weeks differed
            at = _minutes(row.time if row.passing else row.arr)
            spread = trains.stop_spread(w.timetable, train_no, station_id)
            early, late = at - spread, at + spread
        else:
            at = early = late = _minutes(row.dep)  # starts here: at the platform until then
        return Alt(station_id, station_id, ready_by=early - READY, at=at, free_at=late,
                   passing=row.passing, basis=row.time_basis or f"{w.basis.summary}의 어림 시각")

    # ----- search

    def _search(self):
        """states[(done mask, last item, its alternative)] = (earliest safe free time,
        moves, previous state). Being free earlier is never worse, so one value per state
        is enough."""
        w, t1 = self.w, self.t1
        start = (0, -1, 0)
        states = {start: (self.t0, 0, None)}
        layer = [start]
        while layer:
            reached: dict = {}
            for key in layer:
                mask, i, k = key
                t, moves, _ = states[key]
                loc = self.r.start.place if i < 0 else self.alts[i][k].out
                for j, alts in enumerate(self.alts):
                    if mask >> j & 1:
                        continue
                    for k2, alt in enumerate(alts):
                        arrive = w.arrival(loc, t, alt.place)
                        if arrive is None:
                            continue
                        if alt.ready_by is None:
                            free = arrive + alt.stay
                        elif arrive <= alt.ready_by:
                            free = alt.free_at
                        else:
                            continue
                        if free > t1:
                            continue
                        value = (free, moves + (loc != alt.place))
                        key2 = (mask | 1 << j, j, k2)
                        if key2 not in reached or value < reached[key2][:2]:
                            reached[key2] = (*value, key)
            states.update(reached)
            layer = list(reached)
        finals = []
        for key, (t, moves, _) in states.items():
            mask, i, k = key
            loc = self.r.start.place if i < 0 else self.alts[i][k].out
            home = w.arrival(loc, t, self.r.end.place)
            if home is not None and home <= t1:
                finals.append((mask, moves + (loc != self.r.end.place), home, key))
        return states, finals

    def _score(self, mask: int, mode: str) -> int:
        train, visit = WEIGHTS[mode]
        return sum(train if item.kind in TRAIN_KINDS else visit
                   for i, item in enumerate(self.items) if mask >> i & 1)

    def _pick(self, mode: str, keep_musts: bool):
        pool = [f for f in self.finals if not keep_musts or f[0] & self.must == self.must]
        if not pool:
            return None
        return max(pool, key=lambda f: (
            _popcount(f[0] & self.must), self._score(f[0], mode), -f[1], -f[2], -f[0], f[3]))

    def _path(self, key) -> list[tuple[int, Alt]]:
        path = []
        while key is not None and key[1] >= 0:
            path.append((key[1], self.alts[key[1]][key[2]]))
            key = self.states[key][2]
        return path[::-1]

    def fits(self, wanted: int) -> bool:
        """Can all the items in `wanted` be in one day?"""
        return any(f[0] & wanted == wanted for f in self.finals)

    # ----- why an item is not in the plan

    def _label(self, index: int) -> str:
        item = self.items[index]
        if isinstance(item, StayItem):
            return self.w.name(item.place)
        return f"{item.train} 열차 {'타기' if isinstance(item, RideItem) else '보기'}"

    def _when(self, index: int) -> str | None:
        """'before' / 'after' when every chance to do a train item is outside the day."""
        alts = [a for a in self.alts[index] if a.at is not None]
        if alts and all(a.at < self.t0 for a in alts):
            return "before"
        if alts and all(a.at > self.t1 for a in alts):
            return "after"
        return None

    def _why(self, index: int, kept: tuple[int, ...] = ()) -> str:
        w, item = self.w, self.items[index]
        if index in self.invalid:
            return self.invalid[index]
        when = self._when(index)
        if when == "before":
            return f"하루를 시작하는 {self.r.start.time}보다 먼저 지나가요"
        if when == "after":
            return f"돌아가야 하는 {self.r.end.time}보다 늦게 지나가요"
        verb = "탈" if isinstance(item, RideItem) else "볼"
        first = None
        for alt in self.alts[index]:
            name = w.name(alt.place)
            arrive = w.arrival(self.r.start.place, self.t0, alt.place)
            if arrive is None:
                no_coordinates = w.walk(self.r.start.place, alt.place) is None and (
                    w.places[alt.place].lat is None or w.places[self.r.start.place].lat is None)
                how = ("갈 열차가 없고, 좌표가 아직 없어 걸어가는 시간도 계산할 수 없어요"
                       if no_coordinates else "갈 열차가 없고 걸어가기에는 너무 멀어요")
                reason = (f"{item.train} 열차는 {name}에 {_clock(alt.at)}에 오는데, "
                          f"그 전에 {name}까지 {how}" if alt.at is not None
                          else f"{name}까지 {how}")
            elif alt.ready_by is not None and arrive > alt.ready_by:
                reason = (f"{name}에는 빨라도 {_clock(arrive)}에 도착할 수 있어서 "
                          f"{_clock(alt.at)}의 {item.train} 열차를 {verb} 수 없어요")
            else:
                free = alt.free_at if alt.ready_by is not None else arrive + alt.stay
                home = w.arrival(alt.out, free, self.r.end.place)
                if free <= self.t1 and home is not None and home <= self.t1:
                    break  # possible on its own: it is the other items that get in the way
                reason = (f"{self._label(index)} 뒤에는 {self.r.end.time}까지 "
                          f"{w.name(self.r.end.place)}에 돌아갈 수 없어요")
            first = first or reason
        else:
            return first or "넣을 수 있는 시간이 없어요"
        clashing = [self._label(k) for k in kept if not self.fits(1 << index | 1 << k)]
        if clashing:
            return f"꼭 넣기로 한 {', '.join(clashing[:2])}와(과) 시간이 겹쳐요"
        return "다른 일정과 시간이 겹쳐요"

    def _outside(self, included: Collection[int]) -> list[Outside]:
        out = []
        for index, item in enumerate(self.items):
            when = self._when(index)
            if index in included or when is None:
                continue
            at = (max if when == "before" else min)(a.at for a in self.alts[index])
            out.append(Outside(
                item=index, train=item.train, at=_clock(at),
                hint=("전날 와서 묵거나 더 일찍 시작하면 볼 수 있어요" if when == "before"
                      else "하룻밤 묵으면 볼 수 있어요")))
        return out

    # ----- options for a must item that cannot be kept

    def _candidates(self, index: int):
        w, item = self.w, self.items[index]
        if isinstance(item, ObserveItem):
            now = self.alts[index][0].at if self.alts[index] else None
            seen = [(sid, alt.at) for sid in w.stations
                    if sid != item.place and (alt := self._sighting(item.train, sid))]
            if now is not None:
                seen.sort(key=lambda s: abs(s[1] - now))
            for sid, _ in seen:
                yield f"{w.name(sid)}에서 보기", item.model_copy(update={"place": sid})
        elif isinstance(item, RideItem):
            if index in self.invalid:  # a station where trains do not stop: same train elsewhere
                for (a, b), rides in w.rides.items():
                    if (a == item.from_ or b == item.to) and any(
                            r.no == item.train for r in rides):
                        yield (f"{w.name(a)}에서 {w.name(b)}까지 타기",
                               item.model_copy(update={"from_": a, "to": b}))
            else:
                now = self.alts[index][0].at
                others = [r for r in w.rides[(item.from_, item.to)] if r.no != item.train]
                for r in sorted(others, key=lambda r: abs(r.dep - now)):
                    yield (f"{r.no} 열차({_clock(r.dep)})로 바꾸기",
                           item.model_copy(update={"train": r.no}))
        elif index not in self.invalid and item.stay_min >= 20:
            shorter = item.stay_min // 2
            yield f"머무는 시간을 {shorter}분으로 줄이기", item.model_copy(
                update={"stay_min": shorter})
        yield "꼭 넣기를 풀고 가능하면 넣기", item.model_copy(update={"must": False})

    def _options(self, index: int, dropped: Collection[int], mode: str) -> list[Option]:
        options = []
        for tries, (label, replacement) in enumerate(self._candidates(index)):
            if len(options) == MAX_OPTIONS or tries == MAX_OPTION_TRIES:
                break
            items = list(self.items)
            for other in dropped:  # judged against the musts that can be kept
                items[other] = items[other].model_copy(update={"must": False})
            items[index] = replacement
            trial = Planner(self.w, self.r.model_copy(update={"items": items}))
            best = trial._pick(mode, keep_musts=True)
            if best is not None and (not replacement.must or best[0] >> index & 1):
                options.append(Option(label=label, change=Change(item=index, replace=replacement)))
        return options

    # ----- the plan

    def plan(self, mode: str | None = None) -> Plan:
        mode = mode or self.r.mode
        w, r = self.w, self.r
        basis = f"{w.basis.summary}의 어림 시각"
        if r.date.isoformat() != w.basis.target_date:
            basis += f" (요청한 {r.date.isoformat()}이 아니라 {w.basis.target_date} 토요일 시간표예요)"
        best = self._pick(mode, keep_musts=True)
        if best is None:
            return Plan(feasible=False, summary=None, timeline=[], conflicts=self._conflicts(mode),
                        missed=[], outside=self._outside(()), suggestions=[], basis=basis)
        path = self._path(best[3])
        included = {i for i, _ in path}
        built = _Timeline(self, path)
        suggestions = []
        if r.depth != "reference":
            skip = set(r.dismissed) | {r.start.place, r.end.place}
            for item in self.items:
                skip |= {getattr(item, name, None) for name in ("place", "from_", "to")}
            for place, start, end, sure in built.gaps:
                found = suggest(w.places, place, _clock(start), _clock(end), skip=skip,
                                date=r.date, far_first=mode == "visit_first",
                                fit_from=_clock(max(start, sure)))
                suggestions += found
                skip |= {s.target for s in found}
        return Plan(
            feasible=True, summary=built.summary(),
            timeline=[] if r.depth == "light" else built.steps,
            conflicts=[],
            missed=[Missed(item=i, reason=self._why(i))
                    for i in range(len(self.items)) if i not in included],
            outside=self._outside(included), suggestions=suggestions, basis=basis)

    def _conflicts(self, mode: str) -> list[Conflict]:
        w, r = self.w, self.r
        most = self._pick(mode, keep_musts=False)
        if most is None:
            return [Conflict(item=None, options=[], reason=(
                f"{w.name(r.start.place)}에서 {r.start.time}에 출발해 {r.end.time}까지 "
                f"{w.name(r.end.place)}에 갈 방법이 없어요"))]
        musts = [i for i, item in enumerate(self.items) if item.must]
        kept = tuple(i for i in musts if most[0] >> i & 1)
        dropped = [i for i in musts if i not in kept]
        return [Conflict(item=i, reason=self._why(i, kept),
                         options=self._options(i, dropped, mode)) for i in dropped]


class _Timeline:
    """Replays the chosen order with two clocks: `shown` (typical times, what the user
    reads) and `safe` (worst recorded week, what the search judged with)."""

    def __init__(self, planner: Planner, path: list[tuple[int, Alt]]):
        self.w = planner.w
        r = planner.r
        self.steps: list = [StartStep(at=r.start.time, place=r.start.place)]
        self.loc = r.start.place
        self.shown = self.safe = planner.t0
        self.done: set[str] = set()  # trains already seen or ridden: not "passing" again
        self.seen: set[str] = set()
        self.gaps: list[tuple[str, int, int, int]] = []  # place, from, to, surely there from
        self.walked = self.ridden = self.visits = 0
        for index, alt in path:
            item = planner.items[index]
            self._go(alt.place)
            if isinstance(item, StayItem):
                until = self.shown + alt.stay
                self.steps.append(VisitStep(
                    item=index, place=alt.place, from_=_clock(self.shown), to=_clock(until),
                    passing=self._passing(self.shown - 1, until)))
                self.shown, self.safe = until, self.safe + alt.stay
                self.visits += 1
            elif isinstance(item, RideItem):
                self._board(Ride(item.train, alt.place, alt.out, alt.at, alt.arrive, alt.late),
                            index)
            else:
                margin = alt.at - self.safe
                self._idle(alt.at, item.train)
                self.steps.append(ObserveStep(
                    item=index, train=item.train, place=alt.place, at=_clock(alt.at),
                    passing_through=alt.passing, basis=alt.basis, margin_min=margin))
                self.done.add(item.train)
                self.seen.add(item.train)
                self.shown, self.safe = alt.at, alt.free_at
        self._go(r.end.place)
        self.end_arrival = self.shown
        self._idle(planner.t1)
        self.steps.append(EndStep(at=r.end.time, place=r.end.place))

    def summary(self) -> Summary:
        return Summary(
            seen=len(self.seen), ridden=self.ridden, visits=self.visits, walk_min=self.walked,
            longest_gap_min=max((end - start for _, start, end, _ in self.gaps), default=0),
            end_arrival=_clock(self.end_arrival))

    def _passing(self, after: int, until: int, target: str | None = None) -> list[Passing]:
        found = [Passing(train=row.no, at=row.time) for minute, row in self.w.rows(self.loc)
                 if after < minute <= until and row.no != target and row.no not in self.done]
        self.seen.update(p.train for p in found)
        return found

    def _idle(self, until: int, target: str | None = None) -> None:
        """Time spent where we are. Stretches of GAP_MIN without a train are gaps; the
        rest is waiting, with the trains that come by."""
        start = self.shown
        if until <= start:
            return
        passing = self._passing(start, until, target)
        edges = sorted({_minutes(p.at) for p in passing} | {until})
        waiting = None
        for begin, end in zip([start, *edges], edges):
            here = [p for p in passing if begin < _minutes(p.at) <= end]
            if end - begin >= GAP_MIN:
                waiting = None
                self.steps.append(GapStep(from_=_clock(begin), to=_clock(end), place=self.loc,
                                          note=self._gap_note(), passing=here))
                self.gaps.append((self.loc, begin, end, self.safe))
            elif waiting is None:
                waiting = WaitStep(from_=_clock(begin), to=_clock(end), place=self.loc,
                                   passing=here)
                self.steps.append(waiting)
            else:
                waiting.to = _clock(end)
                waiting.passing.extend(here)
        self.shown, self.safe = until, max(self.safe, until)

    def _gap_note(self) -> str:
        name = self.w.name(self.loc)
        if self.loc in self.w.boardable:
            return f"이 시간에는 {name}에 서는 열차가 없어요. 다른 곳으로 가려면 택시·버스가 필요해요"
        if self.w.rows(self.loc):
            return f"이 시간에는 {name} 앞을 지나는 열차가 없어요"
        return "비어 있는 시간이에요"

    def _go(self, dest: str) -> None:
        if dest == self.loc:
            return
        _, legs = self.w.journey(self.loc, self.safe, dest)
        for leg in legs:
            if isinstance(leg, Ride):
                self._board(leg, None)
            else:
                self.steps.append(WalkStep(
                    from_=leg.origin, to=leg.dest, depart=_clock(self.shown),
                    arrive=_clock(self.shown + leg.minutes), min=leg.minutes))
                self.shown += leg.minutes
                self.safe += leg.minutes
                self.walked += leg.minutes
                self.loc = leg.dest

    def _board(self, ride: Ride, item: int | None) -> None:
        margin = ride.dep - self.safe
        self._idle(ride.dep, ride.no)
        self.steps.append(RideStep(
            item=item, train=ride.no, from_=ride.origin, depart=_clock(ride.dep),
            to=ride.dest, arrive=_clock(ride.arr), margin_min=margin))
        self.done.add(ride.no)
        self.ridden += 1
        self.loc = ride.dest
        self.shown, self.safe = ride.arr, ride.arr + ride.late


# --- public functions ---------------------------------------------------------------


def make_plan(request: PlanRequest, places: Mapping[str, Place], timetable: Timetable) -> Plan:
    """Raises UnknownPlace when the start or end place is not in the catalog."""
    return Planner(World(places, timetable), request).plan()


def _feature(mode: str, plan: Plan, total: int) -> str:
    s = plan.summary
    if s is None:
        return "꼭 넣기로 한 항목이 서로 맞지 않아 이 안은 만들 수 없어요"
    if mode == "train_first":
        return f"열차를 먼저 담았어요: {s.seen}편을 보고 {s.ridden}번 타요"
    if mode == "visit_first":
        return f"방문지를 먼저 담았어요: {s.visits}곳에 머물고 {s.walk_min}분 걸어요"
    return (f"담은 {total}개 중 {total - len(plan.missed)}개를 넣었어요: "
            f"가장 긴 빈 시간은 {s.longest_gap_min}분이에요")


def variants(request: PlanRequest, places: Mapping[str, Place], timetable: Timetable) -> Variants:
    """The same request three ways. Must items are identical in all of them."""
    planner = Planner(World(places, timetable), request)
    out = []
    for mode in WEIGHTS:
        plan = planner.plan(mode)
        out.append(Variant(mode=mode, label=MODE_LABELS[mode], summary=plan.summary, plan=plan,
                           feature=_feature(mode, plan, len(request.items))))
    return Variants(variants=out)


def _got(plan: Plan) -> dict[tuple[str, str], str]:
    """What a plan gives: (kind, id) -> how, in timeline order."""
    got: dict[tuple[str, str], str] = {}
    for step in plan.timeline:
        if step.type in ("observe", "ride"):
            got[("train", step.train)] = step.type  # stronger than having passed by
        for passing in getattr(step, "passing", ()):
            got.setdefault(("train", passing.train), "passing")
        if step.type == "visit":
            got[("place", step.place)] = "visit"
    return got


def compare(base: PlanRequest, changed: PlanRequest, places: Mapping[str, Place],
            timetable: Timetable) -> Comparison:
    """Both plans and what the change gains and loses (trains and places)."""
    world = World(places, timetable)
    full = {"depth": "full"}  # the difference needs both timelines
    a = Planner(world, base.model_copy(update=full)).plan()
    b = Planner(world, changed.model_copy(update=full)).plan()
    before, after = _got(a), _got(b)
    diff = Diff(
        gained=[Delta(kind=k, id=i, how=how) for (k, i), how in after.items()
                if (k, i) not in before],
        lost=[Delta(kind=k, id=i, how=how) for (k, i), how in before.items()
              if (k, i) not in after],
        end_arrival=[p.summary.end_arrival if p.summary else None for p in (a, b)],
        gap_change_min=(b.summary.longest_gap_min - a.summary.longest_gap_min
                        if a.summary and b.summary else None))
    return Comparison(base=a, changed=b, diff=diff)
