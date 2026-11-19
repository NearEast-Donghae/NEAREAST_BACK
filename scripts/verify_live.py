"""Exercise real PostgreSQL, embeddings and chat; requires explicit execution and spends API tokens."""

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from neareast_api.core.config import Settings
from neareast_api.db.entities import KnowledgeChunkEntity, UserEntity
from neareast_api.db.url import build_database_url
from neareast_api.main import create_app


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = Settings.from_env().model_copy(update={"chat_mock": False})
    if not settings.openai_api_key.get_secret_value():
        raise SystemExit("OpenAI key is not configured")
    engine = create_engine(build_database_url(settings))
    report = {"model": settings.chat_model, "checks": []}
    userId = None
    try:
        with Session(engine) as session:
            report["embeddedDocuments"] = session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkEntity)
                .where(KnowledgeChunkEntity.embedding.is_not(None))
            )
        with TestClient(create_app(settings)) as client:
            response = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": f"live-{uuid4().hex}@example.com",
                    "nickname": "Live verification",
                    "password": uuid4().hex,
                },
            )
            response.raise_for_status()
            userId = response.json()["user"]["id"]
            client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
            conversation = client.post("/api/v1/conversations", json={"title": "Live verification"})
            conversation.raise_for_status()
            conversationId = conversation.json()["id"]
            checks = [
                ("station_history", "묵호역의 역사와 특징을 알려줘", {"focused": "station_mukho"}),
                (
                    "train_lookup",
                    "추암역에서 오후 4시 이후 열차 목록을 알려줘",
                    {"focused": "station_chuam"},
                ),
                ("semantic_search", "바닷가 풍경이 보이고 벽화가 있는 산책 장소를 알려줘", {}),
            ]
            for name, message, context in checks:
                started = time.monotonic()
                request = {"requestId": str(uuid4()), "message": message, "context": context}
                response = client.post(
                    f"/api/v1/conversations/{conversationId}/messages", json=request
                )
                elapsed = round(time.monotonic() - started, 2)
                response.raise_for_status()
                answer = response.json()["response"]
                check = {
                    "name": name,
                    "status": response.status_code,
                    "mode": answer["mode"],
                    "sources": answer["sources"],
                    "tool": answer["tool_used"],
                    "seconds": elapsed,
                    "reply": answer["reply"],
                }
                repeated = client.post(
                    f"/api/v1/conversations/{conversationId}/messages", json=request
                )
                check["idempotent"] = (
                    repeated.status_code == 201 and repeated.json()["id"] == response.json()["id"]
                )
                report["checks"].append(check)
                print(json.dumps(check, ensure_ascii=False), flush=True)
            history = client.get(f"/api/v1/conversations/{conversationId}/messages").json()
            report["savedMessages"] = len(history)
            report["integrationPassed"] = (
                all(
                    check["mode"] == "llm" and check["idempotent"] and check["sources"]
                    for check in report["checks"]
                )
                and len(history) == 6
            )
    finally:
        if userId:
            with Session(engine) as session:
                session.execute(delete(UserEntity).where(UserEntity.id == userId))
                session.commit()
        engine.dispose()
    report["qualityNote"] = (
        "Integration success does not certify answer accuracy; tourism source descriptions are incomplete."
    )
    Path("live-verification.log").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "checks"}, ensure_ascii=False
        )
    )
    return 0 if report.get("integrationPassed") else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
