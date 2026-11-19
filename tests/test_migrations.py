"""Verify migration scaffolding in temporary folders, without PostgreSQL."""

import argparse
import importlib.util
import io
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


class PlaceCoordinatesMigrationTests(unittest.TestCase):
    """0003 fills coordinates for places that 0002 seeded without them."""

    REVISION = "0003_place_coordinates"

    def setUp(self):
        path = PROJECT_ROOT / "alembic" / "versions" / f"{self.REVISION}.py"
        spec = importlib.util.spec_from_file_location(self.REVISION, path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        # A database as 0002 leaves it: the tables and the reference seed.
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            connection.connection.driver_connection.executescript(
                (PROJECT_ROOT / "alembic" / "seed" / "0002_reference.sql").read_text("utf-8"))
            connection.commit()

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def coordinates(self):
        with self.engine.connect() as connection:
            return {row.id: (row.lat, row.lng)
                    for row in connection.execute(text("SELECT id, lat, lng FROM places"))}

    def test_revision_follows_the_reference_seed(self):
        self.assertEqual(self.migration.revision, self.REVISION)
        self.assertEqual(self.migration.down_revision, "0002_reference_content")
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        self.assertEqual(ScriptDirectory.from_config(config).get_heads(), [self.REVISION])

    def test_seed_has_no_coordinates_and_every_listed_place_exists(self):
        before = self.coordinates()
        self.assertTrue(all(value == (None, None) for value in before.values()))
        self.assertLessEqual(set(self.migration.COORDINATES), set(before))

    def test_upgrade_fills_the_listed_places_and_leaves_the_rest_empty(self):
        self.run_step(self.migration.upgrade)
        after = self.coordinates()
        self.assertEqual(after["station_donghae"], (37.4982, 129.1238))
        self.assertEqual(after["station_mukho"], (37.5461, 129.1082))
        for place_id, value in after.items():
            expected = self.migration.COORDINATES.get(place_id, (None, None))
            self.assertEqual(value, expected, place_id)
        for lat, lng in self.migration.COORDINATES.values():  # inside Donghae city
            self.assertTrue(37.3 < lat < 37.7 and 128.9 < lng < 129.3, (lat, lng))

    def test_downgrade_empties_them_again(self):
        self.run_step(self.migration.upgrade)
        self.run_step(self.migration.downgrade)
        self.assertTrue(all(value == (None, None) for value in self.coordinates().values()))

    def test_postgresql_sql_is_plain_updates(self):
        output = io.StringIO()
        config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        with patch.dict(os.environ, {}, clear=True), patch("sqlalchemy.create_engine") as engine:
            command.upgrade(config, f"0002_reference_content:{self.REVISION}", sql=True)
        engine.assert_not_called()
        sql = output.getvalue()
        self.assertIn("UPDATE places SET lat=37.4982, lng=129.1238 WHERE places.id = 'station_donghae'", sql)
        self.assertIn("UPDATE places SET lat=37.5461, lng=129.1082 WHERE places.id = 'station_mukho'", sql)
        self.assertNotIn("INSERT INTO places", sql)



if __name__ == "__main__":
    unittest.main()
