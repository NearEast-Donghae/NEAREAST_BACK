import argparse
import json
from datetime import datetime

import httpx

from backend.app.config import DATA
from backend.app.services.public_data import KST, DataApiError, PublicData, writeJson
from scripts.fetch_tourism import ENDPOINT as TOURISM
from scripts.fetch_train_data import CITIES
from scripts.fetch_transport import ENDPOINT as TRANSPORT

PROBES = (
    ("tago", CITIES, {}, "citycode"),
    ("tourapi", TOURISM, {
        "MobileOS": "ETC", "MobileApp": "NEAREAST", "lDongRegnCd": "51",
        "lDongSignguCd": "170", "contentTypeId": "12",
    }, "contentid"),
    ("donghae", TRANSPORT, {"DONG_NM": "삼화동"}, "grid_id"),
)


def check(session, probes=PROBES):
    results = []
    for service, endpoint, params, expectedField in probes:
        for requestedFormat in ("json", "xml"):
            result = {
                "service": service, "endpoint": endpoint, "requested_format": requestedFormat,
                "response_format": None, "http_status": None, "api_code": None,
                "parsed_rows": None, "status": "failed", "reason": None,
            }
            try:
                client = PublicData(service, session, maxCalls=1)
                page = client.requestPage(endpoint, params, pageSize=1, responseFormat=requestedFormat)
                result.update(
                    response_format=page.responseFormat, http_status=page.httpStatus, api_code=page.apiCode,
                    parsed_rows=len(page.rows),
                )
                if page.responseFormat != requestedFormat:
                    result["reason"] = "Expected response format did not match"
                elif any(expectedField not in {field.lower() for field in row} for row in page.rows):
                    result["reason"] = "Expected dataset field missing"
                else:
                    result["status"] = "parsed" if page.rows else "parsed_empty"
            except DataApiError as error:
                result.update(http_status=error.httpStatus, api_code=error.apiCode,
                              response_format=error.responseFormat, reason=error.reason)
            results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description="Verify the public APIs using live JSON and XML requests.")
    parser.add_argument("--service", choices=[probe[0] for probe in PROBES],
                        help="Check only the selected service.")
    parser.add_argument("--read-timeout", dest="readTimeout", type=int, default=12, metavar="SECONDS",
                        help="Server response read timeout in seconds (default: 12).")
    args = parser.parse_args()
    if args.readTimeout < 1:
        parser.error("--read-timeout must be at least 1 second.")
    probes = [probe for probe in PROBES if args.service is None or probe[0] == args.service]
    with httpx.Client(timeout=httpx.Timeout(12, read=args.readTimeout)) as session:
        attempts = check(session, probes)
    report = {
        "checked_at": datetime.now(KST).isoformat(timespec="seconds"),
        "probe_rows_saved_as_data": False,
        "attempts": attempts,
    }
    filename = f"{args.service}_connectivity.json" if args.service else "api_connectivity.json"
    writeJson(DATA / "reports" / filename, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return int(any(item["status"] == "failed" for item in attempts))


if __name__ == "__main__":
    raise SystemExit(main())
