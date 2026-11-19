import argparse
import json
from pathlib import Path

from scripts.normalize_data import LOCATION_KINDS, coordinatePair

TABLES = ("locations", "stations", "railway_lines", "trains", "transport_hubs")


def validate(tables):
    errors = []
    for table in TABLES:
        seen = set()
        for row in tables[table]:
            stableId = row.get("id")
            if not isinstance(stableId, str) or not stableId or stableId in seen:
                errors.append(f"{table}: 유효하지 않거나 중복된 ID {stableId}")
            seen.add(stableId)
    byId = {row["id"]: row for row in tables["locations"] if "id" in row}
    for row in byId.values():
        key = row["id"]
        latitude, longitude = row.get("latitude"), row.get("longitude")
        issue = coordinatePair(latitude, longitude)[2]
        if issue and (latitude is not None or longitude is not None):
            errors.append(f"{key}: 유효하지 않은 위경도")
        if row.get("kind") not in LOCATION_KINDS or not row.get("name") or not row.get("sources"):
            errors.append(f"{key}: 유형·이름·출처 확인 필요")
        sources = {source.get("source_ref") for source in row.get("sources", [])}
        if None in sources or any(not refs or not set(refs) <= sources
                                  for refs in row.get("field_sources", {}).values()):
            errors.append(f"{key}: 출처 식별자 또는 필드 참조 불일치")
        if row.get("crs") != "EPSG:4326":
            errors.append(f"{key}: 좌표계 확인 필요")
        if row.get("coordinate_ready") != (issue is None):
            errors.append(f"{key}: 좌표 상태 불일치")
        if row.get("routing_ready") != (issue is None and row.get("visit_allowed") is True):
            errors.append(f"{key}: 경로 사용 조건 불일치")
    for table, kind in (("stations", "station"), ("transport_hubs", "transport_hub")):
        actual = {row.get("id") for row in tables[table]}
        expected = {key for key, row in byId.items() if row.get("kind") == kind}
        if actual != expected:
            errors.append(f"{table}: locations 참조 누락 또는 불일치")
        for row in tables[table]:
            location = byId.get(row.get("id"), {})
            if any(row.get(field) != location.get(field)
                   for field in ("name", "address", "latitude", "longitude", "crs", "visit_allowed")):
                errors.append(f"{row.get('id')}: 장소 상세 내용이 locations와 일치하지 않음")
            sources = {source.get("source_ref") for source in row.get("sources", [])}
            for referenceMap in ("field_sources", "railway_line_sources"):
                if any(not refs or not set(refs) <= sources
                       for refs in row.get(referenceMap, {}).values()):
                    errors.append(f"{row.get('id')}: {referenceMap} 출처 식별자 불일치")
    stationIds = {row.get("id") for row in tables["stations"]}
    for line in tables["railway_lines"]:
        if not set(line.get("station_ids", [])) <= stationIds:
            errors.append(f"{line.get('id')}: 노선에 유효하지 않은 역 ID 포함")
    for train in tables["trains"]:
        for field in ("departure", "arrival"):
            location = train.get(field, {}).get("location_id")
            if location is not None and location not in stationIds:
                errors.append(f"{train.get('id')}: 열차 구간에 유효하지 않은 역 ID 포함")
    return errors


def main():
    parser = argparse.ArgumentParser(description="생성된 NEAREAST JSON 및 참조 검증")
    parser.add_argument("--processed", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data" / "processed")
    args = parser.parse_args()
    try:
        tables = {name: json.loads((args.processed / f"{name}.json").read_text(encoding="utf-8"))
                  for name in TABLES}
        if any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
               for rows in tables.values()):
            raise ValueError("JSON 최상위 값은 레코드 목록이어야 합니다.")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    errors = validate(tables)
    print(json.dumps({"counts": {name: len(rows) for name, rows in tables.items()}, "errors": errors},
                     ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
