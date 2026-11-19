import argparse
import csv
import json
from collections import Counter
from datetime import date
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from backend.app.config import DATA
from backend.app.services.public_data import today_kst, write_json
from scripts.import_csv import SOURCES, collect_csv, read_csv
from scripts.normalize_data import close_coordinates, merge_locations, railway_lines, tourapi_records, train_records
from scripts.validate_data import validate

LOCATION_FIELDS = ("id", "name", "kind", "place_type", "address", "alternate_address", "latitude",
                   "longitude", "crs", "description", "visit_allowed", "coordinate_ready", "routing_ready",
                   "observed_stay_minutes", "default_stay_minutes", "sources")
STATION_FIELDS = LOCATION_FIELDS + ("operation_status", "passenger_handling", "freight_handling",
                                    "railway_lines")
HUB_FIELDS = LOCATION_FIELDS + ("operator", "facility_category")


def snapshots(path, source, endpoint):
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


def load_reference(reference, filename):
    path = reference / filename
    return read_csv(path)[0].to_dict("records") if path.is_file() else []


def exclude_reviewed_coordinates(records, reviews):
    issues = []
    review_map = {(review["dataset"], review["name"]): review for review in reviews
                  if review.get("source_url") and review.get("checked_at")}
    for record in records:
        row, source = record["row"], record["source"]
        for name_field, lat_field, lng_field in (
            ("관광명소명", "위도", "경도"), ("시설명", "위도", "경도"),
            ("역이름", "위도좌표", "경도좌표"), ("name", "latitude", "longitude"),
            ("title", "mapy", "mapx"),
        ):
            review = review_map.get((source["dataset"], row.get(name_field)))
            if (review and str(row.get(lat_field)).strip() == review["latitude"]
                    and str(row.get(lng_field)).strip() == review["longitude"]):
                issues.append({"code": "quarantined_coordinates", **review, "source": source})
                row[lat_field] = row[lng_field] = ""
    return issues


def select(place, fields):
    return {field: place[field] for field in fields}


def write_csv(path, rows):
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


def build(raw, processed, reports, reference=DATA / "reference"):
    records, inventory = collect_csv(raw)
    if reference.is_dir() and reference.resolve() != raw.resolve():
        supplemental, supplemental_inventory = collect_csv(reference)
        records.extend(supplemental)
        inventory.extend(row for row in supplemental_inventory if row.get("dataset") == "verified_places")
    input_errors = [row for row in inventory if row["status"] == "error"]
    if input_errors:
        write_json(reports / "input_errors.json", input_errors)
        raise ValueError(f"{len(input_errors)}개 CSV 읽기 오류. data/reports/input_errors.json 확인")
    tourism = raw / "tourapi.json"
    tourism_snapshots = snapshots(tourism, "tourapi", "areaBasedList2") if tourism.is_file() else []
    for entry in tourism_snapshots:
        params = entry["parameters"]
        if str(params.get("lDongRegnCd")) != "51" or str(params.get("lDongSignguCd")) != "170":
            raise ValueError("TourAPI 스냅샷의 동해시 지역 코드 확인 필요")
    records.extend(tourapi_records(tourism_snapshots))
    issues = exclude_reviewed_coordinates(records, load_reference(reference, "coordinate_exclusions.csv"))
    places, merge_issues = merge_locations(records)
    issues.extend(merge_issues)
    for left, right in combinations(places, 2):
        if close_coordinates(left, right, meters=15):
            issues.append({"code": "nearby_distinct_locations", "names": [left["name"], right["name"]]})

    train_snapshots = [
        entry for path in sorted(raw.glob("tago_schedule_*.json"))
        for entry in snapshots(path, "tago", "GetStrtpntAlocFndTrainInfo")
    ]
    tago_index = {}
    for path in sorted(raw.glob("tago_stations_*.json")):
        for entry in snapshots(path, "tago", "GetCtyAcctoTrainSttnList"):
            for row in entry["items"]:
                fields = {key.lower(): value for key, value in row.items()}
                if fields.get("nodeid") and fields.get("nodename"):
                    tago_index[str(fields["nodeid"])] = str(fields["nodename"]).strip()

    stations = [select(place, STATION_FIELDS) for place in places if place["kind"] == "station"]
    trains, train_issues = train_records(train_snapshots, stations, tago_index)
    issues.extend(train_issues)
    tables = {
        "locations": [select(place, LOCATION_FIELDS) for place in places],
        "stations": stations,
        "railway_lines": railway_lines(stations),
        "trains": trains,
        "transport_hubs": [select(place, HUB_FIELDS) for place in places if place["kind"] == "transport_hub"],
    }
    errors = validate(tables)
    if errors:
        write_json(reports / "validation_errors.json", errors)
        raise ValueError("통합 데이터 검증에 실패했습니다.")
    for collection, rows in tables.items():
        write_json(processed / f"{collection}.json", rows)
    write_csv(processed / "locations.csv", tables["locations"])

    coordinate_missing = [place["name"] for place in places if not place["coordinate_ready"]]
    missing_details = {}
    for field in ("address", "description", "visit_allowed", "observed_stay_minutes"):
        missing_details[field] = sum(place[field] is None for place in places)
    for field in ("operation_status", "passenger_handling", "freight_handling"):
        missing_details[f"station_{field}"] = sum(station[field] is None for station in stations)
    datasets = {row.get("dataset") for row in inventory}
    scheduled_days = sorted({entry["parameters"]["depPlandTime"] for entry in train_snapshots})
    report = {
        "generated_on_kst": today_kst(),
        "counts": {name: len(rows) for name, rows in tables.items()},
        "location_kinds": dict(Counter(place["kind"] for place in places)),
        "merged_records": len(records) - len(places) - sum(i["code"] == "rejected_record" for i in issues),
        "coordinate_ready_count": sum(place["coordinate_ready"] for place in places),
        "routing_ready_count": sum(place["routing_ready"] for place in places),
        "locations_missing_coordinates": coordinate_missing,
        "missing_fields": missing_details,
        "csv_inventory": inventory,
        "expected_csv_missing": [key for key, *_ in SOURCES if key != "verified_places" and key not in datasets],
        "api": {
            "tourapi": "collected" if tourism_snapshots else "no_snapshot",
            "tago_passenger": "collected" if train_snapshots else "no_snapshot",
            "tago_dates_queried": scheduled_days,
            "tago_station_codes": len(tago_index),
            "donghae_traffic_grid": "available_separately" if (raw / "donghae_traffic_grid.json").is_file()
                                    else "no_snapshot",
            "freight_schedules": "not_acquired",
            "passing_train_schedules": "not_acquired",
        },
        "issues": issues,
        "validation_errors": errors,
    }
    write_json(reports / "data_quality.json", report)
    write_json(reports / "missing_data.json", {
        "source_review_queue": load_reference(reference, "review_queue.csv"),
        "locations_missing_coordinates": coordinate_missing,
        "missing_fields": missing_details,
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
