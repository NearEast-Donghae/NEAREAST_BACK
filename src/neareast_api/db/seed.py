import gzip
import hashlib
import json
from datetime import date, datetime

from sqlalchemy import Date, DateTime, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as postgresInsert
from sqlalchemy.dialects.sqlite import insert as sqliteInsert

from neareast_api.core.geography import dateKey, validCoordinates
from neareast_api.db.metadata import metadata

PUBLIC_TABLES = frozenset({
    "places", "knowledge_documents", "knowledge_chunks", "timetable_snapshots",
    "train_services", "train_stops", "planner_datasets", "planner_locations",
    "planner_stations", "planner_transport_hubs", "planner_railway_lines",
    "planner_line_stations", "planner_train_segments", "data_reference_records",
})
MAX_BUNDLE_BYTES = 128 * 1024 * 1024


def jsonValue(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Unsupported seed value: {type(value).__name__}")


def encodePayload(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=jsonValue, allow_nan=False).encode()


def exportSeed(connection, path, includeEmbeddings=True, releaseMetadata=None):
    tables = {}
    for table in metadata.sorted_tables:
        if table.name not in PUBLIC_TABLES:
            continue
        if table.name == "knowledge_chunks" and not includeEmbeddings:
            continue
        tables[table.name] = [dict(row) for row in connection.execute(
            select(table).order_by(*table.primary_key.columns)
        ).mappings()]
    payload = {"format": "neareast-public-seed-v1", "tables": tables}
    if releaseMetadata is not None:
        payload["release"] = releaseMetadata
    checksum = hashlib.sha256(encodePayload(payload)).hexdigest()
    with gzip.open(path, "wb") as stream:
        stream.write(encodePayload({"sha256": checksum, "payload": payload}))
    return {"sha256": checksum, "counts": {name: len(rows) for name, rows in tables.items()}}


def readPayload(path):
    with gzip.open(path, "rb") as stream:
        raw = stream.read(MAX_BUNDLE_BYTES + 1)
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("Seed exceeds decompressed size limit")
    envelope = json.loads(raw)
    payload = envelope["payload"]
    if hashlib.sha256(encodePayload(payload)).hexdigest() != envelope["sha256"]:
        raise ValueError("Seed checksum mismatch")
    if payload.get("format") != "neareast-public-seed-v1":
        raise ValueError("Unsupported seed format")
    if set(payload["tables"]) - PUBLIC_TABLES:
        raise ValueError("Seed contains a non-public table")
    return payload


def readSeed(path):
    return readPayload(path)["tables"]


def importSeed(connection, path, dryRun=False):
    payload = readPayload(path)
    requiredRevision = payload.get("release", {}).get("requiredRevision")
    if requiredRevision:
        if not inspect(connection).has_table("alembic_version"):
            raise ValueError("Run database migrations before importing this seed")
        revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        if requiredRevision not in revisions:
            raise ValueError(f"Seed requires schema revision {requiredRevision}")
    tables = payload["tables"]
    report = {"counts": {name: len(rows) for name, rows in tables.items()}, "dryRun": dryRun}
    for table in metadata.sorted_tables:
        if table.name not in tables:
            continue
        primaryKeys = [column.name for column in table.primary_key]
        seen = set()
        for record in tables[table.name]:
            if table.name == "train_services" and "train_type_source" not in record:
                record = {**record, "train_type_source": None}
            if set(record) != {column.name for column in table.columns}:
                raise ValueError(f"Seed columns do not match {table.name}")
            identity = tuple(record[key] for key in primaryKeys)
            if identity in seen:
                raise ValueError(f"Duplicate seed identity in {table.name}")
            seen.add(identity)
            values = dict(record)
            for column in table.columns:
                if values[column.name] is not None:
                    if isinstance(column.type, DateTime):
                        values[column.name] = datetime.fromisoformat(values[column.name])
                    elif isinstance(column.type, Date):
                        values[column.name] = date.fromisoformat(values[column.name])
            if table.name == "knowledge_chunks" and values["embedding"] is not None:
                if len(values["embedding"]) != 1536:
                    raise ValueError("Seed embedding dimension must be 1536")
            conflictKeys = ["document_id", "position"] if table.name == "knowledge_chunks" else primaryKeys
            existing = connection.execute(select(table).where(*(
                table.c[key] == values[key] for key in conflictKeys
            ))).mappings().first()
            if existing and table.name.startswith("planner_") and "payload" in values:
                if values["payload"] != existing["payload"]:
                    raise ValueError("An immutable planner snapshot has conflicting content")
            if existing and table.name == "knowledge_documents" and existing["body"] != values["body"]:
                # Keep both sources even when an older seed used the same document ID.
                suffix = hashlib.sha256(encodePayload(dict(existing))).hexdigest()[:20]
                archive = dict(existing)
                archive["id"] = "seed-preserved:" + suffix
                insert = postgresInsert(table) if connection.dialect.name == "postgresql" else sqliteInsert(table)
                if not dryRun:
                    connection.execute(insert.values(**archive).on_conflict_do_nothing())
            if existing and table.name == "places":
                values["aliases"] = list(dict.fromkeys([*existing["aliases"], *values["aliases"]]))
                values["operating_info"] = {**existing["operating_info"], **values["operating_info"]}
                oldSelection = existing["operating_info"].get("coordinateSelection") or {}
                newSelection = record["operating_info"].get("coordinateSelection") or {}
                oldRank = (oldSelection.get("priority", 70), dateKey(oldSelection.get("referenceDate")))
                newRank = (newSelection.get("priority", 50), dateKey(newSelection.get("referenceDate")))
                if validCoordinates(existing["lat"], existing["lng"]) and (
                    not validCoordinates(values["lat"], values["lng"]) or oldRank > newRank
                ):
                    values["lat"], values["lng"] = existing["lat"], existing["lng"]
                    if "coordinateSelection" in existing["operating_info"]:
                        values["operating_info"]["coordinateSelection"] = existing["operating_info"]["coordinateSelection"]
            if dryRun:
                continue
            insert = postgresInsert(table) if connection.dialect.name == "postgresql" else sqliteInsert(table)
            statement = insert.values(**values)
            updates = {column.name: statement.excluded[column.name]
                       for column in table.columns if column.name not in primaryKeys}
            connection.execute(statement.on_conflict_do_update(index_elements=conflictKeys, set_=updates))
    return report
