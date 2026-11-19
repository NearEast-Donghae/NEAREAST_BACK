import argparse
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError, OpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from neareast_api.core.config import Settings
from neareast_api.db.url import build_database_url
from neareast_api.ingestion.service import IngestionService
from neareast_api.modules.chat.retrieval import EMBEDDING_DIMENSIONS


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed changed PostgreSQL knowledge documents")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = Settings.from_env()
    engine = create_engine(build_database_url(settings))
    usedTokens = []

    def embedBatch(modelName: str, texts: list[str]) -> list[list[float]]:
        key = settings.openai_api_key.get_secret_value()
        if not key:
            raise ValueError("NEAREAST_OPENAI_API_KEY is not configured")
        with OpenAI(api_key=key, timeout=30, max_retries=1) as client:
            try:
                response = client.embeddings.create(
                    model=modelName, input=texts, dimensions=EMBEDDING_DIMENSIONS
                )
            except APIError as exception:
                raise RuntimeError(
                    f"Embedding API failed: {type(exception).__name__}; status={getattr(exception, 'status_code', None)}"
                ) from None
        usedTokens.append(response.usage.total_tokens)
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    try:
        with Session(engine) as session:
            result = IngestionService.embedDocuments(
                session, settings.embedding_model, None if arguments.dry_run else embedBatch
            )
            print(result)
            if result["embedded"]:
                # Enough to check the run and to price it (tokens x the model's input price).
                print(
                    f"embedded {result['embedded']} documents as {EMBEDDING_DIMENSIONS}-dimension "
                    f"vectors with {settings.embedding_model}; {sum(usedTokens)} tokens"
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
