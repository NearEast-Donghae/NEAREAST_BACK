"""Knowledge content integrity, retrieval order and the embedding cache (no network)."""

import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from legacy_support import create_app
from legacy_support import setUpModule as setUpModule
from legacy_support import tearDownModule as tearDownModule

from neareast_api.core.config import Settings
from neareast_api.modules.chat import retrieval, tools

ROOT = Path(__file__).resolve().parents[1]
URL = "/api/v1/chat/messages"


class ContentIntegrityTests(unittest.TestCase):
    """Map markers, documents and tools must agree on ids (the contract with the front)."""

    @classmethod
    def setUpClass(cls):
        cls.content = retrieval.load_content(str(ROOT))

    def test_place_ids_follow_the_naming_rule(self):
        for place in self.content.places.values():
            with self.subTest(place.id):
                prefix = place.id.split("_", 1)[0]
                self.assertIn(prefix, {"station", "poi", "heritage", "spot"})
                self.assertEqual(prefix, place.kind)

    def test_aliases_do_not_collide_with_other_places(self):
        labels = {}
        for place in self.content.places.values():
            for label in (place.name, *place.aliases):
                self.assertNotIn(label, labels, f"{label}: {place.id} vs {labels.get(label)}")
                labels[label] = place.id

    def test_every_place_has_a_document_with_the_same_id(self):
        missing = set(self.content.places) - set(self.content.docs)
        self.assertEqual(missing, set())

    def test_documents_only_reference_known_places(self):
        for doc in self.content.docs.values():
            with self.subTest(doc.id):
                self.assertTrue(doc.summary)
                for place_id in doc.places:
                    self.assertIsNotNone(self.content.place(place_id), place_id)

    def test_mock_tools_only_use_catalog_ids(self):
        ids = set(tools._TIMETABLE) | set(tools._NEARBY)
        ids |= {spot for links in tools._NEARBY.values() for spot, _ in links}
        self.assertEqual(ids - set(self.content.places), set())


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = retrieval.load_content(str(ROOT))

    def test_context_first_then_keyword(self):
        hits = self.content.search("평능역 알려줘", ["station_donghae"])
        reasons = [h.reason for h in hits]
        self.assertEqual(hits[0].doc.id, "station_donghae")  # direct id lookup first
        self.assertEqual(reasons, sorted(reasons))  # every context hit before any keyword hit
        self.assertIn(("heritage_pyeongneung", "keyword"), [(h.doc.id, h.reason) for h in hits])

    def test_many_pins_do_not_crowd_out_an_explicit_mention(self):
        pins = ["station_donghae", "station_mukho", "station_chuam", "station_mangsang"]
        ids = [h.doc.id for h in self.content.search("평능역 알려줘", pins)]
        self.assertIn("heritage_pyeongneung", ids)

    def test_longer_keyword_ranks_first(self):
        hits = self.content.search("묵호항역 화물")
        self.assertEqual(hits[0].doc.id, "station_mukhohang")

    def test_place_in_text_prefers_earliest_then_longest_name(self):
        self.assertEqual(self.content.place_in_text("묵호항역이랑 동해역").id, "station_mukhohang")

    def test_short_names_are_understood(self):
        expected = {
            "묵호 열차 언제 와?": "station_mukho",
            "동해 열차 리듬": "station_donghae",
            "추암 근처": "station_chuam",
            "천곡동굴 어때?": "poi_cheongok_cave",
        }
        for text, place_id in expected.items():
            with self.subTest(text):
                self.assertEqual(self.content.place_in_text(text).id, place_id)

    def test_aliases_do_not_swallow_longer_names_or_city_names(self):
        self.assertEqual(self.content.place_in_text("묵호항역 화물").id, "station_mukhohang")
        self.assertEqual(self.content.place_in_text("망상해수욕장 가고 싶어").id, "poi_mangsang_beach")
        self.assertEqual(self.content.place_in_text("추암 촛대바위").id, "poi_chotdaebawi")
        self.assertIsNone(self.content.place_in_text("동해시 관광지 알려줘"))
        self.assertIsNone(self.content.place_in_text("부산역 맛집"))

    def test_vector_step_needs_matching_model(self):
        doc = self.content.docs["rail_lines"]
        content = retrieval.Content(
            self.content.places, self.content.docs, "m1", {doc.id: (1.0, 0.0)}
        )
        embed = lambda text: ("m1", (1.0, 0.0))  # noqa: E731
        self.assertEqual([h.doc.id for h in content.search("기찻길", embed=embed)], ["rail_lines"])
        with self.assertLogs("neareast_api.modules.chat.retrieval", "WARNING"):
            other = content.search("기찻길", embed=lambda text: ("m2", (1.0, 0.0)))
        self.assertEqual(other, [])


class ContentFailureTests(unittest.TestCase):
    def test_empty_database_is_503(self):
        client = TestClient(create_app(Settings(), seed=False))
        response = client.post(URL, json={"message": "안녕"})
        self.assertEqual(response.status_code, 503)

    def test_health_does_not_require_content(self):
        with TestClient(create_app(Settings(), seed=False)) as client:
            self.assertEqual(client.get("/health/live").status_code, 200)


if __name__ == "__main__":
    unittest.main()
