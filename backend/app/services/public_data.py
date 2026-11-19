import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from tempfile import NamedTemporaryFile
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx

from backend.app.config import DATA, ROOT

KST = timezone(timedelta(hours=9))
SERVICES = {
    "tago": ("TAGO_SERVICE_KEY", 10_000, "https://apis.data.go.kr/1613000/TrainInfo", "15098552"),
    "tourapi": ("TOURAPI_SERVICE_KEY", 1_000, "https://apis.data.go.kr/B551011/KorService2", "15101578"),
    "donghae": ("DONGHAE_SERVICE_KEY", 5_000,
                "https://apis.data.go.kr/4210000/DonghaeTrafficFacilityInfoService", "15095898"),
}


class DataApiError(RuntimeError):
    pass


def today_kst():
    return datetime.now(KST).date().isoformat()


def service_key(variable):
    env_file = ROOT / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv
        load_dotenv(env_file)
    value = os.getenv(variable, "").strip()
    if not value:
        raise DataApiError(f"{variable} 미설정. 요청을 보내지 않았습니다.")
    return unquote(value)


def parse_page(response):
    try:
        envelope = response.json()["response"]
        code = str(envelope["header"]["resultCode"])
        if code == "03":
            return [], 0, None
        if code not in {"0", "00", "0000"}:
            safe_code = code if code.isascii() and code.isdigit() and len(code) <= 4 else "unknown"
            raise DataApiError(f"공공데이터 API 결과 코드 {safe_code}")
        body = envelope["body"]
        container = body.get("items")
        items = container.get("item", []) if isinstance(container, dict) else container
        if isinstance(items, dict):
            items = [items]
        items = [] if items in (None, "") else items
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError
        total = body.get("totalCount")
        page_size = body.get("numOfRows")
        return items, int(total) if total is not None else None, int(page_size) if page_size else None
    except (KeyError, TypeError, ValueError, AttributeError):
        try:
            code = ElementTree.fromstring(response.text).findtext(".//returnReasonCode")
        except ElementTree.ParseError:
            code = None
        if code and code.isascii() and code.isdigit() and len(code) <= 4:
            raise DataApiError(f"공공데이터 게이트웨이 오류 {code}") from None
        raise DataApiError("공공데이터 API 응답 형식을 확인할 수 없습니다.") from None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
        from pathlib import Path

        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class PublicData:
    def __init__(self, service, client, max_calls=10, ledger=DATA / "raw" / "api_usage.sqlite3"):
        variable, provider_limit, self.base, catalog = SERVICES[service]
        self.key = service_key(variable)
        self.service = service
        self.catalog = f"https://www.data.go.kr/data/{catalog}/openapi.do"
        self.client = client
        self.max_calls = max_calls
        self.calls = 0
        self.daily_limit = provider_limit
        self.ledger = ledger

    def _reserve_call(self):
        if self.calls >= self.max_calls:
            raise DataApiError("실행당 호출 한도에 도달했습니다. 이번 스냅샷은 저장되지 않습니다.")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS usage (service TEXT, day TEXT, "
                       "calls INTEGER, PRIMARY KEY (service, day))")
            cursor = db.execute(
                "INSERT INTO usage VALUES (?, ?, 1) ON CONFLICT(service, day) "
                "DO UPDATE SET calls = calls + 1 WHERE calls < ?",
                (self.service, today_kst(), self.daily_limit),
            )
            if not cursor.rowcount:
                raise DataApiError("이 작업 폴더의 일일 호출 한도에 도달했습니다.")
        self.calls += 1

    def rows(self, endpoint, parameters, page_size=50, format_key="_type"):
        results = []
        for page in range(1, self.max_calls + 1):
            self._reserve_call()
            try:
                response = self.client.get(
                    f"{self.base}/{endpoint}",
                    params={**parameters, "serviceKey": self.key, format_key: "json",
                            "pageNo": page, "numOfRows": page_size},
                )
            except httpx.RequestError:
                raise DataApiError(f"{self.service} 네트워크 요청 실패") from None
            if not response.is_success:
                raise DataApiError(f"{self.service} HTTP {response.status_code}")
            records, total, reported_size = parse_page(response)
            results.extend(records)
            if total is not None:
                if len(results) == total:
                    return results
                if not records or len(results) > total:
                    raise DataApiError(f"{self.service} 페이지 응답 건수가 일치하지 않습니다.")
            elif len(records) < (reported_size or page_size):
                return results
        raise DataApiError("페이지 조회가 완료되지 않아 이번 스냅샷을 저장하지 않습니다.")

    def snapshot(self, endpoint, params, items):
        return {
            "source": self.service,
            "source_url": self.catalog,
            "endpoint": endpoint,
            "parameters": params,
            "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
            "items": items,
        }
