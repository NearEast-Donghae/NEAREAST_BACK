"""Recommendations (modules/recommend): places for a train-less stretch, walking time and
market days. The chat must offer exactly what this module offers. No LLM anywhere."""

import datetime as dt
import unittest

from fastapi.testclient import TestClient
from legacy_support import create_app

from neareast_api.core.config import Settings
from neareast_api.db.entities import PlaceEntity
from neareast_api.db.session import CONTENT_SESSION
from neareast_api.modules.places import service as places_service
from neareast_api.modules.places.service import Place
from neareast_api.modules.recommend import service as recommend
from neareast_api.modules.trains import service as trains_service

API = "/api/v1"
STATION, NEAR, FAR, NO_COORD, MARKET = (
    "station_a", "poi_near", "poi_far", "poi_nocoord", "poi_bukpyeong_market")
# 0.001 degree of latitude is about 111 m: 2 minutes on foot by the rule below.
PLACES = {p.id: p for p in (
    Place(STATION, "가", "station", lat=37.500, lng=129.10),
    Place(NEAR, "가까운 곳", "poi", lat=37.503, lng=129.10),     # 6 min
    Place(MARKET, "북평민속시장", "poi", lat=37.497, lng=129.10),  # 6 min
    Place(FAR, "먼 곳", "heritage", lat=37.530, lng=129.10),     # 58 min
    Place(NO_COORD, "좌표 없는 곳", "poi"),
    Place("station_b", "나", "station", lat=37.502, lng=129.10),
)}


def minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


class WalkTests(unittest.TestCase):
    def place(self, lat, lng):
        return Place("x", "x", "poi", lat=lat, lng=lng)

    def test_walk_is_straight_distance_times_1_3_at_4_5_kmh_rounded_up(self):
        # 0.01 degree of latitude is 1.112 km: 1.112 * 1.3 / 4.5 h = 19.3 min
        self.assertEqual(
            recommend.walk_minutes(self.place(37.50, 129.1), self.place(37.51, 129.1)), 20)
        self.assertEqual(
            recommend.walk_minutes(self.place(37.50, 129.1), self.place(37.50, 129.1)), 0)

    def test_no_coordinates_no_walk(self):
        self.assertIsNone(
            recommend.walk_minutes(self.place(None, None), self.place(37.5, 129.1)))

    def test_very_long_walks_are_not_offered(self):
        self.assertIsNone(
            recommend.walk_minutes(self.place(37.50, 129.1), self.place(37.70, 129.1)))


class MarketDayTests(unittest.TestCase):
    def test_market_days_end_in_3_or_8(self):
        self.assertEqual(recommend.market_day(dt.date(2026, 11, 21)),
                         (False, dt.date(2026, 11, 23)))
        self.assertEqual(recommend.market_day(dt.date(2026, 11, 23)),
                         (True, dt.date(2026, 11, 23)))
        self.assertEqual(recommend.market_day(dt.date(2026, 11, 29)),
                         (False, dt.date(2026, 12, 3)))


class SuggestTests(unittest.TestCase):
    def targets(self, start="11:00", end="16:00", **kwargs):
        return [s.target for s in recommend.suggest(PLACES, STATION, start, end, **kwargs)]

    def test_only_places_to_visit_that_fit_in_the_gap(self):
        # Stations and places without coordinates are never offered.
        self.assertEqual(self.targets(limit=10), [MARKET, NEAR, FAR])
        # 10:00-11:00 leaves 60 minutes: the far place needs 2 x 58 + 30 + 10.
        self.assertEqual(self.targets("10:00", "11:00", limit=10), [MARKET, NEAR])
        for s in recommend.suggest(PLACES, STATION, "10:00", "11:00", limit=10):
            need = 2 * s.walk_min + s.stay_min + recommend.RETURN_SLACK
            self.assertLessEqual(need, minutes(s.gap[1]) - minutes(s.gap[0]))
        # 30 + 10 + two walks do not fit in 45 minutes
        self.assertEqual(self.targets("10:00", "10:45"), [])

    def test_fit_from_is_when_the_user_is_surely_there(self):
        self.assertEqual(self.targets("10:00", "11:00", fit_from="10:20"), [])

    def test_at_most_two_by_default_nearest_first(self):
        self.assertEqual(self.targets(), [MARKET, NEAR])
        self.assertEqual(self.targets(far_first=True), [FAR, MARKET])

    def test_pinned_and_dismissed_places_are_skipped(self):
        self.assertEqual(self.targets(skip={MARKET, NEAR}), [FAR])

    def test_unknown_anchor_gets_nothing(self):
        self.assertEqual(recommend.suggest(PLACES, "station_gone", "11:00", "16:00"), [])

    def test_a_suggestion_is_an_offer_with_its_reason(self):
        s = recommend.suggest(PLACES, STATION, "11:00", "16:00", skip={MARKET})[0]
        self.assertEqual((s.type, s.target, s.gap), ("add", NEAR, ["11:00", "16:00"]))
        self.assertIs(s.needs_confirm, True)
        self.assertEqual(s.certainty, "estimated")
        for text in ("가 공백(11:00–16:00)", "편도 6분", "머무는 시간 30분"):
            self.assertIn(text, s.reason)

    def test_market_suggestion_says_whether_it_is_a_market_day(self):
        saturday = recommend.suggest(PLACES, STATION, "11:00", "16:00", date=dt.date(2026, 11, 21))
        self.assertTrue(any("장날이 아니에요" in n and "11/23" in n for n in saturday[0].notes))
        monday = recommend.suggest(PLACES, STATION, "11:00", "16:00", date=dt.date(2026, 11, 23))
        self.assertTrue(any("장날이에요" in n for n in monday[0].notes))
        self.assertEqual(recommend.suggest(PLACES, STATION, "11:00", "16:00")[0].notes, [])
        self.assertEqual(saturday[1].notes, [])  # only the market has market days


class ChatUsesTheSameSuggestionRule(unittest.TestCase):
    """니어리의 공백 제안은 추천 모듈의 제안 함수에서 나온다."""

    COORDINATES = {"station_donghae": (37.50, 129.10), "poi_bukpyeong_market": (37.503, 129.10),
                   "poi_cheongok_cave": (37.60, 129.10)}  # made up: only distances matter

    def setUp(self):
        app = create_app(Settings())  # the seeded reference content
        session = app.state.sessionFactory()
        token = CONTENT_SESSION.set(session)  # so the test can call services as a request does

        def close():
            CONTENT_SESSION.reset(token)
            session.close()
            app.state.databaseEngine.dispose()
        self.addCleanup(close)
        self.session = session
        self.client = TestClient(app)

    def locate(self):
        for place_id, (lat, lng) in self.COORDINATES.items():
            place = self.session.get(PlaceEntity, place_id)
            place.lat, place.lng = lat, lng
        self.session.commit()

    def ask(self, **context):
        response = self.client.post(API + "/chat/messages", json={
            "message": "여기 열차 언제 몰려?",
            "context": {"date": "2026-11-21", "focused": "station_donghae", **context}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def longest_gap(self):
        rhythm = trains_service.station_trains(trains_service.load("."), "station_donghae").rhythm
        return max(rhythm.gaps, key=lambda g: g.minutes)

    def test_chat_proposal_is_the_recommended_place_for_the_same_gap(self):
        self.locate()
        gap = self.longest_gap()
        expected = recommend.suggest(places_service.all_places("."), "station_donghae",
                                     gap.start, gap.end, date=dt.date(2026, 11, 21))
        self.assertEqual([s.target for s in expected], ["poi_bukpyeong_market"])  # the cave is too far
        proposal, = self.ask()["proposals"]
        self.assertEqual((proposal["target"], proposal["reason"]),
                         (expected[0].target, expected[0].reason))
        self.assertIs(proposal["needs_confirm"], True)

    def test_dismissed_or_pinned_place_is_not_proposed(self):
        self.locate()
        self.assertEqual(self.ask(dismissed=["poi_bukpyeong_market"])["proposals"], [])
        self.assertEqual(self.ask(pins=["poi_bukpyeong_market"])["proposals"], [])

    def test_without_coordinates_the_chat_keeps_its_example_walking_minutes(self):
        proposals = self.ask()["proposals"]
        self.assertTrue(proposals)
        for proposal in proposals:
            self.assertIn("도보", proposal["reason"])


if __name__ == "__main__":
    unittest.main()
