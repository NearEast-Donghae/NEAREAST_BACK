"""Verify migration scaffolding in temporary folders, without PostgreSQL."""

import argparse
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from neareast_api.core.config import Settings
from neareast_api.db.base import Base
from neareast_api.db.metadata import metadata
from neareast_api.db.url import build_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "alembic" / "versions").mkdir(parents=True)
        shutil.copyfile(PROJECT_ROOT / "alembic.ini", root / "alembic.ini")
        for name in ("env.py", "script.py.mako"):
            shutil.copyfile(PROJECT_ROOT / "alembic" / name, root / "alembic" / name)
        (root / ".env").write_text(
            "NEAREAST_DB_HOST=dotenv-host\nNEAREAST_DB_NAME=dotenv-db\n"
            "NEAREAST_DB_USER=dotenv-user\nNEAREAST_DB_PASSWORD='dotenv-p@ss:%'\n",
            encoding="utf-8",
        )
        self.sql_output = io.StringIO()
        self.config = Config(str(root / "alembic.ini"), output_buffer=self.sql_output)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_shared_metadata_registers_service_tables(self):
        self.assertIs(metadata, Base.metadata)
        self.assertEqual(len(metadata.tables), 13)
        self.assertIn("knowledge_chunks", metadata.tables)
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), [])

    def test_connection_url_preserves_reserved_characters(self):
        settings = Settings(db_user="user@name", db_password="p@ss:/%+#? 한글")
        url = build_database_url(settings)
        decoded = make_url(url.render_as_string(hide_password=False))
        self.assertEqual(decoded.password, settings.db_password.get_secret_value())
        self.assertEqual(decoded.username, settings.db_user)
        self.assertEqual(decoded.drivername, "postgresql+psycopg2")
        self.assertNotIn(settings.db_password.get_secret_value(), str(url))

    def test_template_and_postgresql_sql_generation_without_database(self):
        command.revision(self.config, message="temporary test", rev_id="test_revision")
        with patch("sqlalchemy.create_engine") as create_engine:
            command.upgrade(self.config, "head", sql=True)
        create_engine.assert_not_called()
        self.assertIn("INSERT INTO alembic_version", self.sql_output.getvalue())
        self.assertIn("test_revision", self.sql_output.getvalue())

    def test_environment_overrides_project_dotenv(self):
        with patch.dict(
            os.environ,
            {"NEAREAST_DB_HOST": "environment-host", "NEAREAST_DB_PASSWORD": "environment-pass"},
        ):
            with patch(
                "neareast_api.db.url.build_database_url", wraps=build_database_url
            ) as build_url:
                command.upgrade(self.config, "head", sql=True)
        settings = build_url.call_args.args[0]
        self.assertEqual(settings.db_host, "environment-host")
        self.assertEqual(settings.db_password.get_secret_value(), "environment-pass")
        self.assertEqual(settings.db_name, "dotenv-db")

    def test_cli_autogenerate_refuses_empty_model_registry_before_connecting(self):
        self.config.cmd_opts = argparse.Namespace(autogenerate=True)
        with patch("sqlalchemy.create_engine") as create_engine, patch("neareast_api.db.metadata.metadata") as emptyMetadata:
            emptyMetadata.tables = {}
            with self.assertRaisesRegex(CommandError, "No models are registered"):
                command.revision(self.config, message="must not generate", autogenerate=True)
        create_engine.assert_not_called()
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), [])

    def test_migration_engine_is_disposed_after_connection_failure(self):
        engine = MagicMock()
        engine.connect.side_effect = RuntimeError("test connection failure")
        with patch("sqlalchemy.create_engine", return_value=engine):
            with self.assertRaisesRegex(RuntimeError, "test connection failure"):
                command.current(self.config)
        engine.dispose.assert_called_once()


class MarketAliasesMigrationTests(unittest.TestCase):
    """market_aliases brings an already seeded database up to the seed file's keyword list."""

    def setUp(self):
        path = PROJECT_ROOT / "alembic" / "versions" / "market_aliases.py"
        spec = importlib.util.spec_from_file_location("market_aliases", path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            connection.connection.driver_connection.executescript(
                (PROJECT_ROOT / "alembic" / "seed" / "0002_reference.sql").read_text("utf-8"))
            connection.commit()

    def keywords(self):
        with self.engine.connect() as connection:
            return json.loads(connection.execute(text(
                "SELECT keywords FROM knowledge_documents WHERE id = 'poi_bukpyeong_market'"
            )).scalar())

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def test_seed_and_revision_agree_and_downgrade_restores_the_old_names(self):
        self.assertEqual(self.keywords(), self.migration.AFTER)  # new databases: from the seed
        self.run_step(self.migration.downgrade)
        self.assertEqual(self.keywords(), self.migration.BEFORE)
        self.run_step(self.migration.upgrade)  # databases seeded earlier: from the revision
        self.assertEqual(self.keywords(), self.migration.AFTER)
        for name in ("재래시장", "오일장", "장터", "전통시장"):
            self.assertIn(name, self.keywords())

    def test_postgresql_sql_is_one_update_of_one_document(self):
        output = io.StringIO()
        config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        with patch.dict(os.environ, {}, clear=True), patch("sqlalchemy.create_engine") as engine:
            command.upgrade(config, "0002_reference_content:market_aliases", sql=True)
        engine.assert_not_called()
        sql = output.getvalue()
        self.assertEqual(sql.count("UPDATE knowledge_documents"), 1)
        self.assertIn("재래시장", sql)
        self.assertIn("WHERE id = 'poi_bukpyeong_market'", sql)



if __name__ == "__main__":
    unittest.main()
