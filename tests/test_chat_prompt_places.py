"""The place list in the prompt holds only the places a request can be about.

The catalog has grown past a hundred places. Listing all of them doubled every prompt, so
the fixed part of the prompt carries none and each request lists the stations, what is on
the screen, what the user named, what the retrieved documents are about, and what is a
short walk from those.
"""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app

from neareast_api.core.config import Settings
from neareast_api.db.entities import PlaceEntity
from neareast_api.modules.chat import llm, prompts, service

URL = "/api/v1/chat/messages"
DONGHAE, MUKHO = "station_donghae", "station_mukho"
MARKET, CAVE, NONGOLDAM, BEACH, CHOTDAE = (
    "poi_bukpyeong_market", "poi_cheongok_cave", "poi_nongoldam", "poi_mangsang_beach",
    "poi_chotdaebawi")
STATIONS = {"station_donghae", "station_mukho", "station_chuam", "station_mangsang",
            "station_mangsang_beach", "station_samhwa", "station_mukhohang"}
ANSWER = {"intent": "smalltalk", "reply": "좋아요.", "chips": [], "proposals": [],
          "sources": [], "tool_request": None}


class PromptPlacesTests(unittest.TestCase):
    settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)

    def setUp(self):
        self.app = create_app(self.settings)
        self.addCleanup(self.app.state.databaseEngine.dispose)
        with self.app.state.sessionFactory() as session:
            # made up: the market and the cave are a short walk from 동해역, the beach is not
            for place_id, lat in ((DONGHAE, 37.500), (MARKET, 37.503), (CAVE, 37.506),
                                  (BEACH, 37.900)):
                place = session.get(PlaceEntity, place_id)
                place.lat, place.lng = lat, 129.10
                if place_id in (CAVE, BEACH):
                    place.operatingInfo = {"tourApi": {"contentTypeId": "12"}}
            session.commit()
        self.client = TestClient(self.app)

    def listed(self, message, history=(), **context):
        """(place ids in the request's [장소] list, the fixed first message)."""
        with patch.object(llm, "chat_json", return_value=dict(ANSWER)) as chat:
            response = self.client.post(URL, json={"message": message, "context": context,
                                                   "history": list(history)})
        self.assertEqual(response.status_code, 200, response.text)
        messages = chat.call_args.args[1]
        block = messages[-2]["content"].split("[장소]", 1)[1].split("[문서]", 1)[0]
        ids = [line[2:].split(":", 1)[0] for line in block.splitlines() if line.startswith("- ")]
        return ids, messages[0]["content"]

    def test_the_fixed_part_lists_no_places_and_never_changes(self):
        ids, fixed = self.listed("안녕")
        self.assertNotIn("station_donghae:", fixed)
        self.assertNotIn("poi_", fixed)
        _, again = self.listed("여기 뭐야?", focused=MARKET, pins=[CAVE])
        self.assertEqual(fixed, again)
        self.assertEqual(fixed, prompts.build_static())

    def test_every_station_is_always_listed(self):
        ids, _ = self.listed("안녕")
        self.assertEqual(set(ids), STATIONS)  # nothing else is in play

    def test_screen_named_and_nearby_places_are_added(self):
        ids, _ = self.listed("여기 근처 볼 곳", focused=DONGHAE)
        self.assertEqual(ids[0], DONGHAE)  # what is on the screen comes first
        self.assertLessEqual({MARKET, CAVE} | STATIONS, set(ids))  # a short walk away
        self.assertNotIn(BEACH, ids)  # too far, and not in play
        named, _ = self.listed("망상해수욕장은 어때?")
        self.assertIn(BEACH, named)
        pinned, _ = self.listed("안녕", pins=[CHOTDAE])
        self.assertIn(CHOTDAE, pinned)

    def test_a_place_named_earlier_in_the_conversation_stays_listed(self):
        earlier = [{"role": "user", "content": "망상해수욕장 어때?"},
                   {"role": "assistant", "content": "좋아요."}]
        ids, _ = self.listed("거기 뭐 있어?", history=earlier)
        self.assertIn(BEACH, ids)

    def test_places_the_retrieved_documents_are_about_are_listed(self):
        # The 묵호역 document is also about 논골담길.
        ids, _ = self.listed("여기 뭐야?", focused=MUKHO)
        self.assertIn(NONGOLDAM, ids)

    def test_the_list_is_bounded_and_has_no_duplicates(self):
        everything = sorted(STATIONS) + [MARKET, CAVE, NONGOLDAM, BEACH, CHOTDAE,
                                         "poi_dojjebigol", "heritage_pyeongneung"]
        ids, _ = self.listed("안녕", pins=everything)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(ids), service.MAX_PROMPT_PLACES)
        with patch.object(service, "MAX_PROMPT_PLACES", 3):
            few, _ = self.listed("안녕", focused=DONGHAE, pins=[MARKET, CAVE, BEACH])
        self.assertEqual(few, [DONGHAE, MARKET, CAVE])  # the screen is kept, the rest dropped


if __name__ == "__main__":
    unittest.main()
