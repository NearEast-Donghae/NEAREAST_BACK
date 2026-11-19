"""Knowledge content integrity, retrieval order and the embedding cache (no network)."""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app
from neareast_api.modules.chat import retrieval, tools

ROOT = Path(__file__).resolve().parents[1]
URL = "/api/v1/chat/messages"


def load_ingest():
    spec = importlib.util.spec_from_file_location("ingest", ROOT / "scripts" / "ingest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


class ParseTests(unittest.TestCase):
    def test_front_matter_lists_and_body(self):
        doc = retrieval.parse_doc(
            "---\nid: a\ntitle: A\nkind: rail\nkeywords: [x, y z]\n---\nfirst.\n\nsecond."
        )
        self.assertEqual(doc.keywords, ("x", "y z"))
        self.assertEqual(doc.places, ())
        self.assertEqual(doc.summary, "first.")

    def test_malformed_documents_are_rejected(self):
        for text in ("no front matter", "---\nid: a\n", "---\nid: a\nkind: x\n---\nbody"):
            with self.subTest(text):
                with self.assertRaises(retrieval.ContentError):
                    retrieval.parse_doc(text)


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
    def test_missing_content_is_503(self):
        with tempfile.TemporaryDirectory() as empty:
            client = TestClient(create_app(Settings(content_dir=empty)))
            with self.assertLogs("neareast_api.modules.chat.service", "ERROR"):
                response = client.post(URL, json={"message": "안녕"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(empty, response.text)

    def test_document_id_must_match_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "data", root / "data")
            (root / "knowledge").mkdir()
            (root / "knowledge" / "a.md").write_text("---\nid: b\ntitle: B\nkind: rail\n---\nx")
            with self.assertRaises(retrieval.ContentError):
                retrieval.load_docs(root)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.ingest = load_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "knowledge", self.root / "knowledge")
        shutil.copytree(ROOT / "data", self.root / "data")
        (self.root / "data" / "embeddings.json").unlink(missing_ok=True)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()
        retrieval._load.cache_clear()

    def fake_embed(self, model, texts):
        self.calls.append(len(texts))
        return [(float(len(t)), 1.0) for t in texts]

    def test_dry_run_writes_nothing(self):
        report = self.ingest.ingest(self.root, "m1", None)
        self.assertEqual(len(report["embedded"]), len(list((self.root / "knowledge").glob("*.md"))))
        self.assertFalse((self.root / "data" / "embeddings.json").exists())

    def test_only_changed_documents_are_re_embedded(self):
        self.ingest.ingest(self.root, "m1", self.fake_embed)
        doc = self.root / "knowledge" / "rail_lines.md"
        doc.write_text(doc.read_text("utf-8") + "\n추가 문장.", "utf-8")
        report = self.ingest.ingest(self.root, "m1", self.fake_embed)
        self.assertEqual(report["embedded"], ["rail_lines"])
        self.assertEqual(self.calls[1], 1)
        self.ingest.ingest(self.root, "m1", self.fake_embed)
        self.assertEqual(len(self.calls), 2, "unchanged docs must not call the API")

    def test_cache_is_loaded_and_stale_entries_are_reported(self):
        self.ingest.ingest(self.root, "m1", self.fake_embed)
        content = retrieval.load_content(str(self.root))
        self.assertEqual(content.embedding_model, "m1")
        self.assertEqual(set(content.vectors), set(content.docs))
        retrieval._load.cache_clear()
        doc = self.root / "knowledge" / "rail_lines.md"
        doc.write_text(doc.read_text("utf-8") + "\n변경.", "utf-8")
        with self.assertLogs("neareast_api.modules.chat.retrieval", "WARNING") as logs:
            content = retrieval.load_content(str(self.root))
        self.assertIn("rail_lines", logs.output[0])
        self.assertNotIn("rail_lines", content.vectors)


if __name__ == "__main__":
    unittest.main()
