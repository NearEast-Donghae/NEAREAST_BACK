import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from html import unescape
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from backend.app.config import DATA
from backend.app.services.public_data import write_json
from scripts.fetch_tourism import ENDPOINT as TOURISM, FIELDS as TOUR_FIELDS
from scripts.fetch_train_data import CITIES, ROUTES, STATIONS

TRAIN_FILE = "donghae_train_timetables.json"
TOUR_FILE = "donghae_tourism.json"
CODES_FILE = "tago_train_codes.json"
SOURCES = {"tago": "15098552", "tourapi": "15101578"}
REGION = {"lDongRegnCd": "51", "lDongSignguCd": "170"}
QUERY_FIELDS = ("depPlaceId", "arrPlaceId", "depPlandTime")
TRAIN_FIELDS = ("trainno", "traingradename", "depplacename", "arrplacename", "depplandtime", "arrplandtime")


def read_export(archive, manifest, name):
    members = [item for item in archive.infolist() if item.filename == name]
    if len(members) != 1 or members[0].file_size > 20_000_000:
        raise ValueError(f"{name}: 예상한 ZIP 항목 한 개를 읽을 수 없습니다.")
    content = archive.read(members[0])
    expected = manifest[name]
    actual = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    if actual != expected:
        raise ValueError(f"{name}: 매니페스트와 파일 크기 또는 SHA-256이 일치하지 않습니다.")
    return json.loads(content)


def snapshot(service, endpoint, export, member, parameters, items):
    timestamp = datetime.fromisoformat(export["exported_at"])
    if timestamp.utcoffset() is None:
        raise ValueError(f"{member}: 수집 시각의 시간대가 필요합니다.")
    return {
        "source": service,
        "source_url": f"https://www.data.go.kr/data/{SOURCES[service]}/openapi.do",
        "endpoint": endpoint,
        "fetched_at": timestamp.isoformat(),
        "archive_member": member,
        "parameters": parameters,
        "items": items,
    }


def tago_snapshots(train, codes):
    day = datetime.strptime(train["departure_date"], "%Y%m%d").strftime("%Y%m%d")
    for key in ("departure_date", "cities", "stations", "donghae_stations"):
        if train[key] != codes[key]:
            raise ValueError(f"TAGO 시간표와 코드 카탈로그의 {key} 항목이 일치하지 않습니다.")
    if train["status"] != "complete" or train["errors"] or train["query_count"] != len(train["queries"]):
        raise ValueError("TAGO 조회를 완료한 수집본이 필요합니다.")

    stations = {row["nodeid"]: row for row in codes["stations"]}
    local_ids = {row["nodeid"] for row in codes["donghae_stations"]}
    if len(stations) != len(codes["stations"]) or not local_ids or not local_ids <= stations.keys():
        raise ValueError("TAGO 역 코드 목록을 확인하세요.")
    grouped = defaultdict(list)
    for row in train["items"]:
        grouped[row["depPlaceId"], row["arrPlaceId"], day].append({key: row[key] for key in TRAIN_FIELDS})

    schedules = []
    seen = set()
    for query in train["queries"]:
        departure, arrival, date = key = tuple(query[field] for field in QUERY_FIELDS)
        pair = {departure, arrival}
        rows = grouped.pop(key, [])
        if (key in seen or date != day or len(pair) != 2 or not pair <= stations.keys()
                or not pair & local_ids or query["status"] != "complete" or query["count"] != len(rows)):
            raise ValueError("TAGO 구간별 조회 메타데이터와 운행 행이 일치하지 않습니다.")
        seen.add(key)
        schedules.append(snapshot("tago", ROUTES, train, TRAIN_FILE, dict(zip(QUERY_FIELDS, key)), rows))
    expected_queries = len(local_ids) * (2 * len(stations) - len(local_ids) - 1)
    if grouped or len(seen) != expected_queries or train["count"] != sum(len(s["items"]) for s in schedules):
        raise ValueError("TAGO 전체 조회 범위 또는 운행 행 수가 일치하지 않습니다.")

    cities = [{key: row[key] for key in ("citycode", "cityname")} for row in codes["cities"]]
    by_city = defaultdict(list)
    for code, row in stations.items():
        by_city[str(row["citycode"])].append({
            "nodeid": code, "nodename": row["nodename"], "is_donghae": code in local_ids,
        })
    return {
        "tago_cities.json": snapshot("tago", CITIES, codes, CODES_FILE, {}, cities),
        f"tago_stations_{day}_export.json": [
            snapshot("tago", STATIONS, codes, CODES_FILE, {"cityCode": city}, rows)
            for city, rows in sorted(by_city.items())
        ],
        f"tago_schedule_{day}_export.json": schedules,
    }


def tour_snapshot(tour):
    items = tour["items"]
    if (tour["status"] != "complete" or tour["errors"] or tour["region_codes"] != REGION
            or tour["count"] != len(items) or tour["api_total_count"] != len(items)):
        raise ValueError("동해시 TourAPI 수집본의 상태나 건수가 일치하지 않습니다.")
    seen = set()
    places = []
    for item in items:
        row = {key.lower(): value for key, value in item.items()}
        content_id = row["contentid"]
        if (not content_id or content_id in seen or
                any(str(row.get(key.lower())) != value for key, value in REGION.items())):
            raise ValueError("TourAPI 장소 ID 또는 동해시 지역 코드가 일치하지 않습니다.")
        seen.add(content_id)
        place = {key: row[key] for key in TOUR_FIELDS if key in row}
        common = [record for record in item.get("common", []) if record.get("contentid") == content_id]
        if len(common) == 1 and common[0].get("overview"):
            place["overview"] = re.sub(r"<[^>]+>", " ", unescape(common[0]["overview"]))
        places.append(place)
    return [snapshot("tourapi", TOURISM, tour, TOUR_FILE, REGION, places)]


def import_archive(path, raw):
    with ZipFile(path) as archive:
        metadata = archive.getinfo("manifest.json")
        if metadata.file_size > 100_000:
            raise ValueError("ZIP 매니페스트 크기를 확인하세요.")
        manifest = json.loads(archive.read(metadata))
        train = read_export(archive, manifest, TRAIN_FILE)
        codes = read_export(archive, manifest, CODES_FILE)
        tour = read_export(archive, manifest, TOUR_FILE)
    outputs = {**tago_snapshots(train, codes), "tourapi.json": tour_snapshot(tour)}
    for name, value in outputs.items():
        target = raw / name
        if target.exists() and json.loads(target.read_bytes()) != value:
            raise ValueError(f"{name}: 내용이 다른 기존 스냅샷이 있습니다.")
    for name, value in outputs.items():
        if not (raw / name).exists():
            write_json(raw / name, value)
    return {
        "train_query_count": train["query_count"], "train_segments": train["count"],
        "train_date": train["departure_date"], "station_codes": len(codes["stations"]),
        "tourism_records": tour["count"],
        "tourism_types": dict(Counter(str(row["contenttypeid"]) for row in tour["items"])),
    }


def main():
    parser = argparse.ArgumentParser(description="첨부 공공데이터 ZIP에서 TAGO와 TourAPI 스냅샷을 가져옵니다.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--raw", type=Path, default=DATA / "raw")
    args = parser.parse_args()
    try:
        summary = import_archive(args.archive, args.raw)
    except (BadZipFile, OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
