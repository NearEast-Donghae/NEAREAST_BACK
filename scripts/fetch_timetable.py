"""Import Korail public run records (data.go.kr) into PostgreSQL.

What the API can and cannot give (checked 2026-11-19):
- run records (travelerTrainRunInfo2): per-station times up to yesterday, passenger stops
  only. No passing records, no freight, no train type.
- run plans (travelerTrainRunPlan2): only dates about one month ahead, origin/destination only.
So the snapshot for a Saturday is built from the last three Saturdays' records: a train seen on
all three is "runs every Saturday", strengthened when the next available Saturday plan lists it.
Passing times at 망상 are interpolated between the stops around it by track distance.
Everything is therefore `estimated`, and the snapshot says on what basis.

Raw responses are cached in data/raw/ (git-ignored): re-running makes no API call.
The key is read from NEAREAST_DATA_GO_KR_KEY (the *decoding* key) and never printed.

    python scripts/fetch_timetable.py                 # uses the cache when present
    python scripts/fetch_timetable.py --refresh       # call the API again
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
BASE = "https://apis.data.go.kr/B551457/run/v2"
KILOPOST_PAGE = ("https://www.data.go.kr/tcs/dss/selectFileDataDownload.do?publicDataPk=15153841"
                 "&publicDataDetailPk=uddi:be214132-c79e-4926-8c21-9fcd3a121ea5&fileDetailSn=1")
KILOPOST_FILE = "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId={id}&fileDetailSn={sn}&dataNm=x"

TARGET = "2026-11-21"
RECORD_DATES = ["2026-11-14", "2026-11-07", "2026-10-31"]  # base day first
PLAN_DATE = "2026-12-19"  # nearest Saturday the plan API serves
# API station name -> place id (places table). 정동진 is only needed to bracket 망상.
STATIONS = {"동해": "station_donghae", "묵호": "station_mukho", "추암": "station_chuam"}
BRACKET_STATIONS = ["정동진"]
PASSING = {"망상": "station_mangsang"}  # no records: estimated from neighbours


def ymd(date: str) -> str:
    return date.replace("-", "")


class Api:
    def __init__(self, key: str, refresh: bool):
        self.key, self.refresh, self.calls = key, refresh, 0

    def _cached(self, name: str, fetch):
        path = RAW / name
        if path.exists() and not self.refresh:
            return path.read_bytes()
        data = fetch()
        RAW.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def _get(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        for attempt in range(1, 5):  # the portal times out now and then
            self.calls += 1
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # Never echo the URL or the exception text: both can carry the key.
                reason = f"{type(exc).__name__} {getattr(exc, 'code', '')}".strip()
                if attempt == 4 or getattr(exc, "code", 500) in (400, 401, 403):
                    sys.exit(f"API 호출 실패({attempt}회 시도): {reason}")
                print(f"  재시도 {attempt}: {reason}", file=sys.stderr)
                time.sleep(2 * attempt)
            finally:
                time.sleep(0.15)

    def items(self, operation: str, **cond: str) -> list[dict]:
        """All pages of one query, cached by operation + conditions."""
        tag = hashlib.sha1(json.dumps(cond, sort_keys=True).encode()).hexdigest()[:10]
        label = "_".join(v for v in cond.values())[:40].replace("/", "-")

        def fetch() -> bytes:
            if not self.key:
                sys.exit("NEAREAST_DATA_GO_KR_KEY가 없습니다 (.env에 디코딩 키를 넣으세요).")
            rows, page = [], 1
            while True:
                query = {"serviceKey": self.key, "pageNo": page, "numOfRows": 500,
                         "returnType": "JSON", **{f"cond[{k}]": v for k, v in cond.items()}}
                body = json.loads(self._get(f"{BASE}/{operation}?" + urllib.parse.urlencode(query)))
                if "response" not in body:
                    sys.exit("API returned an unexpected response; credential-bearing payload omitted")
                body = body["response"]["body"]
                got = body["items"]["item"]
                rows += got
                if not got or len(rows) >= body["totalCount"]:
                    return json.dumps(rows, ensure_ascii=False).encode()
                page += 1

        return json.loads(self._cached(f"{operation}_{label}_{tag}.json", fetch))

    def kiloposts(self) -> dict[str, int]:
        """Station name -> kilopost (line prefix + 0.1 km) from Korail's public file."""
        def fetch() -> bytes:
            info = json.loads(self._get(KILOPOST_PAGE))["fileDataRegistVO"]
            return self._get(KILOPOST_FILE.format(id=info["atchFileId"], sn=info["fileDetailSn"]))

        text = self._cached("station_kilopost.csv", fetch).decode("utf-8-sig")
        return {row["역명"]: int(row["여객키로정"]) for row in csv.DictReader(text.splitlines())}


def hhmm(value: str | None) -> str | None:
    return value[11:16] if value else None


def minutes(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


def from_minutes(value: float) -> str:
    value = round(value)
    return f"{value // 60 % 24:02d}:{value % 60:02d}"


def typical(values: list[str | None], pick: str) -> tuple[str | None, int]:
    """Records are actual times, delays included. Across the Saturdays a train does not
    leave early, so the earliest departure is the timetable; arrivals use the middle value.
    Returns (time, spread in minutes)."""
    times = sorted((v for v in values if v), key=minutes)
    if not times:
        return None, 0
    chosen = times[0] if pick == "earliest" else times[(len(times) - 1) // 2]
    return chosen, minutes(times[-1]) - minutes(times[0])


def interpolate_passes(stops: list[dict], kilo: dict[str, int]) -> list[dict]:
    """Insert estimated passing stops for PASSING stations between the stops around them."""
    out = []
    for stop, following in zip(stops, [*stops[1:], None]):
        out.append(stop)
        if following is None:
            continue
        a, b = kilo.get(stop["name"]), kilo.get(following["name"])
        left, arrive = stop["dep"] or stop["arr"], following["arr"] or following["dep"]
        if not (a and b and left and arrive) or a // 1000 != b // 1000:  # same line only
            continue
        for name, place_id in PASSING.items():
            k = kilo.get(name)
            if k is None or k // 1000 != a // 1000 or not min(a, b) < k < max(a, b):
                continue
            ratio = abs(k - a) / abs(b - a)
            span = (minutes(arrive) - minutes(left)) % (24 * 60)
            out.append({
                "name": name, "place_id": place_id, "code": None, "arr": None, "dep": None,
                "pass": from_minutes(minutes(left) + ratio * span), "stop_type": "통과(어림)",
                "basis": f"{stop['name']} {left} 출발과 {following['name']} {arrive} 도착 사이를 "
                         f"거리 비율({ratio:.2f})로 어림",
            })
    return out


def build(api: Api) -> tuple[dict, dict]:
    names = [*STATIONS, *BRACKET_STATIONS]
    by_date: dict[str, dict[str, dict[str, dict]]] = {}  # date -> station -> train no -> row
    for date in RECORD_DATES:
        by_date[date] = {}
        for name in names:
            rows = api.items("travelerTrainRunInfo2", **{"run_ymd::EQ": ymd(date), "stn_nm::EQ": name})
            by_date[date][name] = {r["trn_no"]: r for r in rows}

    base = RECORD_DATES[0]
    train_numbers = sorted({no for name in STATIONS for no in by_date[base][name]})
    plan = {r["trn_no"]: r for r in api.items("travelerTrainRunPlan2", **{"run_ymd::EQ": ymd(PLAN_DATE)})}
    kilo = api.kiloposts()
    place_ids = {**STATIONS, **PASSING}

    trains = []
    for number in train_numbers:
        route = api.items("travelerTrainRunInfo2", **{"run_ymd::EQ": ymd(base), "trn_no::EQ": number})
        route.sort(key=lambda r: int(r["trn_run_sn"]))
        stops = [{
            "name": r["stn_nm"], "place_id": place_ids.get(r["stn_nm"]), "code": r["stn_cd"],
            "arr": hhmm(r["trn_arvl_dt"]), "dep": hhmm(r["trn_dptre_dt"]),
            "stop_type": r["stop_se_nm"], "line": r["mrnt_nm"],
        } for r in route]
        ran = [d for d in RECORD_DATES
               if any(number in by_date[d][name] for name in STATIONS)]
        spread = 0
        for stop in stops:  # stations we have three weeks of: use the typical time
            weeks = [by_date[d][stop["name"]][number] for d in RECORD_DATES
                     if stop["name"] in by_date[d] and number in by_date[d][stop["name"]]]
            if not weeks:
                continue  # other stations keep the base day's actual record
            stop["arr"], arr_spread = typical([hhmm(w["trn_arvl_dt"]) for w in weeks], "middle")
            stop["dep"], dep_spread = typical([hhmm(w["trn_dptre_dt"]) for w in weeks], "earliest")
            if stop["arr"] and stop["dep"] and minutes(stop["dep"]) < minutes(stop["arr"]):
                stop["arr"] = stop["dep"]  # values from different weeks must not cross
            stop["weeks"] = len(weeks)
            stop["spread_min"] = max(arr_spread, dep_spread)
            if stop["name"] in STATIONS:
                spread = max(spread, stop["spread_min"])
        own = next(r for r in route if r["stn_nm"] in STATIONS)
        trains.append({
            "no": str(int(number)),
            "line": own["mrnt_nm"],
            "direction": own["uppln_dn_se_cd"],  # U 상행 / D 하행
            "origin": route[0]["stn_nm"],
            "destination": route[-1]["stn_nm"],
            "saturdays_run": sorted(ran),
            "weekly_saturday": len(ran) == len(RECORD_DATES),
            "spread_min": spread,  # largest week-to-week difference at our stations
            "in_plan": number in plan,
            "stops": interpolate_passes(stops, kilo),
        })

    seen_other_weeks = {no for d in RECORD_DATES[1:] for name in STATIONS for no in by_date[d][name]}
    snapshot = {
        "meta": {
            "target_date": TARGET,
            "certainty": "estimated",
            "basis": {
                "record_dates": RECORD_DATES,
                "plan_date": PLAN_DATE,
                "summary": "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준",
                "weekly_saturday": "세 토요일 모두 운행한 열차",
                "times": "운행 기록은 지연이 섞인 실제 시각이라, 동해·묵호·추암·정동진은 3주 가운데 "
                         "출발은 가장 이른 시각, 도착은 가운데 값을 쓴다. 다른 역은 11/14 실제 기록",
                "in_plan": f"{PLAN_DATE}(토) 운행계획에도 있는 열차",
                "passing": "통과 시각은 앞뒤 정차역 시각을 선로 거리 비율로 어림",
            },
            "limits": [
                f"{TARGET} 당일 운행 여부는 공공데이터로 확인할 수 없음(운행계획은 약 한 달 뒤 날짜만 제공)",
                "여객열차의 정차 기록만 있음: 화물열차와 통과 기록은 공식 데이터 없음",
                "열차 종류는 데이터에 없음: DB에 별도 확인하여 등록한 종류만 표시",
            ],
            "source": "공공데이터포털 한국철도공사_열차운행정보(운행정보·운행계획), 철도운영정보_역키로정내역",
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "not_on_base_day": sorted(str(int(n)) for n in seen_other_weeks - set(train_numbers)),
        },
        "stations": {
            **{place: {"name": name, "code": next(iter(by_date[base][name].values()))["stn_cd"],
                       "records": True} for name, place in STATIONS.items()},
            **{place: {"name": name, "code": None, "records": False} for name, place in PASSING.items()},
        },
        "trains": trains,
    }
    table = {t["no"]: {"type": "", "line": t["line"], "route": f"{t['origin']}→{t['destination']}",
                       "at": next(f"{s['name']} {s['dep'] or s['arr']}" for s in t["stops"]
                                  if s["name"] in STATIONS)}
             for t in trains}
    return snapshot, table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--refresh", action="store_true", help="ignore data/raw and call the API")
    args = parser.parse_args(argv)
    load_dotenv(args.env_file, override=False)
    api = Api(os.environ.get("NEAREAST_DATA_GO_KR_KEY", ""), args.refresh)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from neareast_api.core.config import Settings
    from neareast_api.db.url import build_database_url
    from neareast_api.ingestion.service import IngestionService

    snapshot, _ = build(api)
    engine = create_engine(build_database_url(Settings.from_env()))
    try:
        with Session(engine) as session:
            IngestionService.importTimetable(session, snapshot)
    finally:
        engine.dispose()
    print(f"Imported {len(snapshot['trains'])} train services into PostgreSQL")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
