"""Planner onboarding: the choices of the first screen come from the timetable and from
written rules, never from a model."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app
from legacy_support import setUpModule as setUpModule
from legacy_support import tearDownModule as tearDownModule

from neareast_api.core.config import Settings
from neareast_api.modules.chat import llm
from neareast_api.modules.itineraries.dto import PlanningConditionsDto
from neareast_api.modules.onboarding import service
from neareast_api.modules.places.service import Place
from neareast_api.modules.recommend import companions
from neareast_api.modules.recommend import service as recommend
from neareast_api.modules.trains import service as trains

API = "/api/v1"
MUKHO, DONGHAE, CHUAM, MANGSANG = (
    "station_mukho", "station_donghae", "station_chuam", "station_mangsang")


def stop(name, arr, dep, kind="여객승하차", place_id=None, **extra):
    return {"name": name, "code": f"c-{name}", "arr": arr, "dep": dep, "stop_type": kind,
            "place_id": place_id, **extra}


def timetable(*trains_, types=None):
    meta = {"basis": {"summary": "기준", "record_dates": [], "plan_date": None},
            "target_date": "2026-11-21", "limits": []}
    return trains.Timetable(meta, {}, tuple(trains_), types or {})


def train(no, *stops):
    return {"no": no, "stops": list(stops)}


class OriginRuleTests(unittest.TestCase):
    """Small hand-made timetables: what counts as "a train from there to here"."""

    def origins(self, *trains_, types=None, destination=MUKHO):
        return service.origins(timetable(*trains_, types=types), destination, "묵호역").origins

    def test_stations_before_the_destination_on_a_train_that_stops_there(self):
        rows = self.origins(train(
            "1", stop("서울", None, "08:00", "시발"), stop("원주", "09:00", "09:01"),
            stop("묵호", "10:30", "10:31", place_id=MUKHO), stop("강릉", "11:00", None, "종착")))
        self.assertEqual([r.stationName for r in rows], ["서울", "원주"])  # 강릉 comes after
        self.assertEqual((rows[0].fastestMinutes, rows[1].fastestMinutes), (150, 89))

    def test_a_train_that_only_passes_the_destination_does_not_count(self):
        rows = self.origins(train(
            "1", stop("서울", None, "08:00", "시발"),
            stop("묵호", None, None, "통과(어림)", place_id=MUKHO, **{"pass": "10:30"})))
        self.assertEqual(rows, [])

    def test_a_station_the_train_only_passes_is_not_an_origin(self):
        rows = self.origins(train(
            "1", stop("서울", None, "08:00", "시발"),
            stop("망상", None, None, "통과(어림)", place_id=MANGSANG, **{"pass": "10:20"}),
            stop("묵호", "10:30", "10:31", place_id=MUKHO)))
        self.assertEqual([r.stationName for r in rows], ["서울"])

    def test_counts_types_and_first_and_last_departure(self):
        rows = self.origins(
            train("1", stop("서울", None, "08:00", "시발"), stop("묵호", "10:30", None, place_id=MUKHO)),
            train("2", stop("서울", None, "18:00", "시발"), stop("묵호", "20:10", None, place_id=MUKHO)),
            train("3", stop("서울", None, "12:00", "시발"), stop("묵호", "14:40", None, place_id=MUKHO)),
            types={"1": "KTX-이음", "2": "KTX-이음", "3": "ITX-마음"})
        (row,) = rows
        self.assertEqual((row.trainsPerDay, row.firstDeparture, row.lastDeparture), (3, "08:00", "18:00"))
        self.assertEqual(row.trainTypes, ["KTX-이음", "ITX-마음"])  # most frequent first
        self.assertEqual(row.fastestMinutes, 130)
        self.assertEqual(row.label, "하루 3편 · KTX-이음, ITX-마음")

    def test_unknown_train_type_is_left_out_not_guessed(self):
        (row,) = self.origins(train(
            "9", stop("서울", None, "08:00", "시발"), stop("묵호", "10:30", None, place_id=MUKHO)))
        self.assertEqual(row.trainTypes, [])
        self.assertEqual(row.label, "하루 1편")

    def test_stations_where_a_train_starts_are_featured_first(self):
        rows = self.origins(
            train("1", stop("서울", None, "08:00", "시발"), stop("원주", "09:00", "09:01"),
                  stop("묵호", "10:30", None, place_id=MUKHO)),
            train("2", stop("원주", None, "12:00"), stop("묵호", "13:30", None, place_id=MUKHO)),
            train("3", stop("동해", None, "07:00", "시발", place_id=DONGHAE),
                  stop("묵호", "07:06", None, place_id=MUKHO)))
        self.assertEqual([(r.stationName, r.featured) for r in rows],
                         [("서울", True), ("동해", False), ("원주", False)])
        # 동해 starts a train too but is on the service map: it is not where a trip begins.

    def test_a_trip_past_midnight_has_a_positive_duration(self):
        (row,) = self.origins(train(
            "1", stop("부전", None, "23:30", "시발"), stop("묵호", "00:40", None, place_id=MUKHO)))
        self.assertEqual(row.fastestMinutes, 70)


class OnboardingApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(Settings()))

    def test_options_list_the_three_stations_first_with_trains_a_day(self):
        with patch.object(llm, "chat_json") as chat:
            body = self.client.get(f"{API}/onboarding/options").json()
        chat.assert_not_called()
        featured = [d for d in body["destinations"] if d["featured"]]
        self.assertEqual([d["placeId"] for d in featured], [MUKHO, DONGHAE, CHUAM])
        self.assertEqual(body["destinations"][:3], featured)
        for d in body["destinations"]:
            self.assertGreater(d["trainsPerDay"], 0)
            self.assertTrue(d["label"].startswith(f"하루 {d['trainsPerDay']}편"))
        # 망상역: trains pass without stopping, so it is not somewhere to go by train.
        self.assertNotIn(MANGSANG, [d["placeId"] for d in body["destinations"]])
        self.assertIn("토요일", body["basis"])

    def test_options_carry_the_companion_rules_with_their_reasons(self):
        body = self.client.get(f"{API}/onboarding/options").json()
        rules = {c["id"]: c for c in body["companions"]}
        self.assertEqual(list(rules), ["alone", "couple", "child", "parents"])
        self.assertEqual(rules["parents"]["maxWalkingMinutes"], 20)
        self.assertIn("부모님과 함께라", rules["parents"]["reason"])
        self.assertIsNone(rules["alone"]["reason"])  # nothing changes: nothing to explain

    def test_origins_agree_with_the_train_search(self):
        body = self.client.get(f"{API}/onboarding/origins", params={"destination": MUKHO}).json()
        self.assertEqual(body["destination"], {"placeId": MUKHO, "name": "묵호역"})
        self.assertEqual(body["certainty"], "estimated")
        names = [o["stationName"] for o in body["origins"]]
        self.assertIn("청량리", names)
        self.assertTrue(all(o["featured"] for o in body["origins"][:len(
            [o for o in body["origins"] if o["featured"]])]))  # featured ones lead the list
        donghae = next(o for o in body["origins"] if o["placeId"] == DONGHAE)
        searched = self.client.get(f"{API}/trains", params={"from": DONGHAE, "to": MUKHO}).json()
        self.assertEqual(donghae["trainsPerDay"], len(searched["trains"]))
        self.assertEqual(donghae["firstDeparture"], searched["trains"][0]["dep"])

    def test_unknown_or_unreachable_station(self):
        get = lambda d: self.client.get(f"{API}/onboarding/origins", params={"destination": d})  # noqa: E731
        self.assertEqual(get("station_nowhere").status_code, 404)
        self.assertEqual(get("poi_nongoldam").status_code, 404)  # a place, not a station
        self.assertEqual(get(MANGSANG).status_code, 422)
        self.assertEqual(self.client.get(f"{API}/onboarding/origins").status_code, 422)


def place(id_, lat, lng, category="attraction", name=None):
    return Place(id=id_, name=name or id_, kind="poi", lat=lat, lng=lng, category=category)


class CompanionRuleTests(unittest.TestCase):
    """About 75 m per walking minute: 0.01° of latitude is roughly a 19-minute walk."""

    places = {p.id: p for p in (
        place("anchor", 37.5000, 129.1000, None),
        place("near_sight", 37.5040, 129.1000),      # ~8 min
        place("hall", 37.5060, 129.1000, "culture"),  # ~12 min
        place("far_sight", 37.5095, 129.1000),        # ~18 min
    )}

    def nearby(self, who):
        rule = companions.rule(who)
        within = min(rule.maxWalkingMinutes or recommend.NEARBY_WALK_MIN, recommend.NEARBY_WALK_MIN)
        return [p.id for _, p in recommend.nearby(self.places, "anchor", within=within,
                                                  indoor_first=rule.indoorFirst)]

    def test_no_companion_or_one_without_a_rule_changes_nothing(self):
        expected = ["near_sight", "hall", "far_sight"]
        for who in (None, "alone", "couple"):
            self.assertEqual(self.nearby(who), expected)
            self.assertIsNone(companions.rule(who).reason)

    def test_child_shorter_walk_and_indoor_first(self):
        self.assertEqual(self.nearby("child"), ["hall", "near_sight"])  # 18 min is too far

    def test_parents_gap_offers_stay_within_twenty_minutes(self):
        spread = dict(self.places, distant=place("distant", 37.5130, 129.1000))  # ~25 min
        offer = lambda who: [s.target for s in recommend.suggest(  # noqa: E731
            spread, "anchor", "10:00", "14:00", limit=10,
            max_walk=companions.rule(who).maxWalkingMinutes) if s.purpose == "visit"]
        self.assertIn("distant", offer(None))
        self.assertNotIn("distant", offer("parents"))
        self.assertIn("far_sight", offer("parents"))

    def test_conditions_accept_companions_and_stay_optional(self):
        self.assertIsNone(PlanningConditionsDto().companions)
        self.assertEqual(PlanningConditionsDto(companions="parents").companions, "parents")
        with self.assertRaises(ValueError):
            PlanningConditionsDto(companions="friends")


class CompanionsInChatTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(Settings()))

    def ask(self, message, **context):
        response = self.client.post(f"{API}/chat/messages", json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_plan_conditions_carry_the_rule_and_the_reply_says_why(self):
        body = self.ask("일정 짜줘", date="2026-11-21", pins=[DONGHAE], companions="parents")
        payload = body["action"]["payload"]
        self.assertEqual((payload["companions"], payload["maxWalkingMinutes"]), ("parents", 20))
        self.assertIn("부모님과 함께라 걷는 시간을 편도 20분 이내로 잡았어요.", body["reply"])

    def test_without_a_rule_nothing_is_set_and_nothing_is_said(self):
        for who in (None, "alone", "couple"):
            context = {"date": "2026-11-21", "pins": [DONGHAE]}
            if who:
                context["companions"] = who
            body = self.ask("일정 짜줘", **context)
            self.assertIsNone(body["action"]["payload"]["maxWalkingMinutes"])
            self.assertNotIn("함께라", body["reply"])

    def test_unknown_companion_is_a_validation_error(self):
        response = self.client.post(f"{API}/chat/messages", json={
            "message": "일정 짜줘", "context": {"companions": "friends"}})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
