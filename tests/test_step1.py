import csv
import json

import httpx
import pytest

from backend.app.services.public_data import DataApiError, PublicData, parsePage, readPage
from scripts.build_dataset import build
from scripts.fetch_tourism import fetch as fetchTourism
from scripts.fetch_transport import fetch as fetchTransport
from scripts.import_csv import collectCsv
from scripts.normalize_data import coordinatePair, trainRecords
from scripts.validate_data import TABLES, validate


def writeCsv(path, rows, encoding="utf-8-sig"):
    with path.open("w", encoding=encoding, newline="") as stream:
        csv.writer(stream).writerows(rows)


def page(items, total=None, size=None):
    return {"response": {"header": {"resultCode": "00"}, "body": {
        "items": items, "totalCount": total, "numOfRows": size,
    }}}


def test_csv_encodings_scope_and_website_directory(tmp_path):
    writeCsv(tmp_path / "철도역 정보_20250711.csv", [
        ["역이름", "주소", "위도좌표", "경도좌표"],
        ["가상역", "강원도 동해시 테스트길 1", "37.5", "129.1"],
        ["가상역", "강원도 삼척시 테스트길 1", "37.5", "129.1"],
    ], encoding="cp949")
    writeCsv(tmp_path / "동해시_패밀리사이트.csv", [["명칭", "주소", "분류"], ["가상", "https://example.com", "기관"]])
    records, inventory = collectCsv(tmp_path)
    assert len(records) == 1
    railFile = next(item for item in inventory if item["dataset"] == "kr_stations")
    assert railFile["encoding"] == "cp949"
    assert railFile["out_of_scope"] == 1
    assert any(item["status"] == "website_directory" and item["encoding"] == "utf-8-sig"
               for item in inventory)


@pytest.mark.parametrize("lat,lng", [(129.1, 37.5), (None, 129.1), ("NaN", 129.1), (37.5, 0)])
def test_invalid_coordinates(lat, lng):
    assert coordinatePair(lat, lng) == (None, None, "invalid_coordinates")


def test_parse_page_collection_variants():
    assert parsePage(httpx.Response(200, json=page({"item": {"id": "1"}}, 1))) == ([{"id": "1"}], 1, None)
    assert parsePage(httpx.Response(200, json=page("", 0))) == ([], 0, None)
    with pytest.raises(DataApiError, match="오류 30"):
        parsePage(httpx.Response(200, text="<OpenAPI_ServiceResponse><cmmMsgHeader>"
                  "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"))


def test_key_missing_never_requests(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.app.services.public_data.ROOT", tmp_path)
    monkeypatch.delenv("TAGO_SERVICE_KEY", raising=False)
    monkeypatch.delenv("DATA_GO_KR_API_KEY", raising=False)
    with pytest.raises(DataApiError, match="TAGO_SERVICE_KEY"):
        PublicData("tago", None, ledger=tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()


@pytest.mark.parametrize("service,keyVar,urlVar,url,formatVar", [
    ("tago", "TAGO_SERVICE_KEY", "TAGO_API_URL",
     "https://apis.data.go.kr/1613000/TrainInfo", "_type"),
    ("tourapi", "TOURAPI_SERVICE_KEY", "TOUR_API_URL",
     "https://apis.data.go.kr/B551011/KorService2", "_type"),
    ("donghae", "DONGHAE_SERVICE_KEY", "DONGHAE_API_URL",
     "https://apis.data.go.kr/4210000/DonghaeTrafficFacilityInfoService", "resultType"),
])
def test_shared_key_and_service_override(monkeypatch, tmp_path, caplog, service, keyVar,
                                         urlVar, url, formatVar):
    monkeypatch.setattr("backend.app.services.public_data.ROOT", tmp_path)
    for name in ("DATA_GO_KR_API_KEY", keyVar, urlVar):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    common = f'DATA_GO_KR_API_KEY="fixture%2B%2F"\n{urlVar}={url}/\n'
    env.write_text(common, encoding="utf-8-sig")
    caplog.set_level("INFO", logger="httpx")
    keys = []

    def respond(request):
        keys.append(request.url.params["serviceKey"])
        assert request.url.path == httpx.URL(url).path + "/example"
        assert request.url.params[formatVar] == "json"
        return httpx.Response(200, json=page("", 0))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        ledger = tmp_path / "quota.sqlite3"
        assert PublicData(service, http, ledger=ledger).rows("example", {}) == []
        monkeypatch.setenv("DATA_GO_KR_API_KEY", "process-fixture")
        env.write_text(common + f"{keyVar}=service-fixture # optional override\n", encoding="utf-8")
        assert PublicData(service, http, ledger=ledger).rows("example", {}) == []
        monkeypatch.setenv(urlVar, "https://example.invalid/")
        with pytest.raises(DataApiError, match=urlVar):
            PublicData(service, http, ledger=ledger)
    assert keys == ["fixture+/", "service-fixture"]
    assert not caplog.records


def test_xml_success_and_http_error_code():
    response = httpx.Response(200, text="<response><header><resultCode>00</resultCode></header>"
                              "<body><items><item><citycode>32</citycode></item></items>"
                              "<totalCount>1</totalCount><numOfRows>1</numOfRows></body></response>")
    parsed = readPage(response)
    assert parsed.responseFormat == "xml"
    assert parsed.rows == [{"citycode": "32"}]
    assert parsed.httpStatus == 200 and parsed.apiCode == "00"

    errorBody = {"response": {"header": {"resultCode": "30", "resultMsg": "fixture secret echo"}}}
    with pytest.raises(DataApiError) as captured:
        readPage(httpx.Response(403, json=errorBody))
    assert captured.value.httpStatus == 403 and captured.value.apiCode == "30"
    assert captured.value.responseFormat == "json"
    assert captured.value.reason == "unregistered API key"
    assert "fixture secret echo" not in str(captured.value)


def test_api_pagination_budget_and_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("TAGO_SERVICE_KEY", "fake%2B%2F")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.params["serviceKey"] == "fake+/"
        current = request.url.params["pageNo"]
        return httpx.Response(200, json=page({"item": [{"nodeid": current}]}, 2, 1))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = PublicData("tago", http, maxCalls=2, ledger=tmp_path / "quota.sqlite3")
        assert client.rows("GetCtyAcctoTrainSttnList", {"cityCode": 1}) == [{"nodeid": "1"}, {"nodeid": "2"}]
        with pytest.raises(DataApiError, match="호출 한도"):
            client.rows("GetCtyAcctoTrainSttnList", {})
    assert len(requests) == 2


def test_donghae_grid_and_tourism_use_distinct_schemas(monkeypatch, tmp_path):
    monkeypatch.setenv("DONGHAE_SERVICE_KEY", "mock")
    monkeypatch.setenv("TOURAPI_SERVICE_KEY", "mock")

    def respond(request):
        params = request.url.params
        if request.url.path.endswith("getDonghaeTrafficFacilityInfoList"):
            assert params["resultType"] == "json"
            assert params["DONG_NM"] == "삼화동"
            return httpx.Response(200, json=page({"item": [{"GRID_ID": "1"}]}, 1))
        assert request.url.path.endswith("areaBasedList2")
        assert params["lDongRegnCd"] == "51" and params["lDongSignguCd"] == "170"
        return httpx.Response(200, json=page({"item": [{
            "title": "가상 관광지", "contentid": 1, "firstimage": "https://example.com/image.png",
        }]}, 1))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        ledger = tmp_path / "quota.sqlite3"
        grid = fetchTransport(PublicData("donghae", http, ledger=ledger), {"DONG_NM": "삼화동"})
        touristData = fetchTourism(PublicData("tourapi", http, ledger=ledger))
    assert grid["items"] == [{"GRID_ID": "1"}]
    assert all("firstimage" not in row["items"][0] for row in touristData)


def test_merge_coordinate_quarantine_and_output(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()
    writeCsv(raw / "동해시_관광명소현황.csv", [
        ["관광명소명", "도로명주소", "위도", "경도"],
        ["가상 명소", "강원도 동해시 테스트길 10", "37.51", "129.11"],
        ["가상명소", "강원특별자치도 동해시 테스트길 10", "37.51", "129.11"],
        ["동명이름", "강원특별자치도 동해시 테스트길 50", "37.52", "129.12"],
    ])
    writeCsv(reference / "coordinate_exclusions.csv", [
        ["dataset", "name", "latitude", "longitude", "reason", "source_url", "checked_at"],
        ["tourism", "동명이름", "37.52", "129.12", "가상 검증", "https://example.com", "2026-11-19"],
    ])
    processed, reports = tmp_path / "processed", tmp_path / "reports"
    result = build(raw, processed, reports, reference)
    assert result["counts"]["locations"] == 2
    assert result["merged_records"] == 1
    assert result["coordinate_ready_count"] == 1
    assert result["routing_ready_count"] == 0
    assert result["issues"][0]["code"] == "quarantined_coordinates"
    assert (reports / "data_quality_report.md").is_file()
    tables = {name: json.loads((processed / f"{name}.json").read_text(encoding="utf-8"))
              for name in TABLES}
    assert not validate(tables)
    for location in tables["locations"]:
        assert {source["source_ref"] for source in location["sources"]} >= set(location["field_sources"]["name"])
    assert tables["trains"] == []
    with (processed / "locations.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 2
    tables["locations"][0]["coordinate_ready"] = True
    if tables["locations"][0]["latitude"] is not None:
        tables["locations"][0]["latitude"] = 0
    assert validate(tables)


def test_train_keeps_observed_stop_times_only():
    snapshot = {
        "parameters": {"depPlandTime": "20261119", "depPlaceId": "NAT1", "arrPlaceId": "NAT2"},
        "items": [{"depplacename": "가상", "arrplacename": "가상도착",
                   "depplandtime": 20261119235000, "arrplandtime": 20261120002200,
                   "trainNo": 100, "traingradename": "가상열차"}],
        "source_url": "https://example.com", "fetched_at": "2026-11-19T12:00:00+09:00",
        "endpoint": "GetStrtpntAlocFndTrainInfo",
    }
    records, issues = trainRecords([snapshot, snapshot], [{"name": "가상역", "id": "place1"}],
                                    {"NAT1": "가상"})
    assert not issues
    train, = records
    assert train["train_number"] == "100"
    assert train["departure"]["location_id"] == "place1"
    assert train["arrival"]["location_id"] is None
    assert train["arrival"]["arrival_at"] == "2026-11-20T00:22:00+09:00"
    assert len(train["stops"]) == 2
    assert not train["intermediate_stops_available"]
