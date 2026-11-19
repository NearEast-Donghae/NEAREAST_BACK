import math
import re
import uuid
from collections import defaultdict
from datetime import datetime

from scripts.import_csv import clean, first, iso_date

LOCATION_KINDS = {"station", "tourism", "transport_hub"}
DEFAULT_STAY = {"tourism": 40, "station": 20, "transport_hub": 15,
                "railway_heritage": 30, "observation_point": 25, "former_station_site": 20}
# Screening envelope for Donghae and its coastline. A valid pair does not prove
# administrative membership or that the pin corresponds to a public entrance.
BOUNDS = (37.40, 37.68, 128.84, 129.20)
PRIORITY = {"verified_places": 0, "kr_stations": 1, "tourism": 1, "terminals": 1,
            "korail_donghae": 2, "tourapi": 3}


def identifier(namespace, *parts):
    return f"{namespace}-{uuid.uuid5(uuid.NAMESPACE_URL, 'neareast/' + '/'.join(parts))}"


def name_key(value):
    return re.sub(r"\s+", "", value.casefold())


def canonical_address(value):
    if not value or re.match(r"https?://", value, flags=re.I):
        return None
    return re.sub(r"^(?:강원도|강원)\s+", "강원특별자치도 ", value)


def coordinate_pair(lat_raw, lng_raw):
    try:
        if lat_raw is None and lng_raw is None:
            return None, None, "missing_coordinates"
        lat, lng = float(lat_raw), float(lng_raw)
        south, north, west, east = BOUNDS
        if not (math.isfinite(lat) and math.isfinite(lng) and south <= lat <= north
                and west <= lng <= east):
            raise ValueError
        return lat, lng, None
    except (TypeError, ValueError):
        return None, None, "invalid_coordinates"


def truth(value):
    return {"true": True, "false": False}.get((value or "").lower())


def positive_minutes(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def operation(value):
    return value if value in {"operating", "closed", "suspended"} else None


def service_flag(work, token):
    # Explicit positive/negative only. Absence of a keyword is unknown.
    for value in work:
        if value in {f"{token}취급중단", f"{token}취급안함", f"{token}미취급"}:
            return False
    if f"{token}취급" in work:
        return True
    return None


def normalize_location(record):
    source, row = record["source"], record["row"]
    kind = record["kind"]
    if kind == "curated":
        kind = first(row, "kind")
        if not source["url"] or not source["checked_at"]:
            raise ValueError("수동 검증 자료에 source_url과 checked_at을 입력하세요.")
    if kind not in LOCATION_KINDS:
        raise ValueError("지원하지 않는 장소 kind")
    name = first(row, "name", "역이름", "역사명", "관광명소명", "시설명", "title")
    if not name:
        raise ValueError("장소명 누락")
    if kind == "station":
        name = re.sub(r"\s+역$", "역", name)
    address = canonical_address(first(row, "address", "도로명주소", "소재지도로명주소",
                                      "주소", "역사도로명주소", "addr1",
                                      "지번주소", "소재지지번주소"))
    alternate = canonical_address(first(row, "지번주소", "소재지지번주소"))
    lat, lng, coordinate_issue = coordinate_pair(
        first(row, "latitude", "위도", "위도좌표", "역위도", "mapy"),
        first(row, "longitude", "경도", "경도좌표", "역경도", "mapx"),
    )
    work = [name_key(value) for value in (first(row, "취급업무") or "").split(",")]
    status = operation(first(row, "operation_status"))
    passenger = truth(first(row, "passenger_handling"))
    freight = truth(first(row, "freight_handling"))
    line_names = sorted({value for token in re.split(r"[,;]", first(
        row, "railway_lines", "관련노선", "노선명") or "") if (value := clean(token))})
    subtype = first(row, "place_type") or {
        "station": "railway_station",
        "tourism": "cultural_facility" if first(row, "contenttypeid") == "14" else "attraction",
        "transport_hub": "passenger_terminal",
    }[kind]
    explicit_id = first(row, "source_id", "역번호", "contentid")
    return {
        "name": name, "kind": kind, "place_type": subtype,
        "address": address, "alternate_address": alternate if alternate != address else None,
        "latitude": lat, "longitude": lng, "coordinate_issue": coordinate_issue,
        "description": first(row, "description", "역소개", "장소설명", "설명", "overview"),
        "visit_allowed": truth(first(row, "visit_allowed")),
        "observed_stay_minutes": positive_minutes(first(row, "observed_stay_minutes", "체류시간(분)")),
        "operation_status": status,
        "passenger_handling": passenger if passenger is not None else service_flag(work, "여객"),
        "freight_handling": freight if freight is not None else service_flag(work, "화물"),
        "railway_lines": line_names if kind == "station" else [],
        "operator": first(row, "운영주체"),
        "facility_category": first(row, "구분"),
        "source": {**source, "source_id": explicit_id},
    }


def close_coordinates(left, right, meters=100):
    if left["latitude"] is None or right["latitude"] is None:
        return False
    lat = (left["latitude"] + right["latitude"]) / 2
    return math.hypot(
        (left["latitude"] - right["latitude"]) * 111_200,
        (left["longitude"] - right["longitude"]) * 111_200 * math.cos(math.radians(lat)),
    ) <= meters


def same_place(left, right):
    a, b = left["source"], right["source"]
    if a["dataset"] == b["dataset"] and a["source_id"] and b["source_id"]:
        return a["source_id"] == b["source_id"]
    addresses = {left["address"], left["alternate_address"]} - {None}
    return bool(addresses & {right["address"], right["alternate_address"]}) or close_coordinates(left, right)


def merge_locations(records):
    groups = defaultdict(list)
    issues = []
    for record in records:
        try:
            place = normalize_location(record)
        except ValueError as exc:
            issues.append({"code": "rejected_record", "reason": str(exc), "source": record["source"]})
            continue
        if place["coordinate_issue"] == "invalid_coordinates":
            issues.append({"code": "invalid_input_coordinates", "name": place["name"],
                           "source": place["source"]})
        groups[place["kind"], name_key(place["name"])].append(place)
    result = []
    for (kind, normalized_name), candidates in sorted(groups.items()):
        candidates.sort(key=lambda item: (
            PRIORITY.get(item["source"]["dataset"], 9),
            item["source"].get("file") or "",
            item["source"].get("record_number") or 0,
        ))
        clusters = []
        for candidate in candidates:
            matches = [group for group in clusters if any(same_place(candidate, p) for p in group)]
            if len(matches) == 1:
                matches[0].append(candidate)
            else:
                clusters.append([candidate])
        if len(clusters) > 1:
            issues.append({"code": "ambiguous_same_name", "name": candidates[0]["name"],
                           "count": len(clusters)})
        for cluster in clusters:
            primary = cluster[0]
            anchor = primary["address"] or primary["source"]["source_id"]
            if not anchor:
                anchor = repr((primary["latitude"], primary["longitude"]))
                if primary["latitude"] is None:
                    anchor = f"{primary['source']['file']}:{primary['source']['record_number']}"
            key = identifier("place", kind, normalized_name, anchor)
            merged = {"id": key, "name": primary["name"], "kind": kind,
                      "place_type": primary["place_type"], "crs": "EPSG:4326"}
            uncertain = {"visit_allowed", "operation_status", "passenger_handling", "freight_handling"}
            for field in ("address", "alternate_address", "description", "visit_allowed",
                          "observed_stay_minutes", "operation_status", "passenger_handling",
                          "freight_handling", "operator", "facility_category"):
                values = list(dict.fromkeys(item[field] for item in cluster if item[field] is not None))
                if len(values) > 1 and field in uncertain:
                    issues.append({"code": "field_conflict", "id": key, "field": field, "values": values})
                    merged[field] = None
                else:
                    merged[field] = values[0] if values else None
            coordinates = [item for item in cluster if item["latitude"] is not None]
            coordinate = coordinates[0] if coordinates else None
            if coordinate and any(not close_coordinates(coordinate, item) for item in coordinates[1:]):
                issues.append({"code": "coordinate_conflict", "id": key, "name": primary["name"]})
                coordinate = None
            merged["latitude"] = coordinate["latitude"] if coordinate else None
            merged["longitude"] = coordinate["longitude"] if coordinate else None
            merged["coordinate_ready"] = coordinate is not None
            merged["routing_ready"] = coordinate is not None and merged["visit_allowed"] is True
            merged["default_stay_minutes"] = DEFAULT_STAY.get(primary["place_type"], DEFAULT_STAY[kind])
            merged["railway_lines"] = sorted({line for row in cluster for line in row["railway_lines"]})
            merged["sources"] = [row["source"] for row in cluster]
            result.append(merged)
    return result, issues


def tourapi_records(snapshots):
    records = []
    for snapshot in snapshots:
        for index, row in enumerate(snapshot["items"], start=1):
            records.append({
                "kind": "tourism",
                "row": row,
                "source": {"dataset": "tourapi", "file": "tourapi.json", "record_number": index,
                           "url": snapshot["source_url"], "record_date": iso_date(row.get("modifiedtime")),
                           "snapshot_date": iso_date(snapshot["fetched_at"]),
                           "checked_at": None,
                           "fetched_at": snapshot["fetched_at"]},
            })
    return records


def time_stamp(value):
    if value is None:
        return None
    try:
        text = str(value)
        if not re.fullmatch(r"[0-9]{14}", text):
            return None
        return datetime.strptime(text, "%Y%m%d%H%M%S").isoformat() + "+09:00"
    except ValueError:
        return None


def train_records(snapshots, stations, tago_index):
    trains, issues = {}, []
    by_name = defaultdict(list)
    for station in stations:
        by_name[name_key(station["name"].removesuffix("역"))].append(station["id"])

    def match_local(tago_id, name):
        official_name = tago_index.get(tago_id)
        if not official_name or name_key(official_name.removesuffix("역")) != name_key(name.removesuffix("역")):
            return None
        matches = by_name[name_key(name.removesuffix("역"))]
        return matches[0] if len(matches) == 1 else None

    for snapshot in snapshots:
        params = snapshot["parameters"]
        expected_day = iso_date(params["depPlandTime"])
        dep_id, arr_id = params["depPlaceId"], params["arrPlaceId"]
        for content in snapshot["items"]:
            row = {key.lower(): value for key, value in content.items()}
            departure, arrival = first(row, "depplacename"), first(row, "arrplacename")
            dep_at, arr_at = time_stamp(row.get("depplandtime")), time_stamp(row.get("arrplandtime"))
            number, grade = first(row, "trainno"), first(row, "traingradename")
            invalid = (not all((departure, arrival, dep_at, arr_at, expected_day)) or
                       (dep_at is not None and dep_at[:10] != expected_day) or
                       (dep_at is not None and arr_at is not None and dep_at > arr_at))
            if invalid:
                issues.append({"code": "invalid_train_segment", "train_number": number,
                               "departure_station_id": dep_id, "arrival_station_id": arr_id,
                               "date": expected_day})
                continue
            key = identifier("train", dep_at, arr_at, dep_id, arr_id, grade or "", number or "")
            trains[key] = {
                "id": key, "train_number": number, "train_type": grade, "service_date": expected_day,
                "service_kind": "passenger", "route_scope": "queried_segment", "full_route": None,
                "departure": {"station_name": departure, "tago_station_id": dep_id,
                              "location_id": match_local(dep_id, departure), "departure_at": dep_at},
                "arrival": {"station_name": arrival, "tago_station_id": arr_id,
                            "location_id": match_local(arr_id, arrival), "arrival_at": arr_at},
                "stops": [{"tago_station_id": dep_id, "station_name": departure, "departure_at": dep_at},
                          {"tago_station_id": arr_id, "station_name": arrival, "arrival_at": arr_at}],
                "intermediate_stops_available": False,
                "source": {"url": snapshot["source_url"], "fetched_at": snapshot["fetched_at"],
                           "endpoint": snapshot["endpoint"]},
            }
    return list(trains.values()), issues


def railway_lines(stations):
    lines = {}
    for station in stations:
        for line in station["railway_lines"]:
            entry = lines.setdefault(line, {
                "id": identifier("rail", line),
                "name": line,
                "kind": "train_service" if "셔틀" in line or "(고속)" in line else "railway_line",
                "station_ids": [],
                "station_order_verified": False,
                "geometry": None,
                "sources": [],
            })
            entry["station_ids"].append(station["id"])
            entry["sources"].extend(s for s in station["sources"] if s not in entry["sources"])
    return list(lines.values())
