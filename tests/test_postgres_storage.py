"""Opt-in PostgreSQL integration checks, each isolated from public user data."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import test_service_storage
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from neareast_api.core.config import Settings
from neareast_api.db.entities import KnowledgeChunkEntity
from neareast_api.db.metadata import metadata
from neareast_api.db.url import build_database_url
from neareast_api.main import create_app
from neareast_api.modules.chat import service as chatService


@unittest.skipUnless(
    os.environ.get("NEAREAST_RUN_POSTGRES_TESTS") == "1", "Opt-in PostgreSQL checks"
)
class PostgresStorageTests(test_service_storage.ServiceStorageTests):
    def setUp(self):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        settings = Settings.from_env().model_copy(
            update={"chat_mock": True, "llm_usage_log": os.devnull}
        )
        schemaName = "test_neareast_" + uuid4().hex
        adminEngine = create_engine(build_database_url(settings))
        with adminEngine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schemaName}"'))
        engine = create_engine(
            build_database_url(settings),
            connect_args={"options": f"-csearch_path={schemaName},public"},
        )

        def cleanupSchema():
            engine.dispose()
            try:
                with adminEngine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schemaName}" CASCADE'))
            finally:
                adminEngine.dispose()

        self.addCleanup(cleanupSchema)
        metadata.create_all(engine, checkfirst=False)
        seed = (Path(__file__).resolve().parents[1] / "alembic/seed/0002_reference.sql").read_text(
            "utf-8"
        )
        with engine.begin() as connection:
            connection.execute(text(seed.replace(":", r"\:")))
        self.app = create_app(settings)
        self.app.state.sessionFactory = sessionmaker(engine, expire_on_commit=False)
        self.app.state.databaseEngine = engine
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_simultaneous_chat_requests_do_not_duplicate_answers(self):
        self.signup()
        conversationId = self.createConversation()
        path = f"/api/v1/conversations/{conversationId}/messages"
        request = {"requestId": str(uuid4()), "message": "묵호역 알려줘"}
        entered, release = Event(), Event()
        originalAnswer = chatService.answer

        def delayedAnswer(*arguments):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Test coordination timed out")
            return originalAnswer(*arguments)

        with TestClient(self.app) as concurrentClient:
            concurrentClient.cookies.update(self.client.cookies)
            concurrentClient.headers.update(self.client.headers)
            with (
                patch.object(chatService, "answer", side_effect=delayedAnswer),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                firstRequest = executor.submit(self.client.post, path, json=request)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(concurrentClient.post(path, json=request).status_code, 409)
                finally:
                    release.set()
                first = firstRequest.result(timeout=10)
            self.assertEqual(first.status_code, 201, first.text)
            repeated = concurrentClient.post(path, json=request)
            self.assertEqual(repeated.status_code, 201, repeated.text)
            self.assertEqual(first.json()["id"], repeated.json()["id"])
            self.assertEqual(len(concurrentClient.get(path).json()), 2)

    def test_pgvector_round_trip_and_cosine_distance(self):
        with self.app.state.sessionFactory() as session:
            chunk = session.scalar(select(KnowledgeChunkEntity).where(
                KnowledgeChunkEntity.documentId == "station_mukho",
                KnowledgeChunkEntity.position == 0,
            ))
            chunk.embedding = [1.0] + [0.0] * 1535
            session.commit()
            session.refresh(chunk)
            self.assertEqual(len(chunk.embedding), 1536)
            distance = session.scalar(
                select(KnowledgeChunkEntity.embedding.cosine_distance([1.0] + [0.0] * 1535))
                .where(KnowledgeChunkEntity.id == chunk.id)
            )
            self.assertAlmostEqual(distance, 0.0)


if __name__ == "__main__":
    unittest.main()
