import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

from neareast_api.core.config import Settings
from neareast_api.db.seed import exportSeed, importSeed
from neareast_api.db.url import build_database_url


def main():
    parser = argparse.ArgumentParser(description="Share public data without users, chats or secrets")
    parser.add_argument("operation", choices=("export", "import"), nargs="?", default="import")
    parser.add_argument("path", type=Path, nargs="?",
                        default=Path(__file__).resolve().parents[1] / "neareast.seed.gz")
    parser.add_argument("--dry-run", dest="dryRun", action="store_true")
    parser.add_argument("--without-embeddings", dest="withoutEmbeddings", action="store_true")
    arguments = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    engine = create_engine(build_database_url(Settings.from_env()))
    try:
        with engine.begin() as connection:
            if arguments.operation == "export":
                report = exportSeed(connection, arguments.path, not arguments.withoutEmbeddings)
            else:
                report = importSeed(connection, arguments.path, arguments.dryRun,
                                    Path(__file__).resolve().parents[1] / "alembic")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
