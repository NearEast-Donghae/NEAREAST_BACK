import unittest
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from storage_support import createStorageApp

from neareast_api.db.entities import (
    AuthSessionEntity,
    ItineraryRevisionEntity,
    KnowledgeChunkEntity,
    KnowledgeDocumentEntity,
    UserEntity,
    currentTime,
)
from neareast_api.ingestion.service import IngestionService


class ServiceStorageTests(unittest.TestCase):
    def setUp(self):
        self.app = createStorageApp()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.databaseEngine.dispose)

    def signup(self, client=None, email="owner@example.com"):
        client = client or self.client
        response = client.post(
            "/api/v1/auth/signup",
            json={"email": email, "nickname": "여행자", "password": "password-for-tests"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        return response.json()["user"]

    def createConversation(self):
        response = self.client.post("/api/v1/conversations", json={})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def itineraryPayload(self):
        return {
            "source": "planner",
            "title": "묵호 여행",
            "travelDateLabel": "2026.11.21 (토)",
            "timeRange": "10:00–17:00",
            "stations": ["묵호역"],
            "trains": [],
            "activities": [],
            "timeline": [
                {
                    "time": "10:00",
                    "icon": "○",
                    "description": "묵호역 출발",
                    "placeId": "station_mukho",
                }
            ],
        }

    def test_chat_and_plans_require_login(self):
        for path in [
            "/chat/persona",
            "/conversations",
            "/itineraries",
            "/planning-requests",
            "/users/me",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/api/v1" + path).status_code, 401)
        self.assertEqual(self.client.get("/health/live").status_code, 200)

    def test_public_places_and_trains_do_not_require_login(self):
        for path in [
            "/places",
            "/places/station_mukho",
            "/stations/station_donghae/trains",
            "/trains/841",
            "/trains?from=station_donghae&to=station_mukho",
        ]:
            with self.subTest(path=path):
                response = self.client.get("/api/v1" + path)
                self.assertEqual(response.status_code, 200, response.text)

    def test_anonymous_chat_and_plan_writes_are_rejected(self):
        requests = [
            ("/chat/messages", {"message": "안녕"}),
            ("/conversations", {}),
            ("/itineraries", self.itineraryPayload()),
            ("/planning-requests", {"source": "planner", "conditions": {}}),
        ]
        for path, payload in requests:
            with self.subTest(path=path):
                self.assertEqual(self.client.post("/api/v1" + path, json=payload).status_code, 401)
        self.assertEqual(
            self.client.post("/api/v1/planning-requests/unknown/generations").status_code, 401
        )

    def test_signup_hashes_password_and_normalizes_email(self):
        user = self.signup(email="OWNER@Example.com")
        self.assertEqual(user["email"], "owner@example.com")
        self.assertNotIn("passwordHash", user)
        with self.app.state.sessionFactory() as session:
            entity = session.get(UserEntity, user["id"])
            self.assertTrue(entity.passwordHash.startswith("$argon2id$"))
            self.assertNotEqual(entity.passwordHash, "password-for-tests")
            authSession = session.scalar(select(AuthSessionEntity))
            self.assertNotEqual(authSession.tokenHash, self.client.cookies["neareast_session"])
        duplicate = self.client.post(
            "/api/v1/auth/signup",
            json={
                "email": "owner@example.com",
                "nickname": "중복",
                "password": "password-for-tests",
            },
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_login_logout_and_invalid_credentials(self):
        self.signup()
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/users/me").status_code, 401)
        wrong = self.client.post(
            "/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong-password"}
        )
        self.assertEqual(wrong.status_code, 401)
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "password-for-tests"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/v1/users/me").status_code, 200)

    def test_expired_session_is_rejected(self):
        self.signup()
        with self.app.state.sessionFactory() as session:
            session.scalar(select(AuthSessionEntity)).expiresAt = currentTime() - timedelta(
                seconds=1
            )
            session.commit()
        self.assertEqual(self.client.get("/api/v1/users/me").status_code, 401)

    def test_csrf_and_cross_origin_login_are_rejected(self):
        self.signup()
        self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.client.post("/api/v1/conversations", json={}).status_code, 403)
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "password-for-tests"},
            headers={"Origin": "https://untrusted.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_conversations_have_separate_history_and_support_retry(self):
        self.signup()
        firstId, secondId = self.createConversation(), self.createConversation()
        request = {"requestId": str(uuid4()), "message": "묵호역 알려줘"}
        first = self.client.post(f"/api/v1/conversations/{firstId}/messages", json=request)
        self.assertEqual(first.status_code, 201, first.text)
        again = self.client.post(f"/api/v1/conversations/{firstId}/messages", json=request)
        self.assertEqual(first.json()["id"], again.json()["id"])
        self.assertEqual(
            len(self.client.get(f"/api/v1/conversations/{firstId}/messages").json()), 2
        )
        self.assertEqual(self.client.get(f"/api/v1/conversations/{secondId}/messages").json(), [])
        changed = self.client.post(
            f"/api/v1/conversations/{firstId}/messages", json={**request, "message": "다른 메시지"}
        )
        self.assertEqual(changed.status_code, 409)

    def test_user_cannot_read_or_modify_other_users_content(self):
        self.signup()
        conversationId = self.createConversation()
        itinerary = self.client.post("/api/v1/itineraries", json=self.itineraryPayload()).json()
        with TestClient(self.app) as other:
            self.signup(other, "other@example.com")
            self.assertEqual(other.get("/api/v1/conversations").json(), [])
            self.assertEqual(other.get("/api/v1/itineraries").json(), [])
            for path in [
                f"/conversations/{conversationId}",
                f"/conversations/{conversationId}/messages",
                f"/itineraries/{itinerary['id']}",
                f"/itineraries/{itinerary['id']}/revisions",
            ]:
                self.assertEqual(other.get("/api/v1" + path).status_code, 404)
            self.assertEqual(
                other.delete(f"/api/v1/conversations/{conversationId}").status_code, 404
            )
            self.assertEqual(
                other.delete(f"/api/v1/itineraries/{itinerary['id']}").status_code, 404
            )

    def test_rename_and_delete_conversation(self):
        self.signup()
        conversationId = self.createConversation()
        response = self.client.patch(
            f"/api/v1/conversations/{conversationId}", json={"title": "주말 여행"}
        )
        self.assertEqual(response.json()["title"], "주말 여행")
        self.assertEqual(
            self.client.delete(f"/api/v1/conversations/{conversationId}").status_code, 204
        )
        self.assertEqual(
            self.client.get(f"/api/v1/conversations/{conversationId}/messages").status_code, 404
        )

    def test_itinerary_storage_revisions_and_conflict(self):
        self.signup()
        payload = self.itineraryPayload()
        response = self.client.post("/api/v1/itineraries", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        itineraryId = response.json()["id"]
        updated = self.client.put(
            f"/api/v1/itineraries/{itineraryId}",
            json={**payload, "title": "수정된 여행", "expectedRevision": 1},
        )
        self.assertEqual(updated.json()["revision"], 2)
        stale = self.client.put(
            f"/api/v1/itineraries/{itineraryId}", json={**payload, "expectedRevision": 1}
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            len(self.client.get(f"/api/v1/itineraries/{itineraryId}/revisions").json()), 2
        )
        self.client.delete(f"/api/v1/itineraries/{itineraryId}")
        with self.app.state.sessionFactory() as session:
            self.assertEqual(list(session.scalars(select(ItineraryRevisionEntity))), [])

    def test_invalid_places_and_conditions_are_rejected(self):
        self.signup()
        response = self.client.post(
            "/api/v1/planning-requests",
            json={"source": "planner", "conditions": {"requiredPlaceIds": ["unknown"]}},
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/api/v1/planning-requests",
            json={"source": "planner", "conditions": {"startTime": "17:00", "endTime": "10:00"}},
        )
        self.assertEqual(response.status_code, 422)

    def test_planning_conditions_are_persisted_and_generator_is_not_faked(self):
        self.signup()
        response = self.client.post(
            "/api/v1/planning-requests",
            json={
                "source": "chat",
                "conversationId": self.createConversation(),
                "conditions": {"requiredPlaceIds": ["station_mukho"]},
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        requestId = response.json()["id"]
        self.assertEqual(
            self.client.get(f"/api/v1/planning-requests/{requestId}").json()["conditions"][
                "requiredPlaceIds"
            ],
            ["station_mukho"],
        )
        self.assertEqual(
            self.client.post(f"/api/v1/planning-requests/{requestId}/generations").status_code, 422
        )

    def test_catalog_and_legacy_chat_contract_use_database(self):
        self.signup()
        self.assertEqual(len(self.client.get("/api/v1/places").json()["places"]), 14)
        self.assertEqual(self.client.get("/api/v1/places/station_mukho").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/trains/841").status_code, 200)
        response = self.client.post("/api/v1/chat/messages", json={"message": "묵호역 알려줘"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            set(response.json()),
            {"reply", "intent", "action", "chips", "proposals", "sources", "tool_used", "mode"},
        )

    def test_embedding_updates_only_changed_documents_and_validates_dimension(self):
        with self.app.state.sessionFactory() as session:
            pending = IngestionService.embedDocuments(session, "test-model")
            self.assertEqual(len(pending["pending"]), 20)
            result = IngestionService.embedDocuments(
                session, "test-model", lambda model, texts: [[0.1] * 1536 for text in texts]
            )
            self.assertEqual(result["embedded"], 20)
            self.assertEqual(IngestionService.embedDocuments(session, "test-model")["pending"], [])
            document = session.get(KnowledgeDocumentEntity, "station_mukho")
            document.body += " 변경됨"
            session.commit()
            self.assertEqual(
                IngestionService.embedDocuments(session, "test-model")["pending"], ["station_mukho"]
            )
            with self.assertRaises(ValueError):
                IngestionService.embedDocuments(
                    session, "test-model", lambda model, texts: [[0.1] * 3 for text in texts]
                )
            self.assertEqual(
                session.scalar(select(KnowledgeChunkEntity)).embeddingModel, "test-model"
            )


if __name__ == "__main__":
    unittest.main()
