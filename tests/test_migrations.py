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
from sqlalchemy import create_engine, inspect, text
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
        self.assertEqual(len(metadata.tables), 15)
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


class SavedItemsMigrationTests(unittest.TestCase):
    """The saved_items revision creates exactly the tables the entities describe."""

    TABLES = ("saved_places", "saved_trains")

    def setUp(self):
        path = PROJECT_ROOT / "alembic" / "versions" / "saved_items.py"
        spec = importlib.util.spec_from_file_location("saved_items", path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        # The tables the new ones point at, as the earlier revisions leave them.
        for name in ("users", "places"):
            metadata.tables[name].create(self.engine)

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def test_it_follows_the_reference_seed_in_one_chain(self):
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        self.assertEqual(len(ScriptDirectory.from_config(config).get_heads()), 1)
        self.assertEqual(self.migration.down_revision, "0002_reference_content")

    def test_upgrade_matches_the_entities_and_downgrade_removes_the_tables(self):
        self.run_step(self.migration.upgrade)
        inspector = inspect(self.engine)
        for name in self.TABLES:
            with self.subTest(name):
                model = metadata.tables[name]
                self.assertEqual({c["name"]: c["nullable"] for c in inspector.get_columns(name)},
                                 {c.name: c.nullable for c in model.columns})
                self.assertEqual(
                    sorted(tuple(u["column_names"]) for u in inspector.get_unique_constraints(name)),
                    sorted(tuple(c.name for c in u.columns) for u in model.constraints
                           if u.__class__.__name__ == "UniqueConstraint"))
                self.assertEqual({(tuple(f["constrained_columns"]), f["referred_table"])
                                  for f in inspector.get_foreign_keys(name)},
                                 {((f.parent.name,), f.column.table.name) for f in model.foreign_keys})
        self.run_step(self.migration.downgrade)
        self.assertFalse(set(self.TABLES) & set(inspect(self.engine).get_table_names()))

    def test_postgresql_sql_only_adds_the_two_tables(self):
        output = io.StringIO()
        config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        with patch.dict(os.environ, {}, clear=True), patch("sqlalchemy.create_engine") as engine:
            command.upgrade(config, "0002_reference_content:saved_items", sql=True)
        engine.assert_not_called()
        sql = output.getvalue()
        self.assertEqual(sql.count("CREATE TABLE"), 2)
        self.assertIn("CREATE TABLE saved_places", sql)
        self.assertIn("CREATE TABLE saved_trains", sql)
        self.assertNotIn("ALTER TABLE", sql)



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
            command.upgrade(config, "saved_items:market_aliases", sql=True)
        engine.assert_not_called()
        sql = output.getvalue()
        self.assertEqual(sql.count("UPDATE knowledge_documents"), 1)
        self.assertIn("재래시장", sql)
        self.assertIn("WHERE id = 'poi_bukpyeong_market'", sql)



class KnowledgeEnrichmentMigrationTests(unittest.TestCase):
    """knowledge_enrichment and the seed file hold the same four documents."""

    def setUp(self):
        path = PROJECT_ROOT / "alembic" / "versions" / "knowledge_enrichment.py"
        spec = importlib.util.spec_from_file_location("knowledge_enrichment", path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            connection.connection.driver_connection.executescript(
                (PROJECT_ROOT / "alembic" / "seed" / "0002_reference.sql").read_text("utf-8"))
            connection.commit()

    def documents(self):
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT id, keywords, body, source_url FROM knowledge_documents"))
            return {row.id: (json.loads(row.keywords), row.body,
                             row.source_url.split("\n") if row.source_url else [])
                    for row in rows}

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def test_seed_and_revision_agree_and_downgrade_restores_the_old_text(self):
        untouched = {k: v for k, v in self.documents().items() if k not in self.migration.AFTER}
        for doc_id, expected in self.migration.AFTER.items():
            self.assertEqual(self.documents()[doc_id], tuple(expected), doc_id)  # from the seed
        self.run_step(self.migration.downgrade)
        for doc_id, expected in self.migration.BEFORE.items():
            self.assertEqual(self.documents()[doc_id], tuple(expected), doc_id)
        self.run_step(self.migration.upgrade)  # databases seeded earlier: from the revision
        for doc_id, expected in self.migration.AFTER.items():
            self.assertEqual(self.documents()[doc_id], tuple(expected), doc_id)
        self.assertEqual(untouched, {k: v for k, v in self.documents().items()
                                     if k not in self.migration.AFTER})

    def test_every_document_names_its_sources_and_nothing_unconfirmed(self):
        self.assertEqual(set(self.migration.AFTER), {
            "poi_chotdaebawi", "poi_nongoldam", "poi_mangsang_beach", "poi_dojjebigol"})
        for doc_id, (keywords, body, urls) in self.migration.AFTER.items():
            with self.subTest(doc_id):
                self.assertTrue(urls and all(u.startswith("http") for u in urls))
                self.assertNotIn("http", body)  # sources live in source_url, not in the text
                self.assertEqual(keywords[:len(self.migration.BEFORE[doc_id][0])],
                                 self.migration.BEFORE[doc_id][0])  # old names still work
                for word in ("입장료", "원이에요", "무료"):
                    self.assertNotIn(word, body)
        self.assertNotIn("시부터", self.migration.AFTER["poi_dojjebigol"][1])

    def test_it_follows_market_aliases_in_one_chain(self):
        self.assertEqual(self.migration.down_revision, "market_aliases")
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        self.assertEqual(len(ScriptDirectory.from_config(config).get_heads()), 1)

    def test_postgresql_sql_updates_four_documents(self):
        output = io.StringIO()
        config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        with patch.dict(os.environ, {}, clear=True), patch("sqlalchemy.create_engine") as engine:
            command.upgrade(config, "market_aliases:knowledge_enrichment", sql=True)
        engine.assert_not_called()
        sql = output.getvalue()
        self.assertEqual(sql.count("UPDATE knowledge_documents"), 4)
        self.assertIn("1963년 6월 8일", sql)
        self.assertIn("seantour.kr", sql)



if __name__ == "__main__":
    unittest.main()
