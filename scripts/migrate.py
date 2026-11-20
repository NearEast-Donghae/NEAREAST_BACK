import hashlib
from pathlib import Path

from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.dialects.postgresql import insert

from alembic import command
from neareast_api.core.config import Settings
from neareast_api.db.metadata import metadata
from neareast_api.db.seed import encodePayload
from neareast_api.db.url import build_database_url

OLDER_REVISIONS = {"0001_service_storage", "0002_reference_content", "saved_items", "market_aliases"}
ENRICHED_DOCUMENTS = {"poi_chotdaebawi", "poi_nongoldam", "poi_mangsang_beach", "poi_dojjebigol"}


def preserveKnowledge(connection):
    if not inspect(connection).has_table("alembic_version"):
        return
    revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if not revisions & OLDER_REVISIONS or not inspect(connection).has_table("knowledge_documents"):
        return
    documents = metadata.tables["knowledge_documents"]
    for record in connection.execute(select(documents).where(documents.c.id.in_(ENRICHED_DOCUMENTS))).mappings():
        archived = dict(record)
        archived["id"] = "seed-preserved:" + hashlib.sha256(encodePayload(archived)).hexdigest()[:20]
        connection.execute(insert(documents).values(**archived).on_conflict_do_nothing())


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    engine = create_engine(build_database_url(Settings.from_env()))
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(7086401)"))
            try:
                preserveKnowledge(connection)
                connection.commit()
                config = Config(str(root / "alembic.ini"))
                config.set_main_option("script_location", str(root / "alembic"))
                command.upgrade(config, "head")
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(7086401)"))
                connection.commit()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
