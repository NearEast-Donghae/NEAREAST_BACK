"""Places API: the ids every screen and the chat share."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1/places"


class PlacesApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(Settings()))
        cls.catalog = json.loads((ROOT / "data" / "places.json").read_text("utf-8"))["places"]

    def test_list_returns_the_whole_catalog_with_marker_fields(self):
        body = self.client.get(API).json()
        self.assertEqual([p["id"] for p in body["places"]], [p["id"] for p in self.catalog])
        self.assertEqual(set(body["places"][0]),
                         {"id", "name", "kind", "passenger_stop", "aliases", "lat", "lng"})

    def test_kind_filter(self):
        stations = self.client.get(API + "?kind=station").json()["places"]
        self.assertTrue(stations)
        self.assertEqual({p["kind"] for p in stations}, {"station"})
        self.assertEqual(self.client.get(API + "?kind=spot").json()["places"], [])
        self.assertEqual(self.client.get(API + "?kind=castle").status_code, 422)

    def test_detail_adds_description_and_train_availability(self):
        station = self.client.get(API + "/station_donghae").json()
        self.assertIn("1940", station["summary"])
        self.assertTrue(station["description"].startswith(station["summary"]))
        self.assertEqual(station["source"], "station_donghae")
        self.assertTrue(station["has_trains"])
        passing = self.client.get(API + "/station_mangsang").json()
        self.assertIs(passing["passenger_stop"], False)
        self.assertTrue(passing["has_trains"])  # estimated passing times
        cave = self.client.get(API + "/poi_cheongok_cave").json()
        self.assertFalse(cave["has_trains"])
        self.assertIn("09:00~18:00", cave["description"])
        self.assertFalse(self.client.get(API + "/station_samhwa").json()["has_trains"])

    def test_unknown_place_is_404(self):
        response = self.client.get(API + "/station_nowhere")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "place not found"})

    def test_missing_catalog_is_503(self):
        with tempfile.TemporaryDirectory() as empty:
            client = TestClient(create_app(Settings(content_dir=empty)))
            self.assertEqual(client.get(API).status_code, 503)
            self.assertEqual(client.get(API + "/station_donghae").status_code, 503)


if __name__ == "__main__":
    unittest.main()
