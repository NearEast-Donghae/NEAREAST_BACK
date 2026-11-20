"""A seed names the schema revision it was exported from. A database at that revision
or at a later one takes it; anything else is refused before a row is written."""

import tempfile
import textwrap
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text

from neareast_api.db.metadata import metadata
from neareast_api.db.seed import exportSeed, importSeed, schemaReaches

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED = "0006_train_type_contract"


class SchemaReachesTests(unittest.TestCase):
    """On the project's own migration scripts."""

    SCRIPTS = PROJECT_ROOT / "alembic"

    def test_the_exact_revision_is_accepted_even_without_scripts(self):
        self.assertTrue(schemaReaches({REQUIRED}, REQUIRED))

    def test_an_earlier_revision_has_not_reached_the_seed(self):
        self.assertFalse(schemaReaches({"0005_reference_records"}, REQUIRED, self.SCRIPTS))

    def test_an_unknown_revision_is_refused(self):
        self.assertFalse(schemaReaches({"somebody_elses_revision"}, REQUIRED, self.SCRIPTS))
        self.assertFalse(schemaReaches(set(), REQUIRED, self.SCRIPTS))


class ImportRevisionTests(unittest.TestCase):
    """With a small script directory: start <- made_for <- later, and a side branch."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.scripts = root / "alembic"
        (self.scripts / "versions").mkdir(parents=True)
        for revision, down in (("start", None), ("made_for", "start"),
                               ("later", "made_for"), ("side", "start")):
            (self.scripts / "versions" / f"{revision}.py").write_text(textwrap.dedent(f"""
                revision = {revision!r}
                down_revision = {down!r}
                branch_labels = None
                depends_on = None
                def upgrade(): pass
                def downgrade(): pass
            """), "utf-8")
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        self.seed = root / "seed.gz"
        with self.engine.begin() as connection:
            connection.execute(metadata.tables["places"].insert().values(
                id="station_test", name="시험역", kind="station", aliases=[], operating_info={}))
            exportSeed(connection, self.seed, releaseMetadata={"requiredRevision": "made_for"})
            connection.execute(metadata.tables["places"].delete())

    def at(self, *revisions):
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
            if revisions:
                connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
                for revision in revisions:
                    connection.execute(text("INSERT INTO alembic_version VALUES (:r)"),
                                       {"r": revision})

    def load(self, scripts=True):
        with self.engine.begin() as connection:
            return importSeed(connection, self.seed, False, self.scripts if scripts else None)

    def places(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM places")).scalar()

    def test_the_revision_the_seed_was_made_for(self):
        self.at("made_for")
        self.assertEqual(self.load()["counts"]["places"], 1)
        self.assertEqual(self.places(), 1)

    def test_a_revision_that_follows_it(self):
        self.at("later")
        self.load()
        self.assertEqual(self.places(), 1)

    def test_a_revision_before_it_is_refused(self):
        self.at("start")
        with self.assertRaisesRegex(ValueError, "made_for or a later one .*database is at start"):
            self.load()
        self.assertEqual(self.places(), 0)

    def test_a_side_branch_is_refused(self):
        self.at("side")
        with self.assertRaisesRegex(ValueError, "requires schema revision"):
            self.load()
        self.assertEqual(self.places(), 0)

    def test_two_heads_are_accepted_when_one_of_them_follows_it(self):
        self.at("side", "later")
        self.load()
        self.assertEqual(self.places(), 1)

    def test_without_the_scripts_only_the_exact_revision_is_accepted(self):
        self.at("later")
        with self.assertRaisesRegex(ValueError, "requires schema revision"):
            self.load(scripts=False)
        self.at("made_for")
        self.load(scripts=False)
        self.assertEqual(self.places(), 1)

    def test_a_database_that_was_never_migrated_is_refused(self):
        self.at()
        with self.assertRaisesRegex(ValueError, "Run database migrations"):
            self.load()


if __name__ == "__main__":
    unittest.main()
