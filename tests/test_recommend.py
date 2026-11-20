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
EATERY, HOTEL, SHOP, CAMP, UNCLASSIFIED = (
    "poi_eatery", "poi_hotel", "poi_shop", "poi_camp", "poi_unclassified")
# 0.001 degree of latitude is about 111 m: 2 minutes on foot by the rule below.
PLACES = {p.id: p for p in (
    Place(STATION, "가", "station", lat=37.500, lng=129.10),
    Place(NEAR, "가까운 곳", "poi", lat=37.503, lng=129.10, category="attraction"),  # 6 min
    Place(MARKET, "북평민속시장", "poi", lat=37.497, lng=129.10, category="market"),  # 6 min
    Place(FAR, "먼 곳", "poi", lat=37.530, lng=129.10, category="culture"),        # 58 min
    Place(NO_COORD, "좌표 없는 곳", "poi", category="attraction"),
    Place("station_b", "나", "station", lat=37.502, lng=129.10),
    # Close by, but not somewhere to go and look.
    Place(EATERY, "식당", "poi", lat=37.501, lng=129.10, category="restaurant"),     # 2 min
    Place(HOTEL, "호텔", "poi", lat=37.501, lng=129.10, category="lodging"),
    Place(SHOP, "편의점", "poi", lat=37.501, lng=129.10, category="shopping"),
    Place(CAMP, "캠핑장", "poi", lat=37.501, lng=129.10, category="leisure"),
    Place(UNCLASSIFIED, "분류 없는 곳", "poi", lat=37.501, lng=129.10),
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
    def targets(self, start="14:30", end="19:30", **kwargs):
        """Places to visit offered for a gap (the meal offer is tested separately)."""
        return [s.target for s in recommend.suggest(PLACES, STATION, start, end, **kwargs)
                if s.purpose == "visit"]

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

    def test_only_attractions_cultural_places_and_markets_are_places_to_visit(self):
        offered = set(self.targets("08:00", "22:00", limit=50))
        self.assertEqual(offered, {MARKET, NEAR, FAR})
        for excluded in (HOTEL, SHOP, CAMP, EATERY):
            self.assertNotIn(excluded, offered)

    def test_a_place_the_data_does_not_classify_is_never_offered(self):
        everything = [s.target for s in recommend.suggest(PLACES, STATION, "08:00", "22:00", limit=50)]
        self.assertNotIn(UNCLASSIFIED, everything)
        self.assertNotIn(UNCLASSIFIED, [p.id for _, p in recommend.nearby(PLACES, STATION)])

    def test_a_restaurant_is_offered_only_for_a_gap_over_a_meal_time_and_as_a_meal(self):
        def meals(start, end):
            return [(s.target, s.reason) for s in recommend.suggest(PLACES, STATION, start, end)
                    if s.purpose == "meal"]
        (target, reason), = meals("11:15", "16:00")
        self.assertEqual(target, EATERY)
        for text in ("점심 시간과 겹쳐요", "식사하러", "편도 2분"):
            self.assertIn(text, reason)
        self.assertIn("저녁", meals("16:30", "19:00")[0][1])
        self.assertEqual(meals("14:30", "16:30"), [])   # between the two meal times
        self.assertEqual(meals("08:00", "11:20"), [])   # only 20 minutes of lunch time
        self.assertEqual(meals("13:20", "16:00"), [(EATERY, meals("13:20", "16:00")[0][1])])
        self.assertEqual(meals("20:10", "22:30"), [])   # after dinner time
        # A meal is one extra offer; the places to visit are still there.
        lunch = recommend.suggest(PLACES, STATION, "11:15", "16:00")
        self.assertEqual([s.purpose for s in lunch], ["visit", "visit", "meal"])

    def test_meal_times(self):
        self.assertEqual(recommend.meal_time("11:15", "16:00"), "점심")
        self.assertEqual(recommend.meal_time("17:00", "17:30"), "저녁")
        self.assertIsNone(recommend.meal_time("14:00", "17:00"))
        self.assertIsNone(recommend.meal_time("19:40", "23:00"))

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


class NearbyTests(unittest.TestCase):
    def test_places_to_visit_within_a_short_walk_nearest_first(self):
        self.assertEqual([(m, p.id) for m, p in recommend.nearby(PLACES, STATION)],
                         [(6, MARKET), (6, NEAR)])  # the far one is 58 minutes away
        self.assertEqual([p.id for _, p in recommend.nearby(PLACES, STATION, within=60)],
                         [MARKET, NEAR, FAR])
        self.assertEqual([p.id for _, p in recommend.nearby(PLACES, STATION, skip={MARKET})], [NEAR])
        self.assertEqual(recommend.nearby(PLACES, "station_gone"), [])
        self.assertEqual(recommend.nearby(PLACES, NO_COORD), [])  # nothing can be measured

    def test_one_pair_of_places_has_one_walking_time_everywhere(self):
        walk = recommend.walk_between(PLACES[STATION], PLACES[FAR]).minutes
        near = dict((p.id, m) for m, p in recommend.nearby(PLACES, STATION, within=90))
        gap = {s.target: s.walk_min for s in recommend.suggest(PLACES, STATION, "08:00", "22:00", limit=50)}
        self.assertEqual((near[FAR], gap[FAR]), (walk, walk))
        for place_id in (MARKET, NEAR):
            expected = recommend.walk_between(PLACES[STATION], PLACES[place_id]).minutes
            self.assertEqual((near[place_id], gap[place_id]), (expected, expected))
        # Both directions agree too.
        self.assertEqual(recommend.walk_between(PLACES[FAR], PLACES[STATION]).minutes, walk)


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
