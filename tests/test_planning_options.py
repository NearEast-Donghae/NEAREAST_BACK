from neareast_api.modules.planner.options import buildPlanningOptions


def testOptionsDoNotTreatCoordinatesAsPublicAccess():
    common = {
        "longitude": 129.1, "latitude": 37.5, "crs": "EPSG:4326",
        "coordinate_ready": True, "default_stay_minutes": 20,
    }
    result = buildPlanningOptions([
        {**common, "id": "station", "name": "역", "routing_ready": True},
        {**common, "id": "freight", "name": "화물역", "routing_ready": False},
        {**common, "id": "missing", "name": "좌표 미확인", "longitude": None},
        {**common, "id": "noStay", "name": "체류 미확인", "routing_ready": True,
         "default_stay_minutes": None},
    ])
    station, freight, missing, noStay = result.places
    assert station.canDepart and station.canVisit
    assert not freight.canVisit and freight.reason == "public_access_unverified"
    assert not missing.canDepart and missing.reason == "coordinates_unverified"
    assert noStay.canDepart and not noStay.canVisit
    assert noStay.reason == "stay_duration_missing"
