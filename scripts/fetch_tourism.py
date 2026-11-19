import argparse

import httpx

from backend.app.config import DATA
from backend.app.services.public_data import DataApiError, PublicData, write_json

ENDPOINT = "areaBasedList2"
FIELDS = ("contentid", "title", "addr1", "addr2", "mapx", "mapy", "modifiedtime",
          "contenttypeid", "ldongregncd", "ldongsigngucd")


def fetch(client):
    snapshots = []
    for kind in ("12", "14"):
        params = {"lDongRegnCd": "51", "lDongSignguCd": "170", "contentTypeId": kind,
                  "MobileOS": "ETC", "MobileApp": "NEAREAST", "arrange": "A"}
        items = client.rows(ENDPOINT, params)
        records = [{key: value for key, value in ((k.lower(), v) for k, v in item.items())
                    if key in FIELDS} for item in items]
        snapshots.append(client.snapshot(ENDPOINT, params, records))
    return snapshots


def main():
    parser = argparse.ArgumentParser(description="동해시 관광지·문화시설 수집")
    parser.add_argument("--max-calls", type=int, default=10, choices=range(1, 101), metavar="1..100")
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=20) as session:
            snapshots = fetch(PublicData("tourapi", session, args.max_calls))
        write_json(DATA / "raw" / "tourapi.json", snapshots)
        print(sum(len(snapshot["items"]) for snapshot in snapshots))
    except DataApiError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
