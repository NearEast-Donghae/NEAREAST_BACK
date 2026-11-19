import argparse
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
SOURCES = (
    ("family", "패밀리사이트", "15099922", "directory", False),
    ("tourism", "관광명소현황", "15029894", "tourism", True),
    ("terminals", "여객터미널정보", "15040144", "transport_hub", True),
    ("kr_stations", "철도역정보", "15067652", "station", False),
    ("korail_donghae", "동해선_역위치정보", None, "station", False),
    ("verified_places", "verified_places", None, "curated", False),
)

COLUMNS = {
    "family": ("명칭", "주소", "분류"),
    "tourism": ("관광명소명", "위도", "경도"),
    "terminals": ("시설명", "시군구명", "위도", "경도"),
    "kr_stations": ("역이름", "주소", "위도좌표", "경도좌표"),
    "korail_donghae": ("역사명", "역사도로명주소", "역위도", "역경도"),
    "verified_places": ("name", "kind", "address", "source_url", "checked_at"),
}


def clean(value):
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split()) or None


def readCsv(path):
    for encoding in ("utf-8-sig", "cp949"):
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
            return frame.rename(columns=clean), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("지원 인코딩으로 읽을 수 없음")


def isoDate(value):
    value = clean(value)
    if not value:
        return None
    stamp = re.match(r"^(\d{4})[-.]?(\d{2})[-.]?(\d{2})(?:$|\D|\d{6}$)", value)
    if not stamp:
        return None
    try:
        return date(*(int(part) for part in stamp.groups())).isoformat()
    except ValueError:
        return None


def isDonghae(address):
    return bool(re.match(r"^(?:(?:강원도|강원특별자치도|강원)\s+)?동해시(?:\s|$)", address or ""))


def firstNonEmpty(row, *names):
    return next((value for name in names if (value := clean(row.get(name)))), None)


def sourceInfo(key, catalog, filename, row, line, dateFromFilename):
    stamp = firstNonEmpty(row, "기준일자", "데이터기준일자", "입력일", "source_date")
    return {
        "dataset": key,
        "file": filename,
        "record_number": line,
        "url": f"https://www.data.go.kr/data/{catalog}/fileData.do" if catalog else firstNonEmpty(row, "source_url"),
        "record_date": isoDate(stamp),
        "snapshot_date": dateFromFilename,
        "checked_at": isoDate(firstNonEmpty(row, "checked_at")),
    }


def collectCsv(raw=RAW):
    records, inventory = [], []
    for path in sorted(raw.glob("*.csv")):
        name = re.sub(r"\s+", "", unicodedata.normalize("NFC", path.stem))
        source = next((item for item in SOURCES if item[1] in name), None)
        entry = {"file": path.name, "included": 0, "out_of_scope": 0}
        inventory.append(entry)
        try:
            frame, encoding = readCsv(path)
        except (ValueError, OSError, pd.errors.ParserError) as exc:
            entry.update(status="error", error=str(exc))
            continue
        entry.update(encoding=encoding, columns=frame.columns.tolist(), rows=len(frame))
        if not source:
            entry["status"] = "unrecognized"
            continue
        key, _, catalog, kind, local = source
        entry["dataset"] = key
        missing = sorted(set(COLUMNS[key]) - set(frame.columns))
        if missing:
            entry.update(status="error", missing_columns=missing)
            continue
        if kind == "directory":
            entry["status"] = "website_directory"
            continue
        stamp = re.search(r"(\d{8})$", name)
        snapshotDate = isoDate(stamp[1]) if stamp else None
        for index, row in enumerate(frame.to_dict("records"), start=1):
            addresses = {
                firstNonEmpty(row, column) for column in (
                    "도로명주소", "소재지도로명주소", "주소", "역사도로명주소",
                    "지번주소", "소재지지번주소", "address"
                )
            } - {None}
            explicitCity = firstNonEmpty(row, "시군구명")
            localRow = explicitCity == "동해시" if explicitCity else (
                any(map(isDonghae, addresses)) if addresses else local
            )
            if not localRow:
                entry["out_of_scope"] += 1
                continue
            records.append({
                "kind": kind,
                "row": row,
                "source": sourceInfo(key, catalog, path.name, row, index, snapshotDate),
            })
            entry["included"] += 1
        entry["status"] = "loaded"
    return records, inventory


def main():
    parser = argparse.ArgumentParser(description="원본 CSV 인코딩·컬럼·지역별 건수 확인")
    parser.add_argument("--raw", type=Path, default=RAW)
    args = parser.parse_args()
    _, inventory = collectCsv(args.raw)
    print(json.dumps(inventory, ensure_ascii=False, indent=2))
    return int(any(row["status"] == "error" for row in inventory))


if __name__ == "__main__":
    raise SystemExit(main())
