import json
import logging
import os
import re
import shlex
import socket
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from tempfile import NamedTemporaryFile
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx

from backend.app.config import DATA, ROOT

KST = timezone(timedelta(hours=9))
SERVICES = {
    "tago": ("TAGO_SERVICE_KEY", "TAGO_API_URL", 10_000,
             "https://apis.data.go.kr/1613000/TrainInfo", "15098552", "_type"),
    "tourapi": ("TOURAPI_SERVICE_KEY", "TOUR_API_URL", 1_000,
                "https://apis.data.go.kr/B551011/KorService2", "15101578", "_type"),
    "donghae": ("DONGHAE_SERVICE_KEY", "DONGHAE_API_URL", 5_000,
                "https://apis.data.go.kr/4210000/DonghaeTrafficFacilityInfoService", "15095898", "resultType"),
}
COMMON_KEY = "DATA_GO_KR_API_KEY"
SETTINGS = {COMMON_KEY} | {name for key, url, *_ in SERVICES.values() for name in (key, url)}
API_ERRORS = {
    "01": "API application error", "04": "upstream HTTP error", "05": "upstream timeout",
    "10": "invalid request parameter", "11": "required parameter missing", "12": "unknown API service",
    "20": "API authorization denied", "21": "API key temporarily disabled",
    "22": "daily quota exceeded", "23": "per-second quota exceeded",
    "30": "unregistered API key", "31": "expired API key", "32": "unregistered IP address",
}


class DataApiError(RuntimeError):
    def __init__(self, reason, *, http_status=None, api_code=None, response_format=None):
        self.reason = reason
        self.http_status = http_status
        self.api_code = api_code
        self.response_format = response_format
        parts = [reason]
        if http_status is not None:
            parts.append(f"HTTP {http_status}")
        if api_code is not None:
            parts.append(f"API 오류 {api_code}")
        super().__init__(" / ".join(parts))


@dataclass
class ApiPage:
    rows: list
    total: int | None
    page_size: int | None
    response_format: str
    api_code: str
    http_status: int


# HTTPX's request summary includes the full URL and query string.
logging.getLogger("httpx").addFilter(lambda record: "servicekey=" not in record.getMessage().lower())


def today_kst():
    return datetime.now(KST).date().isoformat()


def read_settings():
    values = {}
    try:
        lines = (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    except (OSError, UnicodeError):
        raise DataApiError(".env 파일을 읽을 수 없습니다.") from None
    for line in lines:
        name, _, raw = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if name not in SETTINGS:
            continue
        try:
            tokens = shlex.split(raw, comments=True)
        except ValueError:
            raise DataApiError(f"{name} 설정 형식 오류") from None
        if len(tokens) > 1:
            raise DataApiError(f"{name} 설정 형식 오류")
        values[name] = tokens[0] if tokens else ""
    return {name: os.environ.get(name, values.get(name, "")).strip() for name in SETTINGS}


def response_envelope(response, response_format):
    if response_format == "xml":
        root = ElementTree.fromstring(response.content)
        for element in root.iter():
            element.tag = element.tag.rsplit("}", 1)[-1]
        code = root.findtext("./header/resultCode") or root.findtext(".//returnReasonCode")
        body = root.find("body")
        return code, {
            "items": [{field.tag: field.text for field in item}
                      for item in body.findall("./items/item")],
            "totalCount": body.findtext("totalCount"),
            "numOfRows": body.findtext("numOfRows"),
        } if body is not None else None
    payload = response.json()
    envelope = payload.get("response", payload)
    header = envelope.get("header", envelope.get("cmmMsgHeader", {}))
    return header.get("resultCode", header.get("returnReasonCode")), envelope.get("body")


def read_page(response):
    content = response.content.removeprefix(b"\xef\xbb\xbf").lstrip()
    response_format = "xml" if content.startswith(b"<") else "json" if content.startswith(b"{") else None
    if content[:20].lower().startswith((b"<!doctype html", b"<html")):
        response_format = "html"
    context = {"http_status": response.status_code, "response_format": response_format}
    try:
        code, body = response_envelope(response, response_format)
    except (ValueError, TypeError, AttributeError, ElementTree.ParseError):
        if not response.is_success:
            raise DataApiError("HTTP response error", **context) from None
        raise DataApiError("Unrecognized API response format", **context) from None
    if code is not None:
        code = str(code)
        code = code if re.fullmatch(r"[0-9]{1,4}", code) else "unknown"
    reason = API_ERRORS.get(code.zfill(2) if code else None)
    if not response.is_success:
        raise DataApiError(reason or "HTTP response error", api_code=code, **context)
    if code in {"3", "03", "0003"}:
        return ApiPage([], 0, None, response_format, code, response.status_code)
    if code not in {"0", "00", "0000"}:
        raise DataApiError(reason or "Unknown or missing API result code", api_code=code, **context)
    try:
        container = body.get("items")
        items = container.get("item", []) if isinstance(container, dict) else container
        if isinstance(items, dict):
            items = [items]
        items = [] if items in (None, "") else items
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError
        total, size = body.get("totalCount"), body.get("numOfRows")
        return ApiPage(items, int(total) if total not in (None, "") else None,
                       int(size) if size else None, response_format, code, response.status_code)
    except (ValueError, TypeError, AttributeError):
        raise DataApiError("Invalid API response body", api_code=code, **context) from None


def parse_page(response):
    page = read_page(response)
    return page.rows, page.total, page.page_size


def network_reason(error):
    underlying = error
    while underlying is not None:
        if (isinstance(underlying, socket.gaierror)
                or getattr(underlying, "errno", None) in {11001, 11002}
                or "getaddrinfo failed" in str(underlying).lower()):
            return "DNS resolution failed"
        underlying = underlying.__cause__ or underlying.__context__
    if isinstance(error, httpx.ConnectTimeout):
        return "Connection establishment timed out"
    if isinstance(error, httpx.ReadTimeout):
        return "Waiting for the server response timed out"
    if isinstance(error, httpx.TimeoutException):
        return "Network request timed out"
    return f"Network request failed ({type(error).__name__})"


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
        variable, url_variable, provider_limit, official_url, catalog, self.format_key = SERVICES[service]
        settings = read_settings()
        value = settings[variable] or settings[COMMON_KEY]
        if not value:
            raise DataApiError(f"{variable} 또는 {COMMON_KEY} 미설정. 요청을 보내지 않았습니다.")
        self.key = unquote(value)
        self.base = (settings[url_variable] or official_url).rstrip("/")
        if self.base != official_url:
            raise DataApiError(f"{url_variable} 공식 HTTPS 서비스 주소 불일치")
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

    def request_page(self, endpoint, parameters, *, page=1, page_size=50, response_format="json"):
        self._reserve_call()
        try:
            response = self.client.get(
                f"{self.base}/{endpoint}",
                params={**parameters, "serviceKey": self.key, self.format_key: response_format,
                        "pageNo": page, "numOfRows": page_size},
                follow_redirects=False,
            )
        except httpx.RequestError as error:
            raise DataApiError(network_reason(error)) from None
        return read_page(response)

    def rows(self, endpoint, parameters, page_size=50):
        results = []
        for page in range(1, self.max_calls + 1):
            reply = self.request_page(endpoint, parameters, page=page, page_size=page_size)
            records, total, reported_size = reply.rows, reply.total, reply.page_size
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
