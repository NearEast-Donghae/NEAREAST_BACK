from dataclasses import replace

from fastapi.testclient import TestClient
from storage_support import createStorageApp

from neareast_api.db.entities import SavedTrainEntity, TrainRunEntity
from neareast_api.modules.trains.service import Timetable, canonicalTrainNumber


def testNumberAliasRequiresSameSnapshotAndCurrentNumberTakesPrecedence():
    timetable = Timetable(meta={"target_date": "2026-11-21"}, stations={}, types={}, trains=({
        "no": "1804", "original_no": "1814", "number_correction": {"validDate": "2026-11-21"},
    },))
    assert canonicalTrainNumber(timetable, "01814") == "1804"
    assert canonicalTrainNumber(timetable, "1804") == "1804"
    assert canonicalTrainNumber(replace(timetable, meta={"target_date": "2026-11-22"}), "1814") == "1814"
    assert canonicalTrainNumber(replace(timetable, trains=(*timetable.trains, {"no": "1814"})), "1814") == "1814"


def testExistingSavedTrainStillWorksAndNewNumberDoesNotDuplicateIt():
    app = createStorageApp()
    try:
        with TestClient(app) as client:
            signup = client.post("/api/v1/auth/signup", json={
                "email": "number-alias@example.com", "nickname": "검증자", "password": "Testing-password-123!",
            })
            assert signup.status_code == 201
            client.headers["X-CSRF-Token"] = signup.json()["csrfToken"]
            saved = client.post("/api/v1/saved/trains", json={
                "trainNo": "1814", "stationId": "station_donghae", "action": "ride",
            })
            assert saved.status_code == 201, saved.text
            savedId = saved.json()["id"]
            with app.state.sessionFactory() as session:
                run = session.get(TrainRunEntity, "2026-11-21:1814")
                run.trainNumber = "1804"
                run.details = {**run.details, "no": "1804", "original_no": "1814",
                               "number_correction": {"validDate": "2026-11-21"}}
                session.commit()
            response = client.get("/api/v1/saved").json()["trains"][0]
            assert response["id"] == savedId and response["available"]
            assert response["trainNo"] == "1804"
            repeated = client.post("/api/v1/saved/trains", json={
                "trainNo": "1804", "stationId": "station_donghae", "action": "ride",
            })
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["id"] == savedId
            with app.state.sessionFactory() as session:
                assert session.get(SavedTrainEntity, savedId).trainNo == "1814"
            assert client.get("/api/v1/trains/1814").json()["no"] == "1804"
    finally:
        app.state.databaseEngine.dispose()
