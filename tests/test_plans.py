"""Day planner (modules/plans): the product rules as tests, written before the code.

Rule tests run on a small made-up line (stations A, B, C and a passing station P) so each
rule has a case where it must bite and one where it must not. The scenarios at the end run
on the committed timetable snapshot. No LLM is involved anywhere.
"""

import datetime as dt
import unittest

from fastapi.testclient import TestClient
from legacy_support import create_app

from neareast_api.core.config import Settings
from neareast_api.db.entities import PlaceEntity
from neareast_api.db.session import CONTENT_SESSION
from neareast_api.ingestion.service import IngestionService
from neareast_api.modules.places import service as places_service
from neareast_api.modules.plans import service as plans
from neareast_api.modules.trains import service as trains_service

API = "/api/v1"
A, B, C, P = "station_a", "station_b", "station_c", "station_p"
NEAR_A, NEAR_B, FAR, NO_COORD = "poi_near_a", "poi_near_b", "poi_far", "poi_nocoord"


def minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def hhmm(value):
    return f"{value // 60:02d}:{value % 60:02d}"


# --- a made-up line: A - B - (P passes) - C, far apart so nobody walks between stations ---

PLACES = [
    {"id": A, "name": "가역", "kind": "station", "passenger_stop": True, "lat": 37.50, "lng": 129.10},
    {"id": B, "name": "나역", "kind": "station", "passenger_stop": True, "lat": 37.70, "lng": 129.10},
    {"id": C, "name": "다역", "kind": "station", "passenger_stop": True, "lat": 37.90, "lng": 129.10},
    # 1.1 km north of B: a 20 minute walk
    {"id": P, "name": "통과역", "kind": "station", "passenger_stop": False, "lat": 37.71, "lng": 129.10},
    # 0.33 km from their stations: a 6 minute walk
    {"id": NEAR_A, "name": "가까운곳", "kind": "poi", "lat": 37.503, "lng": 129.10},
    {"id": NEAR_B, "name": "나역앞", "kind": "poi", "lat": 37.703, "lng": 129.10},
    {"id": FAR, "name": "먼곳", "kind": "poi", "lat": 37.50, "lng": 129.40},
    {"id": NO_COORD, "name": "좌표없는곳", "kind": "poi", "lat": None, "lng": None},
    {"id": "poi_bukpyeong_market", "name": "북평민속시장", "kind": "poi", "lat": 37.497, "lng": 129.10},
]


def _stop(name, place_id, arr, dep, stop_type="여객승하차", spread=0):
    return {"name": name, "place_id": place_id, "code": None, "arr": arr, "dep": dep,
            "stop_type": stop_type, "line": "가상선", "weeks": 3, "spread_min": spread}


def _pass(at):
    return {"name": "통과역", "place_id": P, "code": None, "arr": None, "dep": None,
            "pass": hhmm(at), "stop_type": "통과(어림)", "basis": "앞뒤 정차역 시각으로 어림"}


def _train(no, direction, stops, spread=0):
    return {"no": no, "line": "가상선", "direction": direction, "origin": stops[0]["name"],
            "destination": stops[-1]["name"], "saturdays_run": [], "weekly_saturday": True,
            "spread_min": spread, "in_plan": True, "stops": stops}


def down(no, at, b_spread=0):
    """A -> B -> (P) -> C, leaving A at `at`."""
    t = minutes(at)
    return _train(no, "D", [
        _stop("가", A, None, hhmm(t), "시발"),
        _stop("나", B, hhmm(t + 10), hhmm(t + 11), spread=b_spread),
        _pass(t + 15),
        _stop("다", C, hhmm(t + 20), None, "종착"),
    ], b_spread)


def up(no, at):
    """C -> (P) -> B -> A, leaving C at `at`."""
    t = minutes(at)
    return _train(no, "U", [
        _stop("다", C, None, hhmm(t), "시발"),
        _pass(t + 5),
        _stop("나", B, hhmm(t + 10), hhmm(t + 11)),
        _stop("가", A, hhmm(t + 20), None, "종착"),
    ])


def snapshot(b_spread_105=0):
    trains = [
        down("101", "09:00"), down("103", "11:00"), down("105", "13:00", b_spread_105),
        down("107", "15:00"), down("109", "21:00"),
        up("102", "10:00"), up("104", "11:05"), up("106", "11:30"), up("108", "16:00"),
        up("110", "17:30"),
        # starts at B seven minutes after 105 arrives there
        _train("201", "D", [_stop("나", B, None, "13:17", "시발"), _pass(13 * 60 + 21),
                            _stop("다", C, "13:26", None, "종착")]),
    ]
    return {
        "meta": {"target_date": "2026-11-21", "certainty": "estimated",
                 "basis": {"summary": "가상 시간표 기준", "record_dates": ["2026-11-14"],
                           "plan_date": "2026-12-19"},
                 "limits": ["테스트용 가상 데이터"]},
        "stations": {A: {"name": "가", "code": None, "records": True},
                     B: {"name": "나", "code": None, "records": True},
                     C: {"name": "다", "code": None, "records": True},
                     P: {"name": "통과", "code": None, "records": False}},
        "trains": trains,
    }


def world_app(**kwargs):
    """An app on an empty database holding only the made-up places and timetable."""
    app = create_app(Settings(), seed=False)
    with app.state.sessionFactory() as session:
        for place in PLACES:
            session.add(PlaceEntity(
                id=place["id"], name=place["name"], kind=place["kind"],
                passengerStop=place.get("passenger_stop"), aliases=place.get("aliases", []),
                lat=place.get("lat"), lng=place.get("lng")))
        session.commit()
        IngestionService.importTimetable(session, snapshot(**kwargs))
    return app


def open_content(case, app):
    """Let a test call the place/timetable services directly, as a request would."""
    session = app.state.sessionFactory()
    token = CONTENT_SESSION.set(session)

    def close():
        CONTENT_SESSION.reset(token)
        session.close()
        app.state.databaseEngine.dispose()
    (case.addClassCleanup if isinstance(case, type) else case.addCleanup)(close)
    return session


def request(items, start=(A, "08:30"), end=(A, "18:00"), **extra):
    return {"date": "2026-11-21",
            "start": {"place": start[0], "time": start[1]},
            "end": {"place": end[0], "time": end[1]},
            "items": items, **extra}


def observe(train, place, must=False):
    return {"kind": "observe", "train": train, "place": place, "must": must}


def ride(train, from_, to, must=False):
    return {"kind": "ride", "train": train, "from": from_, "to": to, "must": must}


def visit(place, stay=30, must=False, kind="visit"):
    return {"kind": kind, "place": place, "stay_min": stay, "must": must}


class _WorldCase(unittest.TestCase):
    world_options: dict = {}

    @classmethod
    def setUpClass(cls):
        app = world_app(**cls.world_options)
        cls.client = TestClient(app)
        open_content(cls, app)
        cls.rides = {}
        timetable = trains_service.load(".")
        for a in (A, B, C):
            for b in (A, B, C):
                if a != b:
                    for t in trains_service.search(timetable, a, b).trains:
                        cls.rides[(t.no, a, b)] = (t.dep, t.arr)

    def post(self, path, body, status=200):
        response = self.client.post(API + path, json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def plan(self, items, **kwargs):
        body = self.post("/plans", request(items, **kwargs))
        if body["feasible"]:
            self.check_timeline(body, request(items, **kwargs))
        return body

    def items_in(self, body):
        return {s["item"] for s in body["timeline"] if s.get("item") is not None}

    def steps(self, body, kind):
        return [s for s in body["timeline"] if s["type"] == kind]

    def check_timeline(self, body, req):
        """What every feasible plan must satisfy, whatever the request."""
        timeline = body["timeline"]
        self.assertEqual(timeline[0], {"type": "start", "at": req["start"]["time"],
                                       "place": req["start"]["place"]})
        self.assertEqual(timeline[-1], {"type": "end", "at": req["end"]["time"],
                                        "place": req["end"]["place"]})
        clock = minutes(req["start"]["time"])
        for step in timeline:
            kind = step["type"]
            begin = step.get("at") or step.get("depart") or step["from"]
            finish = step.get("at") or step.get("arrive") or step["to"]
            self.assertGreaterEqual(minutes(begin), clock, step)
            self.assertGreaterEqual(minutes(finish), minutes(begin), step)
            clock = minutes(finish)
            if kind == "ride":
                key = (step["train"], step["from"], step["to"])
                self.assertEqual(self.rides.get(key), (step["depart"], step["arrive"]), step)
                self.assertGreaterEqual(step["margin_min"], plans.MIN_XFER, step)
                self.assertEqual(step["certainty"], "estimated")
            if kind == "observe":
                self.assertGreaterEqual(step["margin_min"], plans.READY, step)
                self.assertEqual(step["certainty"], "estimated")
            if kind == "gap":
                self.assertGreaterEqual(minutes(step["to"]) - minutes(step["from"]),
                                        plans.GAP_MIN, step)
        self.assertLessEqual(clock, minutes(req["end"]["time"]))
        # every pinned (must) item is in the plan
        for index, item in enumerate(req["items"]):
            if item.get("must"):
                self.assertIn(index, self.items_in(body))
        self.assertEqual(body["conflicts"], [])
        for suggestion in body["suggestions"]:
            self.assertIs(suggestion["needs_confirm"], True)


class MustItemTests(_WorldCase):
    """원칙: 핀 불가침. A must item is never dropped or changed by the server."""

    # Seeing 104 at C (11:05) needs the 09:00 train; then 102 and 103 at A cannot be seen.
    ITEMS = [("104", C), ("102", A), ("103", A)]

    def test_must_item_wins_over_a_larger_number_of_optional_items(self):
        body = self.plan([observe("104", C, must=True), observe("102", A), observe("103", A)])
        self.assertTrue(body["feasible"])
        self.assertEqual(self.items_in(body), {0})
        self.assertEqual([s["train"] for s in self.steps(body, "observe")], ["104"])
        self.assertEqual({m["item"] for m in body["missed"]}, {1, 2})
        self.assertTrue(all(m["reason"] for m in body["missed"]))

    def test_without_must_the_plan_keeps_as_many_items_as_possible(self):
        body = self.plan([observe("104", C), observe("102", A), observe("103", A)])
        self.assertEqual(self.items_in(body), {1, 2})
        self.assertEqual([m["item"] for m in body["missed"]], [0])

    def test_impossible_must_gives_no_plan_but_a_reason_and_two_options(self):
        # 102 reaches A at 10:20 and 104 leaves C at 11:05: nobody can see both.
        items = [observe("102", A, must=True), observe("104", C, must=True)]
        body = self.plan(items)
        self.assertFalse(body["feasible"])
        self.assertEqual(body["timeline"], [])
        self.assertIsNone(body["summary"])
        self.assertEqual(body["suggestions"], [])
        self.assertTrue(body["conflicts"])
        for conflict in body["conflicts"]:
            self.assertIn(conflict["item"], (0, 1))
            self.assertTrue(conflict["reason"])
            self.assertEqual(len(conflict["options"]), 2)
            for option in conflict["options"]:
                self.assertTrue(option["label"])
                self.assertEqual(option["change"]["item"], conflict["item"])
                # the option is something the user applies: the server verified it works
                changed = [dict(i) for i in items]
                changed[conflict["item"]] = option["change"]["replace"]
                after = self.plan(changed)
                self.assertTrue(after["feasible"], option)

    def test_must_items_are_returned_exactly_as_pinned(self):
        body = self.plan([observe("103", B, must=True), ride("107", A, C, must=True)])
        seen, = self.steps(body, "observe")
        self.assertEqual((seen["item"], seen["train"], seen["place"]), (0, "103", B))
        pinned = [s for s in self.steps(body, "ride") if s["item"] == 1]
        self.assertEqual([(s["train"], s["from"], s["to"]) for s in pinned], [("107", A, C)])


class SuggestionTests(_WorldCase):
    """원칙: 제안은 제안. Gaps stay empty; places to go are suggestions only."""

    def test_gap_stays_a_gap_and_the_place_is_only_suggested(self):
        body = self.plan([observe("102", A, must=True)], end=(A, "12:30"))
        gaps = self.steps(body, "gap")
        self.assertIn(("09:00", "10:20"), [(g["from"], g["to"]) for g in gaps])
        self.assertTrue(all(g["place"] == A and g["note"] for g in gaps))
        near = [s for s in body["suggestions"] if s["target"] == NEAR_A]
        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["type"], "add")
        self.assertEqual(near[0]["gap"], ["09:00", "10:20"])
        self.assertEqual(near[0]["certainty"], "estimated")
        self.assertIn("가역", near[0]["reason"])
        self.assertIn("09:00", near[0]["reason"])
        # never placed on the timeline
        self.assertEqual(self.steps(body, "visit"), [])
        self.assertEqual(self.steps(body, "walk"), [])
        places = {s.get("place") for s in body["timeline"]} | {
            s.get("to") for s in body["timeline"]}
        self.assertFalse({s["target"] for s in body["suggestions"]} & places)

    def test_only_places_that_fit_in_the_gap_are_suggested(self):
        body = self.plan([observe("102", A, must=True)], end=(A, "12:30"))
        targets = [s["target"] for s in body["suggestions"]]
        self.assertNotIn(FAR, targets)  # too far to walk there and back
        self.assertNotIn(NO_COORD, targets)  # walking time unknown
        self.assertNotIn(A, targets)  # the station of the gap itself
        for suggestion in body["suggestions"]:
            start, end = (minutes(t) for t in suggestion["gap"])
            need = 2 * suggestion["walk_min"] + suggestion["stay_min"] + plans.RETURN_SLACK
            self.assertLessEqual(need, end - start)
        # 08:30-09:00 is a gap too, but 30 minutes fit nothing
        self.assertNotIn(["08:30", "09:00"], [s["gap"] for s in body["suggestions"]])

    def test_at_most_two_suggestions_per_gap(self):
        body = self.plan([observe("108", A, must=True)], start=(A, "11:55"), end=(A, "17:00"))
        per_gap = {}
        for suggestion in body["suggestions"]:
            per_gap.setdefault(tuple(suggestion["gap"]), []).append(suggestion["target"])
        self.assertTrue(per_gap)
        self.assertTrue(all(len(v) <= plans.MAX_SUGGESTIONS_PER_GAP for v in per_gap.values()))

    def test_no_gap_no_suggestion(self):
        # trains come by every 25 minutes or less: nothing to fill
        body = self.plan([observe("106", A, must=True)], start=(A, "10:50"), end=(A, "12:00"))
        self.assertEqual(self.steps(body, "gap"), [])
        self.assertEqual(body["suggestions"], [])

    def test_dismissed_and_already_pinned_places_are_not_suggested(self):
        items = [observe("102", A, must=True)]
        base = self.plan(items, end=(A, "12:30"))
        self.assertIn(NEAR_A, [s["target"] for s in base["suggestions"]])
        dismissed = self.plan(items, end=(A, "12:30"), dismissed=[NEAR_A])
        self.assertNotIn(NEAR_A, [s["target"] for s in dismissed["suggestions"]])
        # another place may take its slot; the timeline itself does not change
        self.assertEqual(dismissed["timeline"], base["timeline"])
        pinned = self.plan([*items, visit(NEAR_A, must=True)], end=(A, "12:30"))
        self.assertNotIn(NEAR_A, [s["target"] for s in pinned["suggestions"]])
        self.assertEqual([v["place"] for v in self.steps(pinned, "visit")], [NEAR_A])

    def test_market_suggestion_says_whether_it_is_a_market_day(self):
        items = [observe("102", A, must=True)]
        saturday = self.plan(items, end=(A, "12:30"))
        market, = [s for s in saturday["suggestions"] if s["target"] == "poi_bukpyeong_market"]
        self.assertTrue(any("장날이 아니에요" in n and "11/23" in n for n in market["notes"]))
        body = request(items, end=(A, "12:30"))
        body["date"] = "2026-11-23"
        monday = self.post("/plans", body)
        market, = [s for s in monday["suggestions"] if s["target"] == "poi_bukpyeong_market"]
        self.assertTrue(any("장날이에요" in n for n in market["notes"]))

    def test_reference_depth_gets_no_suggestions_and_light_no_timeline(self):
        items = [observe("102", A, must=True)]
        reference = self.post("/plans", request(items, end=(A, "12:30"), depth="reference"))
        self.assertEqual(reference["suggestions"], [])
        self.assertTrue(reference["timeline"])
        light = self.post("/plans", request(items, end=(A, "12:30"), depth="light"))
        self.assertTrue(light["feasible"])
        self.assertEqual(light["timeline"], [])
        self.assertEqual(light["summary"], reference["summary"])


class PassingStationTests(_WorldCase):
    """원칙: 통과역은 관찰만."""

    def test_boarding_at_a_passing_station_is_refused(self):
        body = self.plan([ride("103", P, C, must=True)], start=(B, "10:00"), end=(C, "18:00"))
        self.assertFalse(body["feasible"])
        conflict, = body["conflicts"]
        self.assertEqual(conflict["item"], 0)
        self.assertIn("통과", conflict["reason"])

    def test_alighting_at_a_passing_station_is_refused_but_the_day_goes_on(self):
        body = self.plan([ride("103", A, P), observe("103", A)], end=(A, "12:00"))
        self.assertTrue(body["feasible"])
        self.assertEqual(self.items_in(body), {1})
        missed, = body["missed"]
        self.assertEqual(missed["item"], 0)
        self.assertIn("통과", missed["reason"])

    def test_observing_at_a_passing_station_is_fine_and_reached_on_foot(self):
        body = self.plan([observe("103", P, must=True)], start=(B, "10:00"), end=(B, "12:30"))
        self.assertTrue(body["feasible"])
        seen, = self.steps(body, "observe")
        self.assertEqual((seen["place"], seen["at"]), (P, "11:15"))
        self.assertEqual(seen["basis"], "앞뒤 정차역 시각으로 어림")
        walks = self.steps(body, "walk")
        self.assertEqual([(w["from"], w["to"], w["min"]) for w in walks],
                         [(B, P, 20), (P, B, 20)])
        self.assertTrue(all(w["certainty"] == "estimated" for w in walks))
        self.assertEqual(body["summary"]["walk_min"], 40)

    def test_no_route_ever_uses_a_passing_station_to_board_or_alight(self):
        # NEAR_B is a short walk from B; P is on the way but trains do not stop there.
        body = self.plan([visit(NEAR_B, must=True), observe("106", P, must=True)],
                         start=(A, "08:30"), end=(C, "18:00"))
        self.assertTrue(body["feasible"])
        for step in self.steps(body, "ride"):
            self.assertNotIn(P, (step["from"], step["to"]))

    def test_a_place_without_coordinates_cannot_be_walked_to(self):
        body = self.plan([visit(NO_COORD)])
        missed, = body["missed"]
        self.assertIn("좌표", missed["reason"])
        blocked = self.plan([visit(NO_COORD, must=True)])
        self.assertFalse(blocked["feasible"])
        self.assertIn("좌표", blocked["conflicts"][0]["reason"])


class ConservativeTimeTests(_WorldCase):
    """1장: 105 reaches B at 13:10 on a typical day, 201 leaves B at 13:17."""

    ITEMS = [ride("105", A, B, must=True), ride("201", B, C, must=True)]

    def test_transfer_holds_when_the_arrival_never_ran_late(self):
        body = self.plan(self.ITEMS, start=(A, "12:00"), end=(C, "18:00"))
        self.assertTrue(body["feasible"])
        second = [s for s in self.steps(body, "ride") if s["item"] == 1][0]
        self.assertEqual((second["depart"], second["margin_min"]), ("13:17", 7))


class LateArrivalTests(_WorldCase):
    """Same trains, but 105 ran up to 6 minutes late at B in the recorded weeks."""

    world_options = {"b_spread_105": 6}

    def test_transfer_is_refused_when_the_worst_week_would_miss_it(self):
        # typical: 13:10 + 5 <= 13:17. worst: 13:16 + 5 > 13:17.
        items = ConservativeTimeTests.ITEMS
        body = self.plan(items, start=(A, "12:00"), end=(C, "18:00"))
        self.assertFalse(body["feasible"])
        self.assertTrue(body["conflicts"])
        labels = [o["label"] for c in body["conflicts"] for o in c["options"]]
        self.assertTrue(any("107" in label for label in labels), labels)  # the next train

    def test_shown_times_stay_typical_and_the_margin_is_the_worst_case(self):
        body = self.plan([ride("105", A, B, must=True), ride("107", B, C, must=True)],
                         start=(A, "12:00"), end=(C, "18:00"))
        first, second = [s for s in self.steps(body, "ride") if s["item"] is not None]
        self.assertEqual(first["arrive"], "13:10")  # shown: the typical arrival
        self.assertEqual(second["depart"], "15:11")
        self.assertEqual(second["margin_min"], 115)  # judged from 13:16, the latest arrival

    def test_observation_needs_being_there_before_the_earliest_pass(self):
        # 105 at B: typical 13:10, as early as 13:04, so be there by 13:02.
        # NEAR_B is a 6 minute walk away; the typical time alone would allow 13:08.
        late = self.plan([observe("105", B)], start=(NEAR_B, "12:57"), end=(B, "14:00"))
        self.assertEqual([m["item"] for m in late["missed"]], [0])
        on_time = self.plan([observe("105", B)], start=(NEAR_B, "12:56"), end=(B, "14:00"))
        seen, = self.steps(on_time, "observe")
        self.assertEqual((seen["at"], seen["margin_min"]), ("13:10", 8))


class VariantTests(_WorldCase):
    # After 102 at B (10:10) there is time for 106 at A or for four hours near B, not both,
    # before 108 leaves C at 16:00.
    def body(self):
        return request([
            observe("102", B, must=True),
            observe("106", A),
            visit(NEAR_B, stay=240),
            ride("108", C, A, must=True),
        ])

    def test_three_variants_keep_every_must_item(self):
        result = self.post("/plans/variants", self.body())
        self.assertEqual([v["mode"] for v in result["variants"]],
                         ["train_first", "visit_first", "balanced"])
        for variant in result["variants"]:
            plan = variant["plan"]
            self.assertTrue(plan["feasible"])
            self.assertTrue({0, 3} <= self.items_in(plan), variant["mode"])
            self.assertTrue(variant["label"])
            self.assertTrue(variant["feature"])
            self.assertEqual(variant["summary"], plan["summary"])

    def test_modes_differ_only_in_which_optional_items_win(self):
        result = self.post("/plans/variants", self.body())
        by_mode = {v["mode"]: self.items_in(v["plan"]) for v in result["variants"]}
        self.assertEqual(by_mode["train_first"], {0, 1, 3})
        self.assertEqual(by_mode["visit_first"], {0, 2, 3})
        single = self.post("/plans", {**self.body(), "mode": "visit_first"})
        self.assertEqual(single, result["variants"][1]["plan"])

    def test_variants_of_an_impossible_must_are_all_infeasible(self):
        result = self.post("/plans/variants", request(
            [observe("102", A, must=True), observe("104", C, must=True)]))
        self.assertEqual([v["plan"]["feasible"] for v in result["variants"]], [False] * 3)


class CompareTests(_WorldCase):
    @staticmethod
    def trains(plan):
        seen = set()
        for step in plan["timeline"]:
            if step["type"] in ("observe", "ride"):
                seen.add(step["train"])
            seen.update(p["train"] for p in step.get("passing", []))
        return seen

    def compare(self, base, changed):
        result = self.post("/plans/compare", {"base": base, "changed": changed})
        self.assertEqual(result["base"], self.post("/plans", base))
        self.assertEqual(result["changed"], self.post("/plans", changed))
        before, after = self.trains(result["base"]), self.trains(result["changed"])
        diff = result["diff"]
        self.assertEqual({g["id"] for g in diff["gained"] if g["kind"] == "train"}, after - before)
        self.assertEqual({g["id"] for g in diff["lost"] if g["kind"] == "train"}, before - after)
        self.assertEqual(diff["end_arrival"], [result["base"]["summary"]["end_arrival"],
                                               result["changed"]["summary"]["end_arrival"]])
        self.assertEqual(diff["gap_change_min"],
                         result["changed"]["summary"]["longest_gap_min"]
                         - result["base"]["summary"]["longest_gap_min"])
        return diff

    def test_riding_away_loses_the_train_that_came_meanwhile(self):
        base = request([observe("102", A, must=True), observe("103", A, must=True)],
                       end=(A, "12:00"))
        changed = request([observe("102", A, must=True), ride("103", A, C, must=True),
                           ride("106", C, A, must=True)], end=(A, "12:00"))
        diff = self.compare(base, changed)
        # 104 reached A at 11:25 while the user was on the way back
        self.assertEqual(diff["lost"], [{"kind": "train", "id": "104", "how": "passing"}])
        self.assertEqual(diff["gained"], [])
        self.assertEqual(diff["end_arrival"], ["11:00", "11:50"])

    def test_going_to_another_station_gains_the_train_that_only_runs_there(self):
        base = request([observe("105", A, must=True)], start=(A, "12:30"))
        changed = request([ride("105", A, B, must=True), ride("108", B, A, must=True)],
                          start=(A, "12:30"))
        diff = self.compare(base, changed)
        self.assertEqual(diff["gained"], [{"kind": "train", "id": "201", "how": "passing"}])
        self.assertEqual(diff["lost"], [])
        self.assertEqual(diff["end_arrival"], ["13:00", "16:20"])

    def test_places_are_compared_too(self):
        base = request([observe("102", A, must=True)], end=(A, "12:30"))
        changed = request([observe("102", A, must=True), visit(NEAR_A, must=True)],
                          end=(A, "12:30"))
        diff = self.post("/plans/compare", {"base": base, "changed": changed})["diff"]
        self.assertIn({"kind": "place", "id": NEAR_A, "how": "visit"}, diff["gained"])
        self.assertEqual([x for x in diff["lost"] if x["kind"] == "place"], [])

    def test_same_request_changes_nothing(self):
        body = request([observe("102", A, must=True)], end=(A, "12:30"))
        diff = self.post("/plans/compare", {"base": body, "changed": body})["diff"]
        self.assertEqual((diff["gained"], diff["lost"], diff["gap_change_min"]), ([], [], 0))

    def test_change_to_an_impossible_plan_loses_everything_and_has_no_arrival(self):
        base = request([observe("102", A, must=True)], end=(A, "12:30"))
        changed = request([observe("102", A, must=True), observe("104", C, must=True)],
                          end=(A, "12:30"))
        result = self.post("/plans/compare", {"base": base, "changed": changed})
        self.assertFalse(result["changed"]["feasible"])
        self.assertEqual(result["diff"]["gained"], [])
        self.assertIn("102", {x["id"] for x in result["diff"]["lost"]})
        self.assertIsNone(result["diff"]["end_arrival"][1])
        self.assertIsNone(result["diff"]["gap_change_min"])


class TimelineTests(_WorldCase):
    def test_waiting_lists_the_other_trains_that_come_by(self):
        body = self.plan([observe("106", A, must=True)], start=(A, "10:50"), end=(A, "12:00"))
        waits = self.steps(body, "wait")
        passing = [(p["train"], p["at"]) for w in waits for p in w["passing"]]
        self.assertEqual(passing, [("103", "11:00"), ("104", "11:25")])
        self.assertEqual(body["summary"]["seen"], 3)
        self.assertEqual(body["summary"]["longest_gap_min"], 0)

    def test_a_stay_at_a_station_lists_the_trains_seen_there(self):
        body = self.plan([visit(B, stay=30, must=True, kind="station")],
                         start=(A, "08:30"), end=(A, "12:00"))
        stay, = self.steps(body, "visit")
        self.assertEqual((stay["place"], stay["from"], stay["to"]), (B, "09:10", "09:40"))
        self.assertEqual(body["summary"]["visits"], 1)
        self.assertEqual(body["summary"]["ridden"], 2)
        self.assertEqual(body["summary"]["end_arrival"], "10:20")

    def test_trains_outside_the_day_are_reported_with_a_hint(self):
        body = self.plan([observe("109", A), observe("101", A), observe("102", A)],
                         start=(A, "09:30"), end=(A, "18:00"))
        self.assertEqual(self.items_in(body), {2})
        outside = {o["train"]: o for o in body["outside"]}
        self.assertEqual(set(outside), {"109", "101"})
        self.assertEqual(outside["109"]["at"], "21:00")
        self.assertIn("묵으면", outside["109"]["hint"])
        self.assertEqual({m["item"] for m in body["missed"]}, {0, 1})

    def test_a_train_that_does_not_come_to_the_place_is_missed_with_that_reason(self):
        body = self.plan([observe("201", A), observe("999", A)])
        reasons = {m["item"]: m["reason"] for m in body["missed"]}
        self.assertIn("지나지 않아요", reasons[0])
        self.assertIn("999", reasons[1])

    def test_fewer_moves_win_a_tie(self):
        # 103 can be seen at A (stay put) or anywhere down the line: the plan stays at A.
        body = self.plan([{"kind": "observe", "train": "103"}], end=(A, "12:00"))
        seen, = self.steps(body, "observe")
        self.assertEqual(seen["place"], A)
        self.assertEqual(self.steps(body, "ride"), [])

    def test_basis_is_stated(self):
        body = self.plan([])
        self.assertIn("가상 시간표 기준", body["basis"])
        other_day = request([])
        other_day["date"] = "2026-11-25"
        self.assertIn("2026-11-21", self.post("/plans", other_day)["basis"])


class ValidationTests(_WorldCase):
    def test_bad_requests_are_422(self):
        good = request([observe("102", A)])
        cases = {
            "too many items": {**good, "items": [observe("102", A)] * 13},
            "unknown field": {**good, "extra": 1},
            "unknown item field": {**good, "items": [{**observe("102", A), "x": 1}]},
            "unknown kind": {**good, "items": [{"kind": "fly", "place": A}]},
            "end before start": {**good, "end": {"place": A, "time": "08:00"}},
            "bad time": {**good, "start": {"place": A, "time": "9시"}},
            "unknown start": {**good, "start": {"place": "station_x", "time": "08:30"}},
            "unknown mode": {**good, "mode": "fast"},
            "zero stay": {**good, "items": [visit(NEAR_A, stay=0)]},
        }
        for name, body in cases.items():
            with self.subTest(name):
                self.post("/plans", body, status=422)
        self.post("/plans", {**good, "items": [observe("102", A)] * 12})

    def test_unknown_item_place_is_reported_not_fatal(self):
        body = self.plan([visit("poi_gone"), observe("102", A)])
        self.assertEqual(self.items_in(body), {1})
        self.assertIn("poi_gone", body["missed"][0]["reason"])

    def test_no_snapshot_is_503(self):
        app = create_app(Settings(), seed=False)  # a database nothing was imported into
        self.addCleanup(app.state.databaseEngine.dispose)
        response = TestClient(app).post(API + "/plans", json=request([]))
        self.assertEqual(response.status_code, 503)


class WalkTests(unittest.TestCase):
    def place(self, lat, lng):
        return places_service.Place("x", "x", "poi", lat=lat, lng=lng)

    def test_walk_is_straight_distance_times_1_3_at_4_5_kmh_rounded_up(self):
        # 0.01 degree of latitude is 1.112 km: 1.112 * 1.3 / 4.5 h = 19.3 min
        self.assertEqual(plans.walk_minutes(self.place(37.50, 129.1), self.place(37.51, 129.1)), 20)
        self.assertEqual(plans.walk_minutes(self.place(37.50, 129.1), self.place(37.50, 129.1)), 0)

    def test_no_coordinates_no_walk(self):
        self.assertIsNone(plans.walk_minutes(self.place(None, None), self.place(37.5, 129.1)))

    def test_very_long_walks_are_not_planned(self):
        self.assertIsNone(plans.walk_minutes(self.place(37.50, 129.1), self.place(37.70, 129.1)))


class RealTimetableScenarios(unittest.TestCase):
    """대회일(2026-11-21) 스냅샷으로 짠 하루. Station ids and times are the real ones."""

    DONGHAE, MUKHO, CHUAM, MANGSANG = (
        "station_donghae", "station_mukho", "station_chuam", "station_mangsang")

    @classmethod
    def setUpClass(cls):
        app = create_app(Settings())  # the seeded reference content
        cls.client = TestClient(app)
        open_content(cls, app)

    def plan(self, items, start="09:00", end="19:00", **extra):
        body = {"date": "2026-11-21", "start": {"place": self.DONGHAE, "time": start},
                "end": {"place": self.DONGHAE, "time": end}, "items": items, **extra}
        response = self.client.post(API + "/plans", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_chuam_round_trip(self):
        body = self.plan([ride("1864", self.DONGHAE, self.CHUAM, must=True),
                          ride("1861", self.CHUAM, self.DONGHAE, must=True)])
        self.assertTrue(body["feasible"])
        rides = [(s["train"], s["depart"], s["arrive"]) for s in body["timeline"]
                 if s["type"] == "ride"]
        self.assertEqual(rides, [("1864", "09:39", "09:48"), ("1861", "10:15", "10:23")])
        self.assertEqual(body["summary"]["ridden"], 2)
        self.assertEqual(body["summary"]["end_arrival"], "10:23")
        self.assertIn("최근 토요일 3주", body["basis"])
        # 1861 ran up to 11 minutes late at Donghae: the rest of the day starts from there
        self.assertEqual(body["conflicts"], [])

    def test_mangsang_cannot_be_reached_so_the_server_offers_mukho(self):
        items = [observe("1861", self.MANGSANG, must=True)]
        body = self.plan(items)
        if places_service.get(".", self.MANGSANG).lat is not None:
            self.skipTest("망상역 좌표가 채워지면 걸어갈 수 있어 이 충돌은 사라진다")
        self.assertFalse(body["feasible"])
        conflict, = body["conflicts"]
        self.assertIn("망상역", conflict["reason"])
        self.assertIn("10:42", conflict["reason"])
        labels = [o["label"] for o in conflict["options"]]
        self.assertEqual(len(labels), 2)
        self.assertIn("묵호역에서 보기", labels)
        option = conflict["options"][labels.index("묵호역에서 보기")]
        after = self.plan([option["change"]["replace"]])
        self.assertTrue(after["feasible"])
        seen, = [s for s in after["timeline"] if s["type"] == "observe"]
        self.assertEqual((seen["train"], seen["place"], seen["at"]), ("1861", self.MUKHO, "10:34"))

    def test_morning_rush_at_donghae(self):
        body = self.plan([observe("752", self.DONGHAE), observe("751", self.DONGHAE)],
                         end="12:00")
        self.assertTrue(body["feasible"])
        seen = [(s["train"], s["at"]) for s in body["timeline"] if s["type"] == "observe"]
        self.assertEqual(seen, [("752", "10:20"), ("751", "10:59")])
        passing = [p["train"] for s in body["timeline"] for p in s.get("passing", [])]
        self.assertIn("841", passing)  # 09:32
        self.assertIn("1861", passing)  # between the two
        self.assertGreaterEqual(body["summary"]["seen"], 8)
        self.assertEqual(body["missed"], [])
        self.assertFalse([s for s in body["timeline"] if s["type"] in ("ride", "walk")])

    def test_mukho_stay_and_back(self):
        body = self.plan([visit(self.MUKHO, stay=30, must=True, kind="station")], end="13:00")
        self.assertTrue(body["feasible"])
        rides = [(s["train"], s["from"], s["depart"]) for s in body["timeline"]
                 if s["type"] == "ride"]
        self.assertEqual(rides, [("842", self.DONGHAE, "10:04"), ("2502", self.MUKHO, "11:06")])
        stay, = [s for s in body["timeline"] if s["type"] == "visit"]
        self.assertEqual((stay["from"], stay["to"]), ("10:10", "10:40"))
        self.assertIn("752", [p["train"] for p in stay["passing"]])

    def test_late_train_is_outside_the_day_and_hints_at_staying_over(self):
        body = self.plan([observe("755", self.DONGHAE)])
        self.assertTrue(body["feasible"])
        outside, = body["outside"]
        self.assertEqual((outside["train"], outside["at"]), ("755", "22:50"))
        self.assertIn("묵으면", outside["hint"])

    def test_chuam_has_few_trains_so_a_missed_one_cannot_be_fixed(self):
        # Leaving Chuam on 1861 needs 1864 out (09:39); starting at 09:40 it is gone.
        body = self.plan([ride("1861", self.CHUAM, self.DONGHAE, must=True)], start="09:40")
        self.assertFalse(body["feasible"])
        self.assertIn("추암역", body["conflicts"][0]["reason"])

    def test_three_variants_on_the_real_day(self):
        body = {"date": "2026-11-21", "start": {"place": self.DONGHAE, "time": "09:00"},
                "end": {"place": self.DONGHAE, "time": "19:00"},
                "items": [ride("1864", self.DONGHAE, self.CHUAM, must=True),
                          observe("1861", self.CHUAM),
                          visit(self.MUKHO, stay=30, kind="station")]}
        response = self.client.post(API + "/plans/variants", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        for variant in response.json()["variants"]:
            items = {s.get("item") for s in variant["plan"]["timeline"]}
            self.assertIn(0, items)

    def test_walking_needs_coordinates_that_the_catalog_does_not_have_yet(self):
        place = places_service.get(".", "poi_chotdaebawi")
        if place.lat is not None:
            self.skipTest("좌표가 채워졌다")
        body = self.plan([visit("poi_chotdaebawi", stay=40)])
        self.assertTrue(body["feasible"])
        self.assertIn("좌표", body["missed"][0]["reason"])
        self.assertEqual(body["suggestions"], [])


class ChatUsesTheSameSuggestionRule(unittest.TestCase):
    """7장: 니어리의 공백 제안은 계획 모듈의 제안 함수에서 나온다."""

    COORDINATES = {"station_donghae": (37.50, 129.10), "poi_bukpyeong_market": (37.503, 129.10),
                   "poi_cheongok_cave": (37.60, 129.10)}  # made up: only distances matter

    def setUp(self):
        app = create_app(Settings())  # the seeded reference content
        session = open_content(self, app)
        for place_id, (lat, lng) in self.COORDINATES.items():
            place = session.get(PlaceEntity, place_id)
            place.lat, place.lng = lat, lng
        session.commit()
        self.client = TestClient(app)

    def ask(self, **context):
        response = self.client.post(API + "/chat/messages", json={
            "message": "여기 열차 언제 몰려?",
            "context": {"date": "2026-11-21", "focused": "station_donghae", **context}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_chat_proposal_is_the_planners_suggestion_for_the_same_gap(self):
        timetable = trains_service.load(".")
        rhythm = trains_service.station_trains(timetable, "station_donghae").rhythm
        gap = max(rhythm.gaps, key=lambda g: g.minutes)
        expected = plans.suggest(places_service.all_places("."), "station_donghae",
                                 gap.start, gap.end, date=dt.date(2026, 11, 21))
        self.assertEqual([s.target for s in expected], ["poi_bukpyeong_market"])  # the cave is too far
        proposal, = self.ask()["proposals"]
        self.assertEqual((proposal["target"], proposal["reason"]),
                         (expected[0].target, expected[0].reason))
        self.assertIs(proposal["needs_confirm"], True)

    def test_dismissed_place_is_not_proposed_by_either(self):
        self.assertEqual(self.ask(dismissed=["poi_bukpyeong_market"])["proposals"], [])


class MarketDayTests(unittest.TestCase):
    def test_market_day_lives_with_places(self):
        self.assertEqual(places_service.market_day(dt.date(2026, 11, 21)),
                         (False, dt.date(2026, 11, 23)))
        self.assertEqual(places_service.market_day(dt.date(2026, 11, 23)),
                         (True, dt.date(2026, 11, 23)))


if __name__ == "__main__":
    unittest.main()
