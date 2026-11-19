"""Executable bootstrap contracts; business routes are registered explicitly."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import ValidationError

from neareast_api.api.router import build_api_router
from neareast_api.core.config import Settings
from neareast_api.main import create_app


class BootstrapTests(unittest.TestCase):
    def test_app_starts_without_external_services_with_explicit_routes(self):
        # Every public path is listed here on purpose: adding a route must be deliberate.
        with TestClient(create_app(Settings(docs_enabled=True))) as client:
            response = client.get("/openapi.json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                set(response.json()["paths"]),
                {
                    "/health/live",
                    "/health/ready",
                    "/api/v1/chat/messages",
                    "/api/v1/chat/persona",
                },
            )
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/").status_code, 404)

    def test_cors_allows_only_configured_origins(self):
        front = "http://localhost:5173"
        env = {"NEAREAST_CORS_ALLOW_ORIGINS": f"{front}, https://neareast.example"}
        with patch.dict(os.environ, env):
            app = create_app()
        preflight = {"Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "content-type"}
        with TestClient(app) as client:
            allowed = client.options("/api/v1/chat/messages", headers={"Origin": front, **preflight})
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.headers["access-control-allow-origin"], front)
            got = client.get("/api/v1/chat/persona", headers={"Origin": front})
            self.assertEqual(got.headers["access-control-allow-origin"], front)
            other = client.options("/api/v1/chat/messages",
                                   headers={"Origin": "https://evil.example", **preflight})
            self.assertNotIn("access-control-allow-origin", other.headers)

    def test_cors_is_off_by_default(self):
        with TestClient(create_app(Settings())) as client:
            response = client.get("/health/live", headers={"Origin": "http://localhost:5173"})
            self.assertNotIn("access-control-allow-origin", response.headers)

    def test_every_setting_reaches_compose_and_env_example(self):
        # A setting that Compose does not pass is silently ignored in the container.
        root = Path(__file__).resolve().parents[1]
        compose = (root / "compose.yaml").read_text("utf-8")
        example = (root / ".env.example").read_text("utf-8")
        not_in_compose = {"content_dir", "chat_reasoning_effort"}  # explained in compose.yaml
        for name in Settings.model_fields:
            env = f"NEAREAST_{name.upper()}"
            with self.subTest(env):
                self.assertIn(env, example)
                if name in not_in_compose:
                    self.assertNotIn(f"{env}:", compose)
                else:
                    self.assertIn(f"{env}:", compose)

    def test_docs_can_be_disabled_from_environment(self):
        with patch.dict(os.environ, {"NEAREAST_DOCS_ENABLED": "false"}):
            app = create_app()
        with TestClient(app) as client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)

    def test_invalid_setting_fails_at_startup(self):
        with patch.dict(os.environ, {"NEAREAST_DOCS_ENABLED": "not-a-bool"}):
            with self.assertRaises(ValidationError):
                create_app()

    def test_settings_are_not_cached_across_apps(self):
        with patch.dict(os.environ, {"NEAREAST_DOCS_ENABLED": "true"}):
            first = create_app()
        with patch.dict(os.environ, {"NEAREAST_DOCS_ENABLED": "false"}):
            second = create_app()
        with TestClient(first) as a, TestClient(second) as b:
            self.assertEqual(a.get("/openapi.json").status_code, 200)
            self.assertEqual(b.get("/openapi.json").status_code, 404)

    def test_feature_router_composes_without_leaking_to_another_app(self):
        feature = APIRouter(prefix="/_test")

        @feature.get("/probe")
        def probe():
            return {"ok": True}

        combined = build_api_router()
        combined.include_router(feature)
        with patch("neareast_api.main.build_api_router", return_value=combined):
            composed = create_app(Settings())
        untouched = create_app(Settings())

        with TestClient(composed) as a, TestClient(untouched) as b:
            self.assertEqual(a.get("/api/v1/_test/probe").json(), {"ok": True})
            self.assertEqual(a.post("/api/v1/_test/probe").status_code, 405)
            self.assertEqual(b.get("/api/v1/_test/probe").status_code, 404)
            paths = a.get("/openapi.json").json()["paths"]
            self.assertIn("get", paths["/api/v1/_test/probe"])


if __name__ == "__main__":
    unittest.main()
