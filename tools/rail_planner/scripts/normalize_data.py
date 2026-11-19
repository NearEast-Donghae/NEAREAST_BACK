import math
import re
import uuid
from collections import defaultdict
from datetime import datetime

from scripts.import_csv import clean, firstNonEmpty, isoDate

LOCATION_KINDS = {"station", "tourism", "transport_hub"}
TOURAPI_PLACE_TYPES = {
    "12": "attraction", "14": "cultural_facility", "15": "event",
    "25": "travel_course", "28": "leisure", "32": "accommodation",
    "38": "shopping", "39": "dining",
}
DEFAULT_STAY = {"tourism": 40, "station": 20, "transport_hub": 15,
                "railway_heritage": 30, "observation_point": 25, "former_station_site": 20}
# Screening envelope for Donghae and its coastline. A valid pair does not prove
# administrative membership or that the pin corresponds to a public entrance.
BOUNDS = (37.40, 37.68, 128.84, 129.20)
PRIORITY = {"verified_places": 0, "kr_stations": 1, "tourism": 1, "terminals": 1,
            "korail_donghae": 2, "tourapi": 3}


def stableId(namespace, *parts):
    return f"{namespace}-{uuid.uuid5(uuid.NAMESPACE_URL, 'neareast/' + '/'.join(parts))}"


def normalizedNameKey(value):
    return re.sub(r"\s+", "", value.casefold())


def canonicalAddress(value):
    if not value or re.match(r"https?://", value, flags=re.I):
        return None
    return re.sub(r"^(?:강원도|강원)\s+", "강원특별자치도 ", value)


def coordinatePair(latRaw, lngRaw):
    try:
        if latRaw is None and lngRaw is None:
            return None, None, "missing_coordinates"
        lat, lng = float(latRaw), float(lngRaw)
        south, north, west, east = BOUNDS
        if not (math.isfinite(lat) and math.isfinite(lng) and south <= lat <= north
                and west <= lng <= east):
            raise ValueError
        return lat, lng, None
    except (TypeError, ValueError):
        return None, None, "invalid_coordinates"


def parseOptionalBoolean(value):
    return {"true": True, "false": False}.get((value or "").lower())


def positiveMinutes(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def parseOperationStatus(value):
    return value if value in {"operating", "closed", "suspended"} else None


def serviceFlag(work, token):
    # Explicit positive/negative only. Absence of a keyword is unknown.
    for value in work:
        if value in {f"{token}취급중단", f"{token}취급안함", f"{token}미취급"}:
            return False
    if f"{token}취급" in work:
        return True
    return None


def normalizeLocation(record):
    source, row = record["source"], record["row"]
    kind = record["kind"]
    if kind == "curated":
        kind = firstNonEmpty(row, "kind")
        if not source["url"] or not source["checked_at"]:
            raise ValueError("수동 검증 자료에 source_url과 checked_at을 입력하세요.")
    if kind not in LOCATION_KINDS:
        raise ValueError("지원하지 않는 장소 kind")
    name = firstNonEmpty(row, "name", "역이름", "역사명", "관광명소명", "시설명", "title")
    if not name:
        raise ValueError("장소명 누락")
    if kind == "station":
        name = re.sub(r"\s+역$", "역", name)
    address = canonicalAddress(firstNonEmpty(row, "address", "도로명주소", "소재지도로명주소",
                                      "주소", "역사도로명주소", "addr1",
                                      "지번주소", "소재지지번주소"))
    alternate = canonicalAddress(firstNonEmpty(row, "지번주소", "소재지지번주소"))
    lat, lng, coordinateIssue = coordinatePair(
        firstNonEmpty(row, "latitude", "위도", "위도좌표", "역위도", "mapy"),
        firstNonEmpty(row, "longitude", "경도", "경도좌표", "역경도", "mapx"),
    )
    work = [normalizedNameKey(value) for value in (firstNonEmpty(row, "취급업무") or "").split(",")]
    status = parseOperationStatus(firstNonEmpty(row, "operation_status"))
    passenger = parseOptionalBoolean(firstNonEmpty(row, "passenger_handling"))
    freight = parseOptionalBoolean(firstNonEmpty(row, "freight_handling"))
    lineNames = sorted({value for token in re.split(r"[,;]", firstNonEmpty(
        row, "railway_lines", "관련노선", "노선명") or "") if (value := clean(token))})
    subtype = firstNonEmpty(row, "place_type") or {
        "station": "railway_station",
        "tourism": TOURAPI_PLACE_TYPES.get(firstNonEmpty(row, "contenttypeid"), "attraction"),
        "transport_hub": "passenger_terminal",
    }[kind]
    explicitId = firstNonEmpty(row, "source_id", "역번호", "contentid")
    sourceRef = stableId("source", source["dataset"], source.get("url") or "",
                            source["file"], explicitId or str(source["record_number"]))
    return {
        "name": name, "kind": kind, "place_type": subtype,
        "address": address, "alternate_address": alternate if alternate != address else None,
        "latitude": lat, "longitude": lng, "coordinate_issue": coordinateIssue,
        "description": firstNonEmpty(row, "description", "장소설명", "설명", "overview"),
        "visit_allowed": parseOptionalBoolean(firstNonEmpty(row, "visit_allowed")),
        "observed_stay_minutes": positiveMinutes(firstNonEmpty(row, "observed_stay_minutes", "체류시간(분)")),
        "operation_status": status,
        "passenger_handling": passenger if passenger is not None else serviceFlag(work, "여객"),
        "freight_handling": freight if freight is not None else serviceFlag(work, "화물"),
        "railway_lines": lineNames if kind == "station" else [],
        "amenities": [value for token in (firstNonEmpty(row, "amenities") or "").split(";")
                      if (value := clean(token))],
        "operator": firstNonEmpty(row, "운영주체"),
        "facility_category": firstNonEmpty(row, "구분"),
        "source": {**source, "source_id": explicitId, "source_ref": sourceRef},
    }


def closeCoordinates(left, right, meters=100):
    if left["latitude"] is None or right["latitude"] is None:
        return False
    lat = (left["latitude"] + right["latitude"]) / 2
    return math.hypot(
        (left["latitude"] - right["latitude"]) * 111_200,
        (left["longitude"] - right["longitude"]) * 111_200 * math.cos(math.radians(lat)),
    ) <= meters


def samePlace(left, right):
    leftSource, rightSource = left["source"], right["source"]
    if leftSource["dataset"] == rightSource["dataset"] and leftSource["source_id"] and rightSource["source_id"]:
        return leftSource["source_id"] == rightSource["source_id"]
    addresses = {left["address"], left["alternate_address"]} - {None}
    return bool(addresses & {right["address"], right["alternate_address"]}) or closeCoordinates(left, right)


def mergeLocations(records):
    groups = defaultdict(list)
    issues = []
    for record in records:
        try:
            place = normalizeLocation(record)
        except ValueError as exc:
            issues.append({"code": "rejected_record", "reason": str(exc), "source": record["source"]})
            continue
        if place["coordinate_issue"] == "invalid_coordinates":
            issues.append({"code": "invalid_input_coordinates", "name": place["name"],
                           "source": place["source"]})
        groups[place["kind"], normalizedNameKey(place["name"])].append(place)
    result = []
    for (kind, normalizedName), candidates in sorted(groups.items()):
        candidates.sort(key=lambda item: (
            PRIORITY.get(item["source"]["dataset"], 9),
            item["source"].get("file") or "",
            item["source"].get("record_number") or 0,
        ))
        clusters = []
        for candidate in candidates:
            matches = [
                group for group in clusters
                if any(samePlace(candidate, existingPlace) for existingPlace in group)
            ]
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
            if sum(row["address"] == anchor for row in candidates) > len(cluster):
                providerKey = primary["source"]["source_id"] or primary["source"]["source_ref"]
                anchor = f"{anchor}/{providerKey}"
            if not anchor:
                anchor = repr((primary["latitude"], primary["longitude"]))
                if primary["latitude"] is None:
                    anchor = f"{primary['source']['file']}:{primary['source']['record_number']}"
            key = stableId("place", kind, normalizedName, anchor)
            merged = {"id": key, "name": primary["name"], "kind": kind,
                      "place_type": primary["place_type"], "crs": "EPSG:4326"}
            fieldSources = {field: [primary["source"]["source_ref"]]
                             for field in ("name", "kind", "place_type")}
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
                if merged[field] is not None:
                    fieldSources[field] = [item["source"]["source_ref"] for item in cluster
                                            if item[field] == merged[field]]
            coordinates = [item for item in cluster if item["latitude"] is not None]
            coordinate = coordinates[0] if coordinates else None
            if coordinate and any(not closeCoordinates(coordinate, item) for item in coordinates[1:]):
                issues.append({"code": "coordinate_conflict", "id": key, "name": primary["name"]})
                coordinate = None
            merged["latitude"] = coordinate["latitude"] if coordinate else None
            merged["longitude"] = coordinate["longitude"] if coordinate else None
            if coordinate:
                fieldSources["latitude"] = fieldSources["longitude"] = [coordinate["source"]["source_ref"]]
            merged["coordinate_ready"] = coordinate is not None
            merged["routing_ready"] = coordinate is not None and merged["visit_allowed"] is True
            merged["default_stay_minutes"] = DEFAULT_STAY.get(primary["place_type"], DEFAULT_STAY[kind])
            merged["railway_lines"] = sorted({line for row in cluster for line in row["railway_lines"]})
            merged["railway_line_sources"] = {
                line: [item["source"]["source_ref"] for item in cluster if line in item["railway_lines"]]
                for line in merged["railway_lines"]
            }
            merged["amenities"] = sorted({amenity for item in cluster for amenity in item["amenities"]})
            if merged["amenities"]:
                fieldSources["amenities"] = [item["source"]["source_ref"] for item in cluster
                                               if item["amenities"]]
            merged["field_sources"] = fieldSources
            merged["sources"] = [row["source"] for row in cluster]
            result.append(merged)
    return result, issues


def tourapiRecords(snapshots):
    records = []
    for snapshot in snapshots:
        for index, row in enumerate(snapshot["items"], start=1):
            records.append({
                "kind": "tourism",
                "row": row,
                "source": {"dataset": "tourapi", "file": "tourapi.json", "record_number": index,
                           "url": snapshot["source_url"], "record_date": isoDate(row.get("modifiedtime")),
                           "snapshot_date": isoDate(snapshot["fetched_at"]),
                           "checked_at": None,
                           "archive_member": snapshot.get("archive_member"),
                           "fetched_at": snapshot["fetched_at"]},
            })
    return records


def mergeTagoStationCodes(places, catalog):
    byName = defaultdict(list)
    for place in places:
        if place["kind"] == "station":
            byName[normalizedNameKey(place["name"].removesuffix("역"))].append(place)
    additions, issues = [], []
    for code, (name, source) in catalog.items():
        name = name.removesuffix("역")
        record = {"kind": "station", "row": {"name": name + "역", "source_id": code},
                  "source": source}
        matches = byName[normalizedNameKey(name)]
        if len(matches) > 1:
            issues.append({"code": "ambiguous_tago_station", "name": name, "source": source})
            continue
        if matches:
            place = matches[0]
            origin = normalizeLocation(record)["source"]
            place["sources"].append(origin)
        else:
            newPlaces, rejected = mergeLocations([record])
            issues.extend(rejected)
            if not newPlaces:
                continue
            place = newPlaces[0]
            origin = place["sources"][0]
            additions.append(place)
        place["tago_station_id"] = code
        place["field_sources"]["tago_station_id"] = [origin["source_ref"]]
    return additions, issues


def parseTrainTimestamp(value):
    if value is None:
        return None
    try:
        text = str(value)
        if not re.fullmatch(r"[0-9]{14}", text):
            return None
        return datetime.strptime(text, "%Y%m%d%H%M%S").isoformat() + "+09:00"
    except ValueError:
        return None


def trainRecords(snapshots, stations, tagoIndex):
    trains, issues = {}, []
    byName = defaultdict(list)
    for station in stations:
        byName[normalizedNameKey(station["name"].removesuffix("역"))].append(station["id"])

    def matchLocal(tagoId, name):
        officialName = tagoIndex.get(tagoId)
        stationNameKey = normalizedNameKey(name.removesuffix("역"))
        if not officialName or normalizedNameKey(officialName.removesuffix("역")) != stationNameKey:
            return None
        matches = byName[stationNameKey]
        return matches[0] if len(matches) == 1 else None

    for snapshot in snapshots:
        params = snapshot["parameters"]
        expectedDay = isoDate(params["depPlandTime"])
        depId, arrId = params["depPlaceId"], params["arrPlaceId"]
        for content in snapshot["items"]:
            row = {key.lower(): value for key, value in content.items()}
            departure, arrival = firstNonEmpty(row, "depplacename"), firstNonEmpty(row, "arrplacename")
            depAt, arrAt = parseTrainTimestamp(row.get("depplandtime")), parseTrainTimestamp(row.get("arrplandtime"))
            number, grade = firstNonEmpty(row, "trainno"), firstNonEmpty(row, "traingradename")
            invalid = (not all((departure, arrival, depAt, arrAt, expectedDay)) or
                       (depAt is not None and depAt[:10] != expectedDay) or
                       (depAt is not None and arrAt is not None and depAt > arrAt))
            if invalid:
                issues.append({"code": "invalid_train_segment", "train_number": number,
                               "departure_station_id": depId, "arrival_station_id": arrId,
                               "date": expectedDay})
                continue
            key = stableId("train", depAt, arrAt, depId, arrId, grade or "", number or "")
            trains[key] = {
                "id": key, "train_number": number, "train_type": grade, "service_date": expectedDay,
                "service_kind": "passenger", "route_scope": "queried_segment", "full_route": None,
                "departure": {"station_name": departure, "tago_station_id": depId,
                              "location_id": matchLocal(depId, departure), "departure_at": depAt},
                "arrival": {"station_name": arrival, "tago_station_id": arrId,
                            "location_id": matchLocal(arrId, arrival), "arrival_at": arrAt},
                "stops": [{"tago_station_id": depId, "station_name": departure, "departure_at": depAt},
                          {"tago_station_id": arrId, "station_name": arrival, "arrival_at": arrAt}],
                "intermediate_stops_available": False,
                "source": {"dataset": "tago", "source_ref": stableId("source", "tago", key),
                           "url": snapshot["source_url"], "fetched_at": snapshot["fetched_at"],
                           "archive_member": snapshot.get("archive_member"),
                           "endpoint": snapshot["endpoint"]},
            }
    return list(trains.values()), issues


def railwayLines(stations):
    lines = {}
    for station in stations:
        for line in station["railway_lines"]:
            entry = lines.setdefault(line, {
                "id": stableId("rail", line),
                "name": line,
                "kind": "train_service" if "셔틀" in line or "(고속)" in line else "railway_line",
                "station_ids": [],
                "station_order_verified": False,
                "geometry": None,
                "sources": [],
            })
            entry["station_ids"].append(station["id"])
            sourceRefs = station["railway_line_sources"][line]
            entry["sources"].extend(s for s in station["sources"]
                                    if s["source_ref"] in sourceRefs and s not in entry["sources"])
    return list(lines.values())
