"""Nearie's "nearby" answers come from the places' coordinates and tourism categories.

The same pair of places must get the same walking time from the nearby tool, the
walk-time tool and a gap proposal. Example data is used only for a place that has no
coordinates, and says so.
"""

import os
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app

from neareast_api.core.config import Settings
from neareast_api.db.entities import PlaceEntity
from neareast_api.modules.chat import llm

URL = "/api/v1/chat/messages"
PLACES_URL = "/api/v1/places"
DONGHAE, MUKHO, CHUAM = "station_donghae", "station_mukho", "station_chuam"
MARKET, CAVE, NONGOLDAM, DOJJEBIGOL = (
    "poi_bukpyeong_market", "poi_cheongok_cave", "poi_nongoldam", "poi_dojjebigol")
ATTRACTION, LEISURE, RESTAURANT, LODGING = "12", "28", "39", "32"  # TourAPI content types
# made up: only the distances matter (0.003 degrees of latitude is 6 minutes on foot)
SPOTS = {
    DONGHAE: (37.500, 129.10, None),
    MARKET: (37.503, 129.10, None),         # 6 min; the market is listed by hand
    CAVE: (37.506, 129.10, ATTRACTION),     # 12 min
    DOJJEBIGOL: (37.504, 129.10, LODGING),  # 8 min, but not somewhere to visit
    NONGOLDAM: (37.502, 129.10, None),      # 4 min, but the data does not classify it
}


def minutes_in(text, name):
    found = re.search(re.escape(name) + r"\(걸어서 약 (\d+)분\)", text)
    return int(found.group(1)) if found else None


class _Case(unittest.TestCase):
    settings = Settings()  # mock mode
    spots: dict = SPOTS

    def setUp(self):
        self.app = create_app(self.settings)
        self.addCleanup(self.app.state.databaseEngine.dispose)
        with self.app.state.sessionFactory() as session:
            for place_id, (lat, lng, content_type) in self.spots.items():
                place = session.get(PlaceEntity, place_id)
                place.lat, place.lng = lat, lng
                if content_type:
                    place.operatingInfo = {"tourApi": {"contentTypeId": content_type}}
            session.commit()
        self.client = TestClient(self.app)

    def ask(self, message, replies=None, **context):
        if replies is None:
            response = self.client.post(URL, json={"message": message, "context": context})
        else:
            with patch.object(llm, "chat_json", side_effect=list(replies)):
                response = self.client.post(URL, json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class CategoryTests(_Case):
    def test_places_api_shows_the_category_read_from_the_tourism_data(self):
        categories = {p["id"]: p["category"] for p in self.client.get(PLACES_URL).json()["places"]}
        self.assertEqual(categories[CAVE], "attraction")
        self.assertEqual(categories[DOJJEBIGOL], "lodging")
        self.assertEqual(categories[MARKET], "market")  # listed by hand: TourAPI says shopping
        self.assertIsNone(categories[NONGOLDAM])        # the data does not say
        self.assertIsNone(categories[DONGHAE])


class NearbyWithCoordinatesTests(_Case):
    def test_nearby_lists_places_to_visit_with_computed_minutes(self):
        body = self.ask("근처 추천해줘", focused=DONGHAE)
        self.assertEqual(body["tool_used"], "nearby_spots")
        self.assertEqual([p["target"] for p in body["proposals"]], [MARKET, CAVE])  # nearest first
        self.assertEqual(minutes_in(body["reply"], "북평민속시장"), 6)
        self.assertEqual(minutes_in(body["reply"], "천곡황금박쥐동굴"), 12)
        self.assertIn("어림", body["reply"])
        self.assertNotIn("예시 데이터", body["reply"])
        self.assertNotIn("도보 10분", body["reply"])  # the example table said 10 minutes

    def test_lodging_and_unclassified_places_are_not_offered_however_close(self):
        body = self.ask("근처 추천해줘", focused=DONGHAE)
        targets = [p["target"] for p in body["proposals"]]
        self.assertNotIn(DOJJEBIGOL, targets)
        self.assertNotIn(NONGOLDAM, targets)
        self.assertNotIn("도째비골", body["reply"])
        self.assertNotIn("논골담길", body["reply"])

    def test_nearby_and_walk_time_give_the_same_minutes_for_the_same_pair(self):
        near = self.ask("근처 추천해줘", focused=DONGHAE)
        for name, place_name in ((MARKET, "북평민속시장"), (CAVE, "천곡황금박쥐동굴")):
            with self.subTest(name):
                walk = self.ask(f"동해역에서 {place_name}까지 걸어가면 얼마나 걸려?")
                asked = int(re.search(r"걸어서 약 (\d+)분", walk["reply"]).group(1))
                self.assertEqual(minutes_in(near["reply"], place_name), asked)
                reason = next(p["reason"] for p in near["proposals"] if p["target"] == name)
                self.assertIn(f"약 {asked}분", reason)

    def test_gap_proposal_uses_the_same_minutes_too(self):
        body = self.ask("여기 열차 언제 몰려?", date="2026-11-21", focused=DONGHAE)
        proposal = body["proposals"][0]
        self.assertEqual(proposal["target"], MARKET)
        self.assertIn("편도 6분", proposal["reason"])
        walk = self.ask("동해역에서 북평민속시장까지 걸어가면 얼마나 걸려?")
        self.assertIn("약 6분", walk["reply"])

    def test_pinned_and_dismissed_places_are_left_out(self):
        self.assertEqual([p["target"] for p in self.ask(
            "근처 추천해줘", focused=DONGHAE, pins=[MARKET])["proposals"]], [CAVE])
        self.assertEqual([p["target"] for p in self.ask(
            "근처 추천해줘", focused=DONGHAE, dismissed=[CAVE])["proposals"]], [MARKET])

    def test_nothing_within_a_short_walk_is_said_plainly(self):
        with self.app.state.sessionFactory() as session:
            for place_id in (MARKET, CAVE):
                session.get(PlaceEntity, place_id).lat = 37.9  # far away now
            session.commit()
        body = self.ask("근처 추천해줘", focused=DONGHAE)
        self.assertEqual(body["proposals"], [])
        self.assertIn("확인된 정보가 없어요", body["reply"])
        self.assertNotIn("예시 데이터", body["reply"])
        self.assertNotIn("동해역 근처", body["chips"])  # that chip would lead nowhere


class NearbyWithoutCoordinatesTests(_Case):
    spots: dict = {}

    def test_a_place_without_coordinates_falls_back_to_labelled_example_data(self):
        body = self.ask("근처 추천해줘", focused=DONGHAE)
        self.assertIn("(예시 데이터)", body["reply"])
        self.assertIn("도보 10분", body["reply"])
        self.assertEqual(body["tool_used"], "nearby_spots")


class ModelPathTests(_Case):
    settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)

    def test_the_tool_result_carries_computed_minutes_and_says_they_are_estimates(self):
        tool = {"name": "nearby_spots", "station_id": None, "place_id": DONGHAE, "after": None}
        first = {"intent": "proposal", "reply": "찾아볼게요.", "chips": [], "proposals": [],
                 "sources": [], "tool_request": tool}
        final = {**first, "reply": "북평민속시장이 걸어서 약 6분이에요.", "tool_request": None,
                 "proposals": [{"type": "add", "target": MARKET, "reason": "걸어서 약 6분이에요",
                                "time_bound": False}]}
        with patch.object(llm, "chat_json", side_effect=[first, final]) as chat:
            response = self.client.post(URL, json={"message": "근처 추천해줘",
                                                   "context": {"focused": DONGHAE}})
        body = response.json()
        given = chat.call_args_list[1].args[1][-1]["content"]  # the tool result message
        self.assertIn('"source": "coordinates"', given)
        self.assertIn('"walk_minutes": 6', given)
        self.assertIn("어림", given)
        self.assertNotIn(DOJJEBIGOL, given)  # lodging is not handed to the model at all
        self.assertNotIn("예시 데이터", body["reply"])
        self.assertEqual([p["target"] for p in body["proposals"]], [MARKET])


if __name__ == "__main__":
    unittest.main()
