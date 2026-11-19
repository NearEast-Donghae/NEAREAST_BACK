import argparse
import re
from datetime import date

import httpx

from backend.app.config import DATA
from backend.app.services.public_data import DataApiError, PublicData, write_json

ROUTES = "GetStrtpntAlocFndTrainInfo"
STATIONS = "GetCtyAcctoTrainSttnList"
CITIES = "GetCtyCodeList"


def fetch(client, endpoint, params):
    return client.snapshot(endpoint, params, client.rows(endpoint, params))


def station_id(value):
    if not re.fullmatch(r"[A-Za-z0-9]{1,24}", value):
        raise argparse.ArgumentTypeError("유효한 TAGO 역 ID를 입력하세요.")
    return value


def main():
    parser = argparse.ArgumentParser(description="TAGO 도시·역 코드 및 여객열차 조회")
    parser.add_argument("--max-calls", type=int, default=10, choices=range(1, 101), metavar="1..100")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("cities")
    stations = commands.add_parser("stations")
    stations.add_argument("city_code", type=int)
    schedule = commands.add_parser("schedule")
    schedule.add_argument("date", type=date.fromisoformat)
    schedule.add_argument("departure", type=station_id)
    schedule.add_argument("arrival", type=station_id)
    args = parser.parse_args()
    if args.command == "cities":
        endpoint, params, name = CITIES, {}, "tago_cities"
    elif args.command == "stations":
        endpoint, params, name = STATIONS, {"cityCode": args.city_code}, f"tago_stations_{args.city_code}"
    else:
        if args.arrival == args.departure:
            parser.error("출발역과 도착역은 서로 달라야 합니다.")
        day = args.date.strftime("%Y%m%d")
        endpoint = ROUTES
        params = {"depPlaceId": args.departure, "arrPlaceId": args.arrival, "depPlandTime": day}
        name = f"tago_schedule_{day}_{args.departure}_{args.arrival}"
    try:
        with httpx.Client(timeout=20) as session:
            snapshot = fetch(PublicData("tago", session, args.max_calls), endpoint, params)
        write_json(DATA / "raw" / f"{name}.json", snapshot)
        print(len(snapshot["items"]))
    except DataApiError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
