import argparse

import httpx

from backend.app.config import DATA
from backend.app.services.public_data import DataApiError, PublicData, write_json

ENDPOINT = "getDonghaeTrafficFacilityInfoList"


def fetch(client, params):
    records = client.rows(ENDPOINT, params)
    if any(not item.get("GRID_ID") for item in records):
        raise DataApiError("교통시설 응답에 격자 ID가 없습니다.")
    return client.snapshot(ENDPOINT, params, records)


def main():
    parser = argparse.ArgumentParser(description="동해시 100m 교통시설 격자 지표 수집")
    query = parser.add_mutually_exclusive_group(required=True)
    query.add_argument("--grid-id")
    query.add_argument("--dong-name")
    parser.add_argument("--max-calls", type=int, default=10, choices=range(1, 101), metavar="1..100")
    args = parser.parse_args()
    params = {"GRID_ID": args.grid_id} if args.grid_id else {"DONG_NM": args.dong_name}
    try:
        with httpx.Client(timeout=20) as session:
            snapshot = fetch(PublicData("donghae", session, args.max_calls), params)
        write_json(DATA / "raw" / "donghae_traffic_grid.json", snapshot)
        print(len(snapshot["items"]))
    except DataApiError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
