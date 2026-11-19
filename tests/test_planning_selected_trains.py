"""Planning conditions can say what each picked train is for (ride or observe, and where).

selectedTrains is optional detail next to selectedTrainIds: a client that only sends
train numbers keeps working, and when both are sent they must name the same trains.
"""

import unittest

from fastapi.testclient import TestClient
from storage_support import createStorageApp

URL = "/api/v1/planning-requests"
RIDE = {"trainNo": "1864", "action": "ride", "stationId": "station_donghae"}
OBSERVE = {"trainNo": "1861", "action": "observe", "stationId": "station_mangsang"}


class SelectedTrainsTests(unittest.TestCase):
    def setUp(self):
        self.app = createStorageApp()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.databaseEngine.dispose)
        response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "owner@example.com", "nickname": "여행자", "password": "password-for-tests"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.client.headers["X-CSRF-Token"] = response.json()["csrfToken"]

    def create(self, conditions):
        return self.client.post(URL, json={"source": "planner", "conditions": conditions})

    def test_train_numbers_alone_still_work_and_the_new_field_reads_null(self):
        response = self.create({"selectedTrainIds": ["1864", "1861"]})
        self.assertEqual(response.status_code, 201, response.text)
        conditions = response.json()["conditions"]
        self.assertEqual(conditions["selectedTrainIds"], ["1864", "1861"])
        self.assertIsNone(conditions["selectedTrains"])
        self.assertIsNone(self.create({}).json()["conditions"]["selectedTrains"])

    def test_both_fields_naming_the_same_trains_are_stored_and_read_back(self):
        response = self.create(
            {"selectedTrainIds": ["1864", "1861"], "selectedTrains": [OBSERVE, RIDE]})
        self.assertEqual(response.status_code, 201, response.text)
        stored = self.client.get(f"{URL}/{response.json()['id']}").json()["conditions"]
        self.assertEqual(stored["selectedTrains"], [OBSERVE, RIDE])
        self.assertEqual(stored["selectedTrainIds"], ["1864", "1861"])

    def test_one_train_can_be_picked_for_two_things(self):
        watch = {"trainNo": "1864", "action": "observe", "stationId": "station_mukho"}
        response = self.create({"selectedTrainIds": ["1864"], "selectedTrains": [RIDE, watch]})
        self.assertEqual(response.status_code, 201, response.text)

    def test_a_train_in_only_one_field_is_rejected(self):
        cases = {
            "only in selectedTrains": {"selectedTrainIds": [], "selectedTrains": [RIDE]},
            "only in selectedTrainIds": {"selectedTrainIds": ["1864", "1861"], "selectedTrains": [RIDE]},
            "empty detail for listed trains": {"selectedTrainIds": ["1864"], "selectedTrains": []},
            "different trains": {"selectedTrainIds": ["1861"], "selectedTrains": [RIDE]},
        }
        for name, conditions in cases.items():
            with self.subTest(name):
                response = self.create(conditions)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("selectedTrains", response.text)

    def test_empty_detail_with_no_trains_is_consistent(self):
        response = self.create({"selectedTrainIds": [], "selectedTrains": []})
        self.assertEqual(response.status_code, 201, response.text)

    def test_bad_action_or_unknown_station_is_rejected(self):
        for name, train in {
            "action": {**RIDE, "action": "board"},
            "station": {**RIDE, "stationId": "station_nowhere"},
            "extra field": {**RIDE, "must": True},
        }.items():
            with self.subTest(name):
                response = self.create({"selectedTrainIds": ["1864"], "selectedTrains": [train]})
                self.assertEqual(response.status_code, 422, response.text)

    def test_update_is_checked_the_same_way(self):
        created = self.create({"selectedTrainIds": ["1864"]}).json()
        path = f"{URL}/{created['id']}"
        mismatch = self.client.patch(path, json={
            "expectedRevision": 1,
            "conditions": {"selectedTrainIds": ["1864"], "selectedTrains": [OBSERVE]}})
        self.assertEqual(mismatch.status_code, 422, mismatch.text)
        updated = self.client.patch(path, json={
            "expectedRevision": 1,
            "conditions": {"selectedTrainIds": ["1864"], "selectedTrains": [RIDE]}})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["conditions"]["selectedTrains"], [RIDE])


if __name__ == "__main__":
    unittest.main()
