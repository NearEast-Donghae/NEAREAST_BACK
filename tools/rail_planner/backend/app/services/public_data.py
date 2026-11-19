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
    def __init__(self, reason, *, httpStatus=None, apiCode=None, responseFormat=None):
        self.reason = reason
        self.httpStatus = httpStatus
        self.apiCode = apiCode
        self.responseFormat = responseFormat
        parts = [reason]
        if httpStatus is not None:
            parts.append(f"HTTP {httpStatus}")
        if apiCode is not None:
            parts.append(f"API 오류 {apiCode}")
        super().__init__(" / ".join(parts))


@dataclass
class ApiPage:
    rows: list
    total: int | None
    pageSize: int | None
    responseFormat: str
    apiCode: str
    httpStatus: int


# HTTPX's request summary includes the full URL and query string.
logging.getLogger("httpx").addFilter(lambda record: "servicekey=" not in record.getMessage().lower())


def todayKst():
    return datetime.now(KST).date().isoformat()


def readSettings(settingNames=SETTINGS):
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
        if name not in settingNames:
            continue
        try:
            tokens = shlex.split(raw, comments=True)
        except ValueError:
            raise DataApiError(f"{name} 설정 형식 오류") from None
        if len(tokens) > 1:
            raise DataApiError(f"{name} 설정 형식 오류")
        values[name] = tokens[0] if tokens else ""
    return {name: os.environ.get(name, values.get(name, "")).strip() for name in settingNames}


def readResponseEnvelope(response, responseFormat):
    if responseFormat == "xml":
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


def readPage(response):
    content = response.content.removeprefix(b"\xef\xbb\xbf").lstrip()
    responseFormat = "xml" if content.startswith(b"<") else "json" if content.startswith(b"{") else None
    if content[:20].lower().startswith((b"<!doctype html", b"<html")):
        responseFormat = "html"
    context = {"httpStatus": response.status_code, "responseFormat": responseFormat}
    try:
        code, body = readResponseEnvelope(response, responseFormat)
    except (ValueError, TypeError, AttributeError, ElementTree.ParseError):
        if not response.is_success:
            raise DataApiError("HTTP response error", **context) from None
        raise DataApiError("Unrecognized API response format", **context) from None
    if code is not None:
        code = str(code)
        code = code if re.fullmatch(r"[0-9]{1,4}", code) else "unknown"
    reason = API_ERRORS.get(code.zfill(2) if code else None)
    if not response.is_success:
        raise DataApiError(reason or "HTTP response error", apiCode=code, **context)
    if code in {"3", "03", "0003"}:
        return ApiPage([], 0, None, responseFormat, code, response.status_code)
    if code not in {"0", "00", "0000"}:
        raise DataApiError(reason or "Unknown or missing API result code", apiCode=code, **context)
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
                       int(size) if size else None, responseFormat, code, response.status_code)
    except (ValueError, TypeError, AttributeError):
        raise DataApiError("Invalid API response body", apiCode=code, **context) from None


def parsePage(response):
    page = readPage(response)
    return page.rows, page.total, page.pageSize


def networkReason(error):
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


def writeJson(path, value):
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
    def __init__(self, service, client, maxCalls=10, ledger=DATA / "raw" / "api_usage.sqlite3"):
        variable, urlVariable, providerLimit, officialUrl, catalog, self.formatKey = SERVICES[service]
        settings = readSettings()
        value = settings[variable] or settings[COMMON_KEY]
        if not value:
            raise DataApiError(f"{variable} 또는 {COMMON_KEY} 미설정. 요청을 보내지 않았습니다.")
        self.key = unquote(value)
        self.base = (settings[urlVariable] or officialUrl).rstrip("/")
        if self.base != officialUrl:
            raise DataApiError(f"{urlVariable} 공식 HTTPS 서비스 주소 불일치")
        self.service = service
        self.catalog = f"https://www.data.go.kr/data/{catalog}/openapi.do"
        self.client = client
        self.maxCalls = maxCalls
        self.calls = 0
        self.dailyLimit = providerLimit
        self.ledger = ledger

    def reserveCall(self):
        if self.calls >= self.maxCalls:
            raise DataApiError("실행당 호출 한도에 도달했습니다. 이번 스냅샷은 저장되지 않습니다.")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS usage (service TEXT, day TEXT, "
                       "calls INTEGER, PRIMARY KEY (service, day))")
            cursor = db.execute(
                "INSERT INTO usage VALUES (?, ?, 1) ON CONFLICT(service, day) "
                "DO UPDATE SET calls = calls + 1 WHERE calls < ?",
                (self.service, todayKst(), self.dailyLimit),
            )
            if not cursor.rowcount:
                raise DataApiError("이 작업 폴더의 일일 호출 한도에 도달했습니다.")
        self.calls += 1

    def requestPage(self, endpoint, parameters, *, page=1, pageSize=50, responseFormat="json"):
        self.reserveCall()
        try:
            response = self.client.get(
                f"{self.base}/{endpoint}",
                params={**parameters, "serviceKey": self.key, self.formatKey: responseFormat,
                        "pageNo": page, "numOfRows": pageSize},
                follow_redirects=False,
            )
        except httpx.RequestError as error:
            raise DataApiError(networkReason(error)) from None
        return readPage(response)

    def rows(self, endpoint, parameters, pageSize=50):
        results = []
        for page in range(1, self.maxCalls + 1):
            reply = self.requestPage(endpoint, parameters, page=page, pageSize=pageSize)
            records, total, reportedSize = reply.rows, reply.total, reply.pageSize
            results.extend(records)
            if total is not None:
                if len(results) == total:
                    return results
                if not records or len(results) > total:
                    raise DataApiError(f"{self.service} 페이지 응답 건수가 일치하지 않습니다.")
            elif len(records) < (reportedSize or pageSize):
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
