"""Train types: five allowed names, enforced where they are written, kept when the
timetable is collected again, and used when a train is named to the user."""

import importlib.util
import json
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from legacy_support import create_app
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from storage_support import createStorageApp

from neareast_api.core.config import Settings
from neareast_api.db import entities
from neareast_api.db.content_repository import ContentRepository
from neareast_api.db.entities import TrainRunEntity
from neareast_api.db.metadata import metadata
from neareast_api.db.session import CONTENT_SESSION
from neareast_api.ingestion.service import IngestionService
from neareast_api.modules.trains import schemas, service

ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"
ALLOWED = ("KTX-이음", "ITX-마음", "ITX-새마을", "새마을호", "누리로", "무궁화호")
SOURCE = "레일블루 화면에서 수동 확인, 2026-11-20"


def migration():
    path = ROOT / "alembic" / "versions" / "train_types.py"
    spec = importlib.util.spec_from_file_location("train_types", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AllowedValuesTests(unittest.TestCase):
    def test_one_list_everywhere(self):
        self.assertEqual(schemas.TRAIN_TYPES, ALLOWED)
        self.assertEqual(schemas.TrainType.__args__, ALLOWED)
        self.assertEqual(entities.TRAIN_TYPES, ALLOWED)
        self.assertEqual(migration().TRAIN_TYPES, ALLOWED)

    def test_the_database_refuses_any_other_value(self):
        app = create_app(Settings())
        self.addCleanup(app.state.databaseEngine.dispose)
        with app.state.sessionFactory() as session:
            run = session.scalar(select(TrainRunEntity).where(TrainRunEntity.trainNumber == "1861"))
            for name in ALLOWED:  # every allowed name is stored
                run.trainType, run.trainTypeSource = name, SOURCE
                session.commit()
            run.trainType = None  # and none at all is fine
            session.commit()
            for name in ("무궁화", "KTX", "새마을", "ITX-청춘", "", "누리로 "):
                with self.subTest(name):
                    run.trainType = name
                    with self.assertRaises(IntegrityError):
                        session.commit()
                    session.rollback()

    def test_planning_conditions_take_only_the_same_names(self):
        app = createStorageApp()
        self.addCleanup(app.state.databaseEngine.dispose)
        client = TestClient(app)
        signup = client.post(API + "/auth/signup", json={
            "email": "owner@example.com", "nickname": "여행자", "password": "password-for-tests"})
        client.headers["X-CSRF-Token"] = signup.json()["csrfToken"]

        def create(types):
            return client.post(API + "/planning-requests", json={
                "source": "planner", "conditions": {"preferredTrainTypes": types}})
        accepted = create(list(ALLOWED))
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["conditions"]["preferredTrainTypes"], list(ALLOWED))
        self.assertEqual(create([]).status_code, 201)
        for types in (["무궁화"], ["KTX"], ["누리로", "새마을"], ["ktx-이음"]):
            with self.subTest(types=types):
                self.assertEqual(create(types).status_code, 422)


class _Seeded(unittest.TestCase):
    def setUp(self):
        self.app = create_app(Settings())
        self.session = self.app.state.sessionFactory()
        token = CONTENT_SESSION.set(self.session)

        def close():
            CONTENT_SESSION.reset(token)
            self.session.close()
            self.app.state.databaseEngine.dispose()
        self.addCleanup(close)
        self.client = TestClient(self.app)

    def confirm(self, number, name):
        run = self.session.scalar(
            select(TrainRunEntity).where(TrainRunEntity.trainNumber == number))
        run.trainType, run.trainTypeSource = name, SOURCE
        self.session.commit()

    def snapshot(self):
        """The stored timetable in the shape the collector produces."""
        timetable = ContentRepository.timetable()
        return {"meta": timetable.meta, "stations": timetable.stations,
                "trains": [dict(train) for train in timetable.trains]}


class KeptAcrossImportsTests(_Seeded):
    def clear(self, number):
        run = self.session.scalar(
            select(TrainRunEntity).where(TrainRunEntity.trainNumber == number))
        run.trainType = run.trainTypeSource = None
        self.session.commit()

    def collected(self):
        """The timetable as the collector builds it from the records: the 17:20 train to
        부전 comes as 1814 there."""
        snapshot = self.snapshot()
        for train in snapshot["trains"]:
            if train["no"] == "1804":
                train["no"] = "1814"
                train.pop("number_note")
        return snapshot

    def test_collecting_the_timetable_again_keeps_confirmed_types_and_their_source(self):
        self.clear("1864")
        self.confirm("1861", "누리로")  # as the revision leaves it: type with its source
        before = {run.trainNumber: (run.trainType, run.trainTypeSource)
                  for run in self.session.scalars(select(TrainRunEntity))}
        self.assertEqual(sum(1 for kind, _ in before.values() if kind), 62)
        IngestionService.importTimetable(self.session, self.collected())  # same day, again
        self.session.expire_all()
        after = {run.trainNumber: (run.trainType, run.trainTypeSource)
                 for run in self.session.scalars(select(TrainRunEntity))}
        self.assertEqual(after, before)
        self.assertEqual(after["1861"], ("누리로", SOURCE))
        self.assertEqual(after["841"][0], "KTX-이음")
        self.assertEqual(after["1864"], (None, None))  # not confirmed: still empty

    def test_the_renumbered_train_stays_1804_when_collected_again(self):
        IngestionService.importTimetable(self.session, self.collected())
        self.session.expire_all()
        numbers = {run.trainNumber: run for run in self.session.scalars(select(TrainRunEntity))}
        self.assertNotIn("1814", numbers)
        self.assertEqual(numbers["1804"].trainType, "ITX-마음")
        self.assertEqual(numbers["1804"].details["number_note"],
                         "11/21 레일블루 기준, 3주 기록은 1814")
        self.assertEqual(self.client.get(API + "/trains/1804").json()["destination"], "부전")
        self.assertEqual(self.client.get(API + "/trains/1814").status_code, 404)

    def test_a_record_that_already_has_the_new_number_is_not_renamed_onto_it(self):
        snapshot = self.collected()
        twin = dict(next(t for t in snapshot["trains"] if t["no"] == "1814"), no="1804")
        snapshot["trains"].append(twin)
        IngestionService.importTimetable(self.session, snapshot)
        self.session.expire_all()
        numbers = {run.trainNumber for run in self.session.scalars(select(TrainRunEntity))}
        self.assertTrue({"1804", "1814"} <= numbers)

    def test_a_snapshot_for_another_day_gets_the_types_too(self):
        snapshot = self.snapshot()
        snapshot["meta"] = {**snapshot["meta"], "target_date": "2026-11-28"}
        IngestionService.importTimetable(self.session, snapshot)
        self.session.expire_all()
        newer = self.session.get(TrainRunEntity, "2026-11-28:1861")
        self.assertEqual(newer.trainType, "누리로")
        self.assertEqual(len(ContentRepository.timetable().types), 63)  # the latest snapshot


class ShownToTheUserTests(_Seeded):
    def setUp(self):
        super().setUp()
        run = self.session.scalar(
            select(TrainRunEntity).where(TrainRunEntity.trainNumber == "1864"))
        run.trainType = run.trainTypeSource = None  # one train without a confirmed type
        self.session.commit()

    def test_api_shows_the_type_or_null(self):
        self.assertEqual(self.client.get(API + "/trains/1861").json()["type"], "누리로")
        self.assertIsNone(self.client.get(API + "/trains/1864").json()["type"])
        at_donghae = {t["no"]: t["type"] for t in
                      self.client.get(API + "/stations/station_donghae/trains").json()["trains"]}
        self.assertEqual((at_donghae["1861"], at_donghae["1864"]), ("누리로", None))
        self.assertEqual((at_donghae["2502"], at_donghae["1641"]), ("새마을호", "ITX-마음"))
        self.assertEqual(sum(1 for kind in at_donghae.values() if kind), 62)

    def test_a_train_is_named_by_its_type_or_else_by_number_and_line(self):
        timetable = ContentRepository.timetable()
        self.assertTrue(service.describe(service.detail(timetable, "1861")).startswith("누리로 1861 "))
        self.assertTrue(service.describe(service.detail(timetable, "1864")).startswith("1864(동해선) "))

    def test_nearie_says_the_type_when_known_and_number_with_line_when_not(self):
        response = self.client.post(API + "/chat/messages", json={
            "message": "추암역 열차 목록", "context": {"date": "2026-11-21", "focused": "station_chuam"}})
        self.assertEqual(response.status_code, 200, response.text)
        reply = response.json()["reply"]
        self.assertIn("누리로 1861 강릉행", reply)
        self.assertNotIn("동해선 1861", reply)
        # No confirmed type: the line stands in its place, as before.
        self.assertIn("동해선 1864 동대구행", reply)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.migration = migration()
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        # train_services as the earlier revisions leave it: no source column, no rule.
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE train_services (id VARCHAR(64) PRIMARY KEY, snapshot_id VARCHAR(10) "
                "NOT NULL, train_number VARCHAR(20) NOT NULL, train_type VARCHAR(40), "
                "details JSON NOT NULL, UNIQUE (snapshot_id, train_number))"))
            connection.execute(text(
                "CREATE TABLE train_stops (id VARCHAR(80) PRIMARY KEY, service_id VARCHAR(64) "
                "NOT NULL REFERENCES train_services (id) ON DELETE CASCADE, position INTEGER "
                "NOT NULL, place_id VARCHAR(64), details JSON NOT NULL)"))
            for number, name in (("1861", None), ("1771", "무궁화"), ("841", "KTX-이음"),
                                 ("1814", None)):
                connection.execute(text(
                    "INSERT INTO train_services VALUES (:id, '2026-11-21', :no, :type, :details)"),
                    {"id": f"2026-11-21:{number}", "no": number, "type": name,
                     "details": json.dumps({"no": number, "line": "동해선"}, ensure_ascii=False)})
            for position in (0, 1):
                connection.execute(text(
                    "INSERT INTO train_stops VALUES (:id, '2026-11-21:1814', :position, NULL, '{}')"),
                    {"id": f"2026-11-21:1814:{position}", "position": position})

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def types(self):
        with self.engine.connect() as connection:
            return {row.train_number: (row.train_type, row.train_type_source) for row in
                    connection.execute(text("SELECT * FROM train_services"))}

    def test_it_is_the_head_after_knowledge_enrichment(self):
        self.assertEqual(self.migration.down_revision, "knowledge_enrichment")
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        self.assertEqual(ScriptDirectory.from_config(config).get_heads(), ["train_types"])

    def test_upgrade_adds_the_rule_and_the_source_and_matches_the_entity(self):
        self.run_step(self.migration.upgrade)
        columns = {c["name"] for c in inspect(self.engine).get_columns("train_services")}
        self.assertEqual(columns, {c.name for c in metadata.tables["train_services"].columns})
        self.assertEqual(self.types()["1771"][0], "무궁화호")  # a known other spelling is fixed
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE train_services SET train_type = 'KTX' WHERE train_number = '1861'"))

    def test_only_confirmed_trains_are_written_and_every_value_is_allowed(self):
        self.assertTrue(set(self.migration.CONFIRMED.values()) <= set(ALLOWED))
        self.assertEqual(self.migration.SOURCE, SOURCE)
        self.assertEqual(len(self.migration.CONFIRMED), 63)
        self.migration.CONFIRMED = {"1861": "누리로"}
        self.run_step(self.migration.upgrade)
        self.assertEqual(self.types(), {"1861": ("누리로", SOURCE), "1771": ("무궁화호", None),
                                        "841": ("KTX-이음", None), "1804": (None, None)})

    def test_1814_becomes_1804_with_its_stops_and_a_note_and_back(self):
        self.run_step(self.migration.upgrade)
        with self.engine.connect() as connection:
            services = connection.execute(text(
                "SELECT id, train_number, details FROM train_services "
                "WHERE train_number IN ('1804', '1814')")).all()
            stops = connection.execute(text(
                "SELECT id, service_id FROM train_stops ORDER BY position")).all()
        (service_id, number, details), = services
        self.assertEqual((service_id, number), ("2026-11-21:1804", "1804"))
        details = json.loads(details)
        self.assertEqual((details["no"], details["line"]), ("1804", "동해선"))
        self.assertEqual(details["number_note"], "11/21 레일블루 기준, 3주 기록은 1814")
        self.assertEqual([tuple(row) for row in stops],
                         [("2026-11-21:1804:0", "2026-11-21:1804"),
                          ("2026-11-21:1804:1", "2026-11-21:1804")])
        self.run_step(lambda: self.migration.renumber(self.migration.RENUMBERED))
        with self.engine.connect() as connection:  # running it again changes nothing
            self.assertEqual(connection.execute(text(
                "SELECT count(*) FROM train_services WHERE train_number = '1804'")).scalar(), 1)
        self.run_step(self.migration.downgrade)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text(
                "SELECT train_number FROM train_services WHERE train_number IN ('1804', '1814')"
            )).scalars().all(), ["1814"])
            self.assertEqual(connection.execute(text(
                "SELECT DISTINCT service_id FROM train_stops")).scalars().all(),
                ["2026-11-21:1814"])

    def test_an_unknown_value_stops_the_upgrade_with_its_name(self):
        with self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE train_services SET train_type = '통근열차' WHERE train_number = '841'"))
        with self.assertRaisesRegex(RuntimeError, "통근열차"):
            self.run_step(self.migration.upgrade)

    def test_downgrade_removes_what_it_wrote_and_keeps_what_was_there(self):
        self.migration.CONFIRMED = {"1861": "누리로"}
        self.run_step(self.migration.upgrade)
        self.run_step(self.migration.downgrade)
        columns = {c["name"] for c in inspect(self.engine).get_columns("train_services")}
        self.assertNotIn("train_type_source", columns)
        with self.engine.connect() as connection:
            rows = dict(connection.execute(text(
                "SELECT train_number, train_type FROM train_services")).all())
        self.assertEqual(rows, {"1861": None, "1771": "무궁화호", "841": "KTX-이음", "1814": None})

    def test_seed_holds_exactly_the_confirmed_types(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        metadata.create_all(engine)
        with engine.connect() as connection:
            connection.connection.driver_connection.executescript(
                (ROOT / "alembic" / "seed" / "0002_reference.sql").read_text("utf-8"))
            connection.commit()
            rows = connection.execute(text(
                "SELECT train_number, train_type, train_type_source FROM train_services")).all()
        self.assertEqual(len(rows), 63)
        seeded = {row.train_number: row.train_type for row in rows if row.train_type}
        self.assertEqual(seeded, self.migration.CONFIRMED)

    def test_the_seed_runs_on_the_schema_of_the_revision_that_loads_it(self):
        """The seed is executed by revision 0002. It must not use anything a later revision
        adds (found on a fresh PostgreSQL: a column added here broke `alembic upgrade head`)."""
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        path = ROOT / "alembic" / "versions" / "0001_service_storage.py"
        spec = importlib.util.spec_from_file_location("first", path)
        first = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(first)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                first.upgrade()
        with engine.connect() as connection:
            connection.connection.driver_connection.executescript(
                (ROOT / "alembic" / "seed" / "0002_reference.sql").read_text("utf-8"))
            connection.commit()
            self.assertEqual(connection.execute(text(
                "SELECT count(*) FROM train_services")).scalar(), 63)

    def test_upgrade_writes_the_source_for_every_seeded_type(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        metadata.create_all(engine)
        with engine.connect() as connection:
            connection.connection.driver_connection.executescript(
                (ROOT / "alembic" / "seed" / "0002_reference.sql").read_text("utf-8"))
            connection.execute(text("UPDATE train_services SET train_type_source = NULL"))
            connection.commit()
        with engine.begin() as connection:  # the data part of the revision
            with Operations.context(MigrationContext.configure(connection)):
                for number, name in self.migration.CONFIRMED.items():
                    connection.execute(text(
                        "UPDATE train_services SET train_type = :name, train_type_source = :source "
                        "WHERE train_number = :number"),
                        {"name": name, "source": SOURCE, "number": number})
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text(
                "SELECT count(*) FROM train_services WHERE train_type_source = :s"),
                {"s": SOURCE}).scalar(), 63)


if __name__ == "__main__":
    unittest.main()
