import argparse
import csv
import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from backend.app.config import DATA
from backend.app.services.public_data import todayKst, writeJson
from scripts.import_csv import SOURCES, collectCsv, readCsv
from scripts.normalize_data import (
    closeCoordinates, mergeLocations, mergeTagoStationCodes, railwayLines, tourapiRecords, trainRecords,
)
from scripts.validate_data import validate

LOCATION_FIELDS = ("id", "name", "kind", "place_type", "address", "alternate_address", "latitude",
                   "longitude", "crs", "description", "visit_allowed", "coordinate_ready", "routing_ready",
                   "observed_stay_minutes", "default_stay_minutes", "amenities", "sources", "field_sources")
STATION_FIELDS = LOCATION_FIELDS + ("operation_status", "passenger_handling", "freight_handling",
                                    "railway_lines", "railway_line_sources", "tago_station_id")
HUB_FIELDS = LOCATION_FIELDS + ("operator", "facility_category")


def loadSnapshots(path, source, endpoint):
    item = json.loads(path.read_text(encoding="utf-8"))
    values = item if isinstance(item, list) else [item]
    for value in values:
        if (not isinstance(value, dict) or value.get("source") != source or
                value.get("endpoint") != endpoint or not value.get("source_url") or
                not value.get("fetched_at") or not isinstance(value.get("parameters"), dict) or
                not isinstance(value.get("items"), list)):
            raise ValueError(f"API 스냅샷 형식 확인 필요: {path.name}")
        if any(not isinstance(row, dict) for row in value["items"]):
            raise ValueError(f"API 스냅샷 레코드 확인 필요: {path.name}")
    return values


def loadReference(reference, filename):
    path = reference / filename
    return readCsv(path)[0].to_dict("records") if path.is_file() else []


def excludeReviewedCoordinates(records, reviews):
    issues = []
    reviewMap = {(review["dataset"], review["name"]): review for review in reviews
                  if review.get("source_url") and review.get("checked_at")}
    for record in records:
        row, source = record["row"], record["source"]
        for nameField, latField, lngField in (
            ("관광명소명", "위도", "경도"), ("시설명", "위도", "경도"),
            ("역이름", "위도좌표", "경도좌표"), ("name", "latitude", "longitude"),
            ("title", "mapy", "mapx"),
        ):
            review = reviewMap.get((source["dataset"], row.get(nameField)))
            if (review and str(row.get(latField)).strip() == review["latitude"]
                    and str(row.get(lngField)).strip() == review["longitude"]):
                issues.append({"code": "quarantined_coordinates", **review, "source": source})
                row[latField] = row[lngField] = ""
    return issues


def selectLocationFields(place, fields):
    return {field: ({key: value for key, value in place[field].items() if key in fields}
                    if field == "field_sources" else place[field]) for field in fields}


def writeCsv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8-sig", newline="", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            writer = csv.DictWriter(stream, fieldnames=LOCATION_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value, ensure_ascii=False)
                                 if isinstance(value, (bool, list, dict)) else value
                                 for key, value in row.items()})
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def qualityMarkdown(report):
    counts = report["counts"]
    lines = [
        "# NEAREAST STEP 1 데이터 품질 검증",
        "",
        f"생성일(KST) {report['generated_on_kst']}",
        "",
        "| 파일 | 행 수 |",
        "|---|---:|",
        *(f"| `{table}.json` | {count} |" for table, count in counts.items()),
        f"| `locations.csv` | {counts['locations']} |",
        "",
        f"- 통합 장소 중 유효한 좌표 {report['coordinate_ready_count']}건. "
        f"공개 방문 근거까지 확인한 경로 후보 {report['routing_ready_count']}건.",
        f"- 중복 병합 {report['merged_records']}건. 출력 구조·좌표·참조 검증 오류 "
        f"{len(report['validation_errors'])}건.",
        "- 아래 좌표 검사는 수치 범위와 확인된 사례를 다룹니다. 개별 출입구 위치는 별도로 확인합니다.",
        "",
        "## CSV 입력",
        "",
        "| 파일 | 인코딩 | 읽은 행 | 반영 후보 | 타지역 | 처리 상태 |",
        "|---|---|---:|---:|---:|---|",
    ]
    lines.extend(
        f"| {item['file']} | {item.get('encoding', '—')} | {item.get('rows', 0)} | "
        f"{item['included']} | {item['out_of_scope']} | {item['status']} |"
        for item in report["csv_inventory"]
    )
    lines += ["", "## 누락 및 검토", "",
              "- 좌표 누락 또는 보류: " + (", ".join(report["locations_missing_coordinates"]) or "없음"),
              "- 자료별 입력·필드 매핑은 [데이터 출처 및 활용 내역](../../docs/data_sources.md)에 기록했습니다.",
              "- 검토 대기 항목은 [missing_data.json](missing_data.json)에 있습니다.", "",
              "| 필드 | 미확보 건수 |", "|---|---:|"]
    lines.extend(f"| `{field}` | {total} |" for field, total in report["missing_fields"].items())
    lines += ["", "| 수집 범위 | 상태 |", "|---|---|"]
    lines.extend(f"| `{name}` | {json.dumps(value, ensure_ascii=False)} |" for name, value in report["api"].items())
    if report["issues"]:
        lines += ["", "| 점검 항목 | 대상 | 사유 |", "|---|---|---|"]
        for item in report["issues"]:
            name = item.get("name") or ", ".join(item.get("names", [])) or item.get("id") or "—"
            lines.append(f"| {item['code']} | {name} | {item.get('reason', '세부 내용은 data_quality.json 참고')} |")
    return "\n".join(lines) + "\n"


def build(raw, processed, reports, reference=DATA / "reference"):
    records, inventory = collectCsv(raw)
    if reference.is_dir() and reference.resolve() != raw.resolve():
        supplemental, supplementalInventory = collectCsv(reference)
        records.extend(supplemental)
        inventory.extend(row for row in supplementalInventory if row.get("dataset") == "verified_places")
    inputErrors = [row for row in inventory if row["status"] == "error"]
    if inputErrors:
        writeJson(reports / "input_errors.json", inputErrors)
        raise ValueError(f"{len(inputErrors)}개 CSV 읽기 오류. data/reports/input_errors.json 확인")
    tourism = raw / "tourapi.json"
    tourismSnapshots = loadSnapshots(tourism, "tourapi", "areaBasedList2") if tourism.is_file() else []
    for entry in tourismSnapshots:
        params = entry["parameters"]
        if str(params.get("lDongRegnCd")) != "51" or str(params.get("lDongSignguCd")) != "170":
            raise ValueError("TourAPI 스냅샷의 동해시 지역 코드 확인 필요")
    records.extend(tourapiRecords(tourismSnapshots))
    issues = excludeReviewedCoordinates(records, loadReference(reference, "coordinate_exclusions.csv"))
    places, mergeIssues = mergeLocations(records)
    issues.extend(mergeIssues)
    for left, right in combinations(places, 2):
        if closeCoordinates(left, right, meters=15):
            issues.append({"code": "nearby_distinct_locations", "names": [left["name"], right["name"]]})

    trainSnapshots = [
        entry for path in sorted(raw.glob("tago_schedule_*.json"))
        for entry in loadSnapshots(path, "tago", "GetStrtpntAlocFndTrainInfo")
    ]
    tagoIndex = {}
    localTagoIndex = {}
    attachmentStations = {}
    for path in sorted(raw.glob("tago_stations_*.json")):
        for entry in loadSnapshots(path, "tago", "GetCtyAcctoTrainSttnList"):
            for recordNumber, row in enumerate(entry["items"], start=1):
                fields = {key.lower(): value for key, value in row.items()}
                if fields.get("nodeid") and fields.get("nodename"):
                    code, name = str(fields["nodeid"]), str(fields["nodename"]).strip()
                    tagoIndex[code] = name
                    if fields.get("is_donghae") is not False:
                        localTagoIndex[code] = name
                    if fields.get("is_donghae") is True:
                        attachmentStations[code] = (name, {
                            "dataset": "tago", "file": path.name, "record_number": recordNumber,
                            "url": entry["source_url"], "archive_member": entry.get("archive_member"),
                            "record_date": None, "snapshot_date": entry["fetched_at"][:10],
                            "fetched_at": entry["fetched_at"], "checked_at": None,
                        })

    additions, stationIssues = mergeTagoStationCodes(places, attachmentStations)
    mergedRecords = len(records) - len(places) - sum(issue["code"] == "rejected_record" for issue in issues)
    places.extend(additions)
    issues.extend(stationIssues)
    for place in places:
        if place["kind"] == "station":
            place.setdefault("tago_station_id", None)
    stations = [selectLocationFields(place, STATION_FIELDS) for place in places if place["kind"] == "station"]
    if attachmentStations:
        localTagoIndex = {code: name for code, (name, _) in attachmentStations.items()}
    trains, trainIssues = trainRecords(trainSnapshots, stations, localTagoIndex)
    issues.extend(trainIssues)
    tables = {
        "locations": [selectLocationFields(place, LOCATION_FIELDS) for place in places],
        "stations": stations,
        "railway_lines": railwayLines(stations),
        "trains": trains,
        "transport_hubs": [
            selectLocationFields(place, HUB_FIELDS)
            for place in places if place["kind"] == "transport_hub"
        ],
    }
    errors = validate(tables)
    if errors:
        writeJson(reports / "validation_errors.json", errors)
        raise ValueError("통합 데이터 검증에 실패했습니다.")
    for collection, rows in tables.items():
        writeJson(processed / f"{collection}.json", rows)
    writeCsv(processed / "locations.csv", tables["locations"])

    coordinateMissing = [place["name"] for place in places if not place["coordinate_ready"]]
    missingDetails = {}
    for field in ("address", "description", "visit_allowed", "observed_stay_minutes"):
        missingDetails[field] = sum(place[field] is None for place in places)
    for field in ("operation_status", "passenger_handling", "freight_handling"):
        missingDetails[f"station_{field}"] = sum(station[field] is None for station in stations)
    datasets = {row.get("dataset") for row in inventory}
    scheduledDays = sorted({entry["parameters"]["depPlandTime"] for entry in trainSnapshots})
    report = {
        "generated_on_kst": todayKst(),
        "counts": {name: len(rows) for name, rows in tables.items()},
        "location_kinds": dict(Counter(place["kind"] for place in places)),
        "merged_records": mergedRecords,
        "coordinate_ready_count": sum(place["coordinate_ready"] for place in places),
        "routing_ready_count": sum(place["routing_ready"] for place in places),
        "locations_missing_coordinates": coordinateMissing,
        "missing_fields": missingDetails,
        "csv_inventory": inventory,
        "expected_csv_missing": [key for key, *_ in SOURCES if key != "verified_places" and key not in datasets],
        "api": {
            "tourapi": "collected" if tourismSnapshots else "no_snapshot",
            "tago_passenger": "collected" if trainSnapshots else "no_snapshot",
            "tago_dates_queried": scheduledDays,
            "tago_route_queries": len({tuple(entry["parameters"][field] for field in
                                             ("depPlandTime", "depPlaceId", "arrPlaceId"))
                                       for entry in trainSnapshots}),
            "tago_station_codes": len(tagoIndex),
            "tourapi_source_records": sum(len(snapshot["items"]) for snapshot in tourismSnapshots),
            "donghae_traffic_grid": "available_separately" if (raw / "donghae_traffic_grid.json").is_file()
                                    else "no_snapshot",
            "freight_schedules": "not_acquired",
            "passing_train_schedules": "not_acquired",
        },
        "issues": issues,
        "validation_errors": errors,
    }
    writeJson(reports / "data_quality.json", report)
    (reports / "data_quality_report.md").write_text(qualityMarkdown(report), encoding="utf-8")
    writeJson(reports / "missing_data.json", {
        "source_review_queue": loadReference(reference, "review_queue.csv"),
        "locations_missing_coordinates": coordinateMissing,
        "missing_fields": missingDetails,
        "api": report["api"],
    })
    for stale in ("input_errors.json", "validation_errors.json"):
        (reports / stale).unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description="NEAREAST 장소/철도 데이터 및 검증 보고서 생성")
    parser.add_argument("--raw", type=Path, default=DATA / "raw")
    parser.add_argument("--processed", type=Path, default=DATA / "processed")
    parser.add_argument("--reports", type=Path, default=DATA / "reports")
    parser.add_argument("--reference", type=Path, default=DATA / "reference")
    args = parser.parse_args()
    try:
        report = build(args.raw, args.processed, args.reports, args.reference)
    except (ValueError, KeyError, OSError, pd.errors.ParserError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps({key: report[key] for key in ("counts", "coordinate_ready_count", "routing_ready_count",
                                                   "validation_errors")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
