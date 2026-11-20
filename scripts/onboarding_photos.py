"""Pictures for the picture-choosing onboarding: fetch the list, then load the chosen ones.

    python scripts/onboarding_photos.py fetch        # tourism photos taken in 동해시 -> review sheet
    python scripts/onboarding_photos.py load         # marked rows of the sheet -> database
    python scripts/onboarding_photos.py load --dry-run

fetch  needs a data.go.kr key with "한국관광공사_관광사진 정보_GW" (15101914) applied for:
       TOUR_API_KEY, or NEAREAST_DATA_GO_KR_KEY (environment or .env). The key is never printed.
       Writes docs/context/onboarding_photo_review.csv (not committed); what a reviewer
       already filled in is kept. The raw answer goes to data/raw/tour_photos.json.
load   reads the rows marked in the "선택" column. Any problem in a marked row stops the
       load before the database is touched. --embed also stores a vector per picture
       (OpenAI key needed; only pictures whose words changed are embedded again).
       --prune removes pictures that are no longer marked.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from neareast_api.core.config import Settings
from neareast_api.db.entities import KnowledgeChunkEntity, OnboardingPhotoEntity, PlaceEntity
from neareast_api.db.url import build_database_url
from neareast_api.modules.chat.retrieval import EMBEDDING_DIMENSIONS
from neareast_api.modules.onboarding import curation
from neareast_api.modules.places.service import Place

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "docs" / "context" / "onboarding_photo_review.csv"
RAW = ROOT / "data" / "raw" / "tour_photos.json"
ENDPOINT = "https://apis.data.go.kr/B551011/PhotoGalleryService1/gallerySearchList1"
KEYWORDS = (
    "동해시", "동해", "묵호", "추암", "촛대바위", "논골담길", "도째비골", "망상", "천곡",
    "무릉계곡", "무릉별유천지", "북평", "어달", "한섬", "대진", "묵호등대", "묵호항", "삼화사",
    "두타산", "베틀바위", "해랑전망대", "감추", "동해역", "묵호역", "추암역", "바다열차",
    "기차", "열차", "철도", "건널목", "간이역",
)
PAGE = 100


def tour_key() -> str:
    key = os.environ.get("TOUR_API_KEY") or os.environ.get("NEAREAST_DATA_GO_KR_KEY")
    if not key:
        sys.exit("키가 없어요: TOUR_API_KEY 또는 NEAREAST_DATA_GO_KR_KEY를 환경변수나 .env에 넣어 주세요.")
    return key


def search(key: str, keyword: str) -> list[dict]:
    found, page = [], 1
    while True:
        query = urllib.parse.urlencode({
            "serviceKey": key, "MobileOS": "ETC", "MobileApp": "app", "_type": "json",
            "keyword": keyword, "numOfRows": PAGE, "pageNo": page, "arrange": "A"})
        try:
            with urllib.request.urlopen(f"{ENDPOINT}?{query}", timeout=30) as response:
                text = response.read().decode("utf-8")
            body = json.loads(text)["response"]["body"]
        except urllib.error.HTTPError as exc:
            sys.exit(f"'{keyword}' 조회 실패: HTTP {exc.code}. 포털에서 이 API를 활용신청했는지 확인해 주세요.")
        except (KeyError, ValueError):
            # An error comes back as XML or as another JSON shape; the key may be in it.
            sys.exit(f"'{keyword}' 조회 실패: " + text[:300].replace(key, "***"))
        items = body.get("items") or {}
        batch = items.get("item", []) if isinstance(items, dict) else []
        batch = [batch] if isinstance(batch, dict) else batch
        found += batch
        if not batch or page * PAGE >= int(body.get("totalCount") or 0):
            return found
        page += 1
        time.sleep(0.2)


def places_of(session: Session) -> dict[str, Place]:
    return {row.id: Place(row.id, row.name, row.kind, row.passengerStop, tuple(row.aliases))
            for row in session.scalars(select(PlaceEntity))}


def read_sheet(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def fetch(session: Session, sheet: Path) -> None:
    key = tour_key()
    items: dict[str, dict] = {}
    for keyword in KEYWORDS:
        for item in search(key, keyword):
            items.setdefault(str(item.get("galContentId")), item)
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(list(items.values()), ensure_ascii=False, indent=1), "utf-8")
    rows = curation.review_rows(items.values(), places_of(session))
    if sheet.exists():
        rows = curation.merge_review(rows, read_sheet(sheet))
    sheet.parent.mkdir(parents=True, exist_ok=True)
    with sheet.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=curation.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"조회 {len(items)}장 중 촬영지역이 동해시인 사진 {len(rows)}장 → {sheet}")
    if items:
        print("응답 칸:", ", ".join(sorted(next(iter(items.values())))))


def embed(settings: Settings, texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    key = settings.openai_api_key.get_secret_value()
    if not key:
        sys.exit("--embed에는 OpenAI 키가 필요해요(NEAREAST_OPENAI_API_KEY).")
    response = OpenAI(api_key=key).embeddings.create(
        model=settings.embedding_model, input=texts, dimensions=EMBEDDING_DIMENSIONS)
    return [row.embedding for row in sorted(response.data, key=lambda row: row.index)]


def load(session: Session, settings: Settings, sheet: Path, *, dry_run: bool, prune: bool,
         with_vectors: bool) -> None:
    if not sheet.exists():
        sys.exit(f"검토 목록이 없어요: {sheet} (먼저 fetch)")
    places = places_of(session)
    records, problems = curation.parse(read_sheet(sheet), places)
    if problems:
        sys.exit("고칠 곳이 있어서 아무것도 넣지 않았어요:\n  " + "\n  ".join(problems))
    existing = {row.id: row for row in session.scalars(select(OnboardingPhotoEntity))}
    stale = []
    for record in records:
        row = existing.get(record.id)
        if row is None:
            row = OnboardingPhotoEntity(id=record.id)
            session.add(row)
        row.title, row.imageUrl = record.title, record.image_url
        row.photographer, row.shotMonth = record.photographer, record.shot_month
        row.location, row.source, row.license = record.location, curation.SOURCE, record.license
        row.placeId, row.viewStationId = record.place_id, record.view_station_id
        row.moods, row.keywords = list(record.moods), list(record.keywords)
        row.description, row.position = record.description, record.position
        digest = curation.content_hash(record, places)
        if (row.contentHash != digest or row.embedding is None
                or row.embeddingModel != settings.embedding_model):
            stale.append((row, record))
            if row.contentHash != digest:
                row.embedding, row.embeddingModel = None, None  # a vector of older words
        row.contentHash = digest
    removed = [row for photo_id, row in existing.items()
               if photo_id not in {r.id for r in records}]
    if prune:
        for row in removed:
            session.delete(row)
    embedded = 0
    if with_vectors:
        # Vectors of two models cannot be compared: the pictures must use the model the
        # knowledge documents were embedded with, or the likeness step would stay off.
        used = set(session.scalars(select(KnowledgeChunkEntity.embeddingModel).where(
            KnowledgeChunkEntity.embedding.is_not(None)).distinct()))
        if used and used != {settings.embedding_model}:
            sys.exit(f"지식 문서는 {', '.join(sorted(used))}로 임베딩돼 있는데 설정은 "
                     f"{settings.embedding_model}이에요. NEAREAST_EMBEDDING_MODEL을 맞춘 뒤 다시 실행해 주세요.")
    if with_vectors and stale and not dry_run:
        vectors = embed(settings, [curation.embedding_text(r, places) for _, r in stale])
        for (row, _), vector in zip(stale, vectors, strict=True):
            row.embedding, row.embeddingModel = vector, settings.embedding_model
        embedded = len(vectors)
    print(json.dumps({
        "marked": len(records), "new": sum(1 for r in records if r.id not in existing),
        "removed" if prune else "no_longer_marked_but_kept": len(removed),
        "embedded": embedded, "without_vector": len(stale) - embedded,
        "dry_run": dry_run}, ensure_ascii=False))
    if dry_run:
        session.rollback()
    else:
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("operation", choices=("fetch", "load"))
    parser.add_argument("--sheet", type=Path, default=SHEET)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--embed", action="store_true")
    arguments = parser.parse_args()
    load_dotenv(ROOT / ".env")
    settings = Settings.from_env()
    engine = create_engine(build_database_url(settings))
    try:
        with Session(engine) as session:
            if arguments.operation == "fetch":
                fetch(session, arguments.sheet)
            else:
                load(session, settings, arguments.sheet, dry_run=arguments.dry_run,
                     prune=arguments.prune, with_vectors=arguments.embed)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
