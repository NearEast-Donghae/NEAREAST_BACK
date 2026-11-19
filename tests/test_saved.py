"""Saved places and trains (modules/saved): what the user picked, kept per account.

Each server rule has a case where it must bite and one where it must not. Written
before the code. Real auth (cookie + CSRF) on a seeded in-memory database.
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from storage_support import createStorageApp

from neareast_api.core.config import Settings
from neareast_api.db.entities import SavedPlaceEntity, SavedTrainEntity, UserEntity
from neareast_api.modules.saved import service as saved_service

API = "/api/v1"
DONGHAE, MUKHO, CHUAM, MANGSANG = (
    "station_donghae", "station_mukho", "station_chuam", "station_mangsang")
MARKET, NONGOLDAM = "poi_bukpyeong_market", "poi_nongoldam"
PASSING_ONLY = "이 역은 열차가 서지 않아 관찰만 할 수 있어요"
# 1861: 추암 10:15 → 동해 10:27 → 묵호 10:37 정차, 망상 통과(10:42 어림). 841은 추암을 지나지 않음.


def item(body):
    """A saved item without its timestamps (the test database drops their timezone)."""
    return {key: value for key, value in body.items() if key not in ("createdAt", "updatedAt")}


class _Case(unittest.TestCase):
    def setUp(self):
        self.app = createStorageApp(Settings(chat_mock=True, llm_usage_log="/dev/null"))
        self.addCleanup(self.app.state.databaseEngine.dispose)
        self.client = self.login("owner@example.com")

    def login(self, email):
        client = TestClient(self.app)
        self.addCleanup(client.close)
        response = client.post(API + "/auth/signup", json={
            "email": email, "nickname": "여행자", "password": "password-for-tests"})
        self.assertEqual(response.status_code, 201, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        return client

    def place(self, placeId, priority="interested", client=None, status=201):
        response = (client or self.client).post(
            API + "/saved/places", json={"placeId": placeId, "priority": priority})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def train(self, trainNo, action, stationId, priority="interested", client=None, status=201):
        response = (client or self.client).post(API + "/saved/trains", json={
            "trainNo": trainNo, "action": action, "stationId": stationId, "priority": priority})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def saved(self, client=None):
        response = (client or self.client).get(API + "/saved")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class AccessTests(_Case):
    def test_login_is_required(self):
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.get(API + "/saved").status_code, 401)
        for path, body in (("/saved/places", {"placeId": MUKHO, "priority": "must"}),
                           ("/saved/trains", {"trainNo": "1861", "action": "ride",
                                              "stationId": DONGHAE, "priority": "must"})):
            self.assertEqual(anonymous.post(API + path, json=body).status_code, 401)

    def test_changes_need_the_csrf_token_but_reading_does_not(self):
        del self.client.headers["X-CSRF-Token"]
        response = self.client.post(API + "/saved/places", json={"placeId": MUKHO, "priority": "must"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(API + "/saved").status_code, 200)

    def test_another_users_items_are_not_found_and_not_listed(self):
        place = self.place(MUKHO)
        train = self.train("1861", "ride", DONGHAE)
        other = self.login("other@example.com")
        self.assertEqual(self.saved(other), {"places": [], "trains": [], "basis": self.saved()["basis"]})
        for path in (f"/saved/places/{place['id']}", f"/saved/trains/{train['id']}"):
            self.assertEqual(other.patch(API + path, json={"priority": "must"}).status_code, 404)
            self.assertEqual(other.delete(API + path).status_code, 404)
        self.assertEqual(len(self.saved()["places"]), 1)  # the owner still has them
        self.assertEqual(self.client.delete(API + f"/saved/places/{place['id']}").status_code, 204)
        self.assertEqual(self.client.delete(API + f"/saved/places/{place['id']}").status_code, 404)


class PlaceTests(_Case):
    def test_saved_place_comes_back_with_its_name(self):
        created = self.place(MUKHO, "must")
        self.assertEqual({k: created[k] for k in ("placeId", "name", "kind", "priority")},
                         {"placeId": MUKHO, "name": "묵호역", "kind": "station", "priority": "must"})
        self.assertEqual([item(p) for p in self.saved()["places"]], [item(created)])

    def test_priority_defaults_to_interested_and_can_be_changed(self):
        response = self.client.post(API + "/saved/places", json={"placeId": MARKET})
        self.assertEqual(response.json()["priority"], "interested")
        path = API + f"/saved/places/{response.json()['id']}"
        changed = self.client.patch(path, json={"priority": "must"})
        self.assertEqual((changed.status_code, changed.json()["priority"]), (200, "must"))
        self.assertEqual(self.saved()["places"][0]["priority"], "must")

    def test_saving_the_same_place_again_returns_the_first_one(self):
        first = self.place(MUKHO, "must")
        again = self.place(MUKHO, "interested", status=200)
        self.assertEqual(item(again), item(first))  # not duplicated, not silently changed
        self.assertEqual(len(self.saved()["places"]), 1)
        self.place(MARKET)  # a different place is a new item
        self.assertEqual(len(self.saved()["places"]), 2)

    def test_unknown_place_and_bad_values_are_rejected(self):
        response = self.client.post(API + "/saved/places", json={"placeId": "poi_gone"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("poi_gone", response.text)
        for body in ({"placeId": MUKHO, "priority": "maybe"}, {"priority": "must"},
                     {"placeId": MUKHO, "extra": 1}):
            self.assertEqual(self.client.post(API + "/saved/places", json=body).status_code, 422)
        self.assertEqual(self.saved()["places"], [])

    def test_at_most_fifty_places(self):
        owner = self.app.state.sessionFactory()
        self.addCleanup(owner.close)
        user = owner.scalar(select(UserEntity))
        places = ["station_chuam", "station_mangsang", "station_samhwa", "station_mukhohang",
                  "heritage_pyeongneung", NONGOLDAM, "poi_chotdaebawi"]
        self.place(MUKHO)
        # The catalog has 14 places, so the limit is lowered to reach it.
        with patch.object(saved_service, "SAVED_LIMIT", 8):
            for placeId in places:
                self.place(placeId)
            full = self.client.post(API + "/saved/places", json={"placeId": MARKET})
            self.assertEqual(full.status_code, 409, full.text)
            self.place(MUKHO, status=200)  # an item already saved is still returned
        self.assertEqual(len(self.saved()["places"]), 8)
        self.assertEqual(owner.query(SavedPlaceEntity).filter_by(userId=user.id).count(), 8)

    def test_the_real_limit_is_fifty(self):
        self.assertEqual(saved_service.SAVED_LIMIT, 50)


class TrainTests(_Case):
    def test_saved_train_comes_back_with_type_time_and_certainty(self):
        created = self.train("1861", "ride", DONGHAE, "must")
        expected = {
            "trainNo": "1861", "action": "ride", "stationId": DONGHAE, "stationName": "동해역",
            "priority": "must", "available": True, "trainType": None, "line": "동해선",
            "time": "10:27", "arr": "10:23", "dep": "10:27", "passing": False,
            "timeBasis": None, "certainty": "estimated",
        }
        self.assertEqual({k: created[k] for k in expected}, expected)
        self.assertTrue(created["destination"])
        body = self.saved()
        self.assertEqual([item(t) for t in body["trains"]], [item(created)])
        self.assertIn("토요일 3주", body["basis"])

    def test_watching_at_a_passing_station_is_fine_and_its_time_is_an_estimate(self):
        created = self.train("1861", "observe", MANGSANG)
        self.assertEqual((created["time"], created["passing"]), ("10:42", True))
        self.assertIn("어림", created["timeBasis"])
        self.assertEqual(created["certainty"], "estimated")

    def test_riding_at_a_passing_station_is_rejected(self):
        response = self.client.post(API + "/saved/trains", json={
            "trainNo": "1861", "action": "ride", "stationId": MANGSANG, "priority": "must"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], PASSING_ONLY)
        self.assertEqual(self.saved()["trains"], [])
        self.train("1861", "ride", MUKHO)  # the same train where it stops

    def test_a_train_that_does_not_come_by_that_station_is_rejected(self):
        response = self.client.post(API + "/saved/trains", json={
            "trainNo": "841", "action": "observe", "stationId": CHUAM})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("지나지 않아요", response.json()["detail"])
        self.train("841", "observe", MUKHO)  # where it does come by
        for body in ({"trainNo": "99999", "action": "ride", "stationId": DONGHAE},
                     {"trainNo": "1861", "action": "ride", "stationId": "station_gone"},
                     {"trainNo": "1861", "action": "ride", "stationId": MARKET},
                     {"trainNo": "1861", "action": "board", "stationId": DONGHAE}):
            with self.subTest(body=body):
                response = self.client.post(API + "/saved/trains", json=body)
                self.assertEqual(response.status_code, 422, response.text)

    def test_saving_the_same_train_again_returns_the_first_one(self):
        first = self.train("1861", "ride", DONGHAE, "must")
        self.assertEqual(item(self.train("1861", "ride", DONGHAE, "interested", status=200)), item(first))
        self.assertEqual(item(self.train("01861", "ride", DONGHAE, status=200)), item(first))  # same no
        self.assertEqual(len(self.saved()["trains"]), 1)
        # The same train for something else, or somewhere else, is another item.
        self.train("1861", "observe", DONGHAE)
        self.train("1861", "ride", MUKHO)
        self.assertEqual(len(self.saved()["trains"]), 3)

    def test_changing_priority_and_action(self):
        item = self.train("1861", "observe", MANGSANG)
        path = API + f"/saved/trains/{item['id']}"
        changed = self.client.patch(path, json={"priority": "must"})
        self.assertEqual((changed.status_code, changed.json()["priority"]), (200, "must"))
        blocked = self.client.patch(path, json={"action": "ride"})  # still a passing station
        self.assertEqual((blocked.status_code, blocked.json()["detail"]), (422, PASSING_ONLY))
        self.assertEqual(self.client.patch(path, json={}).status_code, 422)
        at_mukho = self.train("1861", "observe", MUKHO)
        mukho = API + f"/saved/trains/{at_mukho['id']}"
        self.assertEqual(self.client.patch(mukho, json={"action": "ride"}).json()["action"], "ride")
        # Turning it into an item that already exists is a conflict, not a second copy.
        self.train("1861", "observe", MUKHO)
        self.assertEqual(self.client.patch(mukho, json={"action": "observe"}).status_code, 409)

    def test_delete(self):
        item = self.train("1861", "ride", DONGHAE)
        self.assertEqual(self.client.delete(API + f"/saved/trains/{item['id']}").status_code, 204)
        self.assertEqual(self.saved()["trains"], [])
        self.assertEqual(self.client.delete(API + f"/saved/trains/{item['id']}").status_code, 404)

    def test_at_most_fifty_trains(self):
        with patch.object(saved_service, "SAVED_LIMIT", 2):
            self.train("1861", "ride", DONGHAE)
            self.train("1861", "ride", MUKHO)
            full = self.client.post(API + "/saved/trains", json={
                "trainNo": "1864", "action": "ride", "stationId": DONGHAE})
            self.assertEqual(full.status_code, 409, full.text)
            self.train("1861", "ride", DONGHAE, status=200)
            self.place(MUKHO)  # places have their own limit
            self.place(MARKET)
            self.assertEqual(self.client.post(
                API + "/saved/places", json={"placeId": NONGOLDAM}).status_code, 409)
        session = self.app.state.sessionFactory()
        self.addCleanup(session.close)
        self.assertEqual(session.query(SavedTrainEntity).count(), 2)

    def test_a_train_gone_from_the_timetable_is_listed_as_unavailable(self):
        item = self.train("1861", "ride", DONGHAE)
        session = self.app.state.sessionFactory()
        self.addCleanup(session.close)
        session.get(SavedTrainEntity, item["id"]).trainNo = "99999"
        session.commit()
        listed = self.saved()["trains"][0]
        self.assertEqual((listed["available"], listed["time"], listed["certainty"]),
                         (False, None, None))


class NearieUsesSavedItemsTests(_Case):
    def ask(self, message, **context):
        response = self.client.post(
            API + "/chat/messages", json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_saved_places_are_pins_for_the_logged_in_user(self):
        self.assertIn("지도에서", self.ask("여기 근처 추천해줘")["reply"])  # nothing to go by yet
        self.place(MUKHO)
        body = self.ask("여기 근처 추천해줘")  # no pins in the request
        self.assertIn("묵호역", body["reply"])

    def test_a_saved_place_is_not_proposed_again(self):
        before = self.ask("근처 추천해줘", focused=MUKHO)
        self.assertIn(NONGOLDAM, [p["target"] for p in before["proposals"]])
        self.place(NONGOLDAM)
        after = self.ask("근처 추천해줘", focused=MUKHO)
        self.assertNotIn(NONGOLDAM, [p["target"] for p in after["proposals"]])

    def test_request_pins_are_merged_with_saved_places(self):
        self.place(NONGOLDAM)
        body = self.ask("근처 추천해줘", focused=DONGHAE, pins=[MARKET])
        self.assertNotIn(MARKET, [p["target"] for p in body["proposals"]])
        body = self.ask("근처 추천해줘", focused=MUKHO, pins=[MARKET])
        self.assertNotIn(NONGOLDAM, [p["target"] for p in body["proposals"]])

    def test_plan_request_carries_must_places_and_saved_trains(self):
        self.place(MUKHO, "must")
        self.place(MARKET, "interested")
        self.train("1861", "ride", DONGHAE, "must")
        self.train("1861", "observe", MANGSANG)
        self.train("1864", "observe", MUKHO)
        body = self.ask("일정 짜줘", date="2026-11-21")
        self.assertEqual(body["intent"], "plan_request")
        payload = body["action"]["payload"]
        self.assertEqual(payload["travelDate"], "2026-11-21")
        self.assertEqual(payload["requiredPlaceIds"], [MUKHO])  # interested places are not required
        self.assertEqual(payload["selectedTrainIds"], ["1861", "1864"])
        self.assertEqual(payload["selectedTrains"], [
            {"trainNo": "1861", "action": "ride", "stationId": DONGHAE},
            {"trainNo": "1861", "action": "observe", "stationId": MANGSANG},
            {"trainNo": "1864", "action": "observe", "stationId": MUKHO}])
        # The payload is accepted as planning conditions as it is.
        stored = self.client.post(API + "/planning-requests",
                                  json={"source": "chat", "conditions": payload})
        self.assertEqual(stored.status_code, 201, stored.text)

    def test_saved_trains_alone_are_enough_to_hand_over(self):
        self.train("1861", "ride", DONGHAE)
        body = self.ask("일정 짜줘", date="2026-11-21")
        self.assertEqual(body["action"]["payload"]["selectedTrainIds"], ["1861"])
        self.assertEqual(body["action"]["payload"]["requiredPlaceIds"], [])

    def test_pins_on_the_screen_count_as_required(self):
        self.place(MUKHO, "must")
        body = self.ask("일정 짜줘", date="2026-11-21", pins=[MARKET, MUKHO])
        self.assertEqual(body["action"]["payload"]["requiredPlaceIds"], [MUKHO, MARKET])

    def test_conversation_messages_use_saved_items_too(self):
        self.place(MUKHO, "must")
        conversation = self.client.post(API + "/conversations", json={}).json()["id"]
        response = self.client.post(f"{API}/conversations/{conversation}/messages", json={
            "requestId": "11111111-1111-4111-8111-111111111111", "message": "일정 짜줘",
            "context": {"date": "2026-11-21"}})
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()["response"]["action"]["payload"]
        self.assertEqual(payload["requiredPlaceIds"], [MUKHO])

    def test_another_users_saved_items_do_not_leak_into_my_chat(self):
        self.place(MUKHO, "must")
        other = self.login("other@example.com")
        response = other.post(API + "/chat/messages", json={
            "message": "일정 짜줘", "context": {"date": "2026-11-21"}})
        self.assertIsNone(response.json()["action"])  # nothing saved: asks to pin first


if __name__ == "__main__":
    unittest.main()
