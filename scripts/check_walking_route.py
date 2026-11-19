import argparse
import json
from dataclasses import asdict

import httpx

from backend.app.services.routing import RouteError, loadRoutePlaces
from backend.app.services.tmap import TmapWalkingRouter


def main():
    parser = argparse.ArgumentParser(description="Look up one walking route by STEP 1 place IDs.")
    parser.add_argument("originId")
    parser.add_argument("destinationId")
    args = parser.parse_args()

    try:
        places, missing = loadRoutePlaces()
    except (OSError, ValueError):
        print(json.dumps({"status": "failed", "reason": "locations_unavailable"}))
        return 1
    with httpx.Client(timeout=15) as client:
        router = TmapWalkingRouter(places, client)
        try:
            route = router.getRoute(args.originId, args.destinationId)
        except RouteError as error:
            output = {
                "status": "failed",
                "reason": error.code,
                "httpStatus": error.httpStatus,
                "apiCode": error.apiCode,
            }
        else:
            output = {"status": "success", "route": asdict(route)}
    output["missingCoordinates"] = missing
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
