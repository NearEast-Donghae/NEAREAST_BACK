"""From the tourism photo list to the pictures on the onboarding board.

A person chooses the pictures: the list is written to a review sheet (CSV), someone
marks the ones to use and corrects the suggested moods and place, and only marked rows
are loaded. The suggestions here read the title and keywords of a picture, never the
picture itself, and the sheet says so.
"""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from neareast_api.modules.onboarding.moods import LABEL_TO_ID, MOODS
from neareast_api.modules.places.service import Place

SOURCE = "한국관광공사 포토코리아"
# The portal describes every picture of this API as KOGL type 1 (attribution). The API
# has no licence field per picture, so the sheet carries that statement for a person to
# confirm; a row whose licence cell says anything else is refused at load time.
LICENSE = "공공누리 제1유형"
ALLOWED_LICENSES = ("제1유형", "제0유형")
AREA = "동해시"

COLUMNS = ("사진번호", "제목", "촬영자", "촬영연월", "촬영지역", "라이선스", "원본 링크", "키워드",
           "분위기 제안", "장소id 제안", "선택", "분위기", "장소id", "열차역id", "설명")
# What the reviewer fills in; kept when the list is fetched again.
REVIEW_COLUMNS = ("선택", "분위기", "장소id", "열차역id", "설명")

MOOD_WORDS: dict[str, tuple[str, ...]] = {
    "sea_train": ("바다열차", "해변열차", "해안선 열차"),
    "train": ("열차", "기차", "철도", "선로", "철길", "레일", "KTX", "건널목"),
    "station": ("동해역", "묵호역", "추암역", "망상역", "간이역", "승강장", "플랫폼", "역사(驛舍)"),
    "sunrise": ("일출", "해돋이", "해맞이", "여명", "일몰", "노을", "석양"),
    "sea": ("바다", "해변", "해수욕장", "해안", "파도", "항구", "등대", "촛대바위", "방파제"),
    "retro": ("논골담", "벽화", "골목", "시장", "옛", "레트로", "근대"),
    "night": ("야경", "야간", "밤바다", "조명", "별빛"),
    "snow": ("설경", "겨울", "눈꽃", "눈 내린"),
    "mountain": ("계곡", "두타산", "무릉", "베틀바위", "폭포", "단풍", "숲"),
    "portrait": ("포토존", "전망대", "스카이밸리", "출렁다리", "스카이워크", "인물"),
}


def suggest_moods(text: str) -> list[str]:
    """Mood ids whose words appear in the text, in the list's order."""
    return [mood for mood in MOODS if any(word in text for word in MOOD_WORDS[mood])]


def suggest_place(title: str, places: Mapping[str, Place]) -> str | None:
    """The place named in the title: the longest name or alias found in it. Spaces are
    ignored ("추암 촛대바위" names 촛대바위). None when no place is named."""
    squeezed = title.replace(" ", "")
    found = [(len(name), place.id) for place in places.values()
             for name in (place.name, *place.aliases)
             if len(name.replace(" ", "")) >= 2 and name.replace(" ", "") in squeezed]
    return max(found)[1] if found else None


def review_rows(items: Iterable[Mapping], places: Mapping[str, Place]) -> list[dict]:
    """One sheet row per picture taken in Donghae, by title."""
    rows = []
    for item in items:
        location = item.get("galPhotographyLocation") or ""
        if AREA not in location:
            continue
        title, keywords = item.get("galTitle") or "", item.get("galSearchKeyword") or ""
        rows.append({
            "사진번호": str(item.get("galContentId") or ""), "제목": title,
            "촬영자": item.get("galPhotographer") or "",
            "촬영연월": item.get("galPhotographyMonth") or "", "촬영지역": location,
            "라이선스": f"{LICENSE}(출처표시) — API 설명 기준, 사용 전 확인",
            "원본 링크": item.get("galWebImageUrl") or "", "키워드": keywords,
            "분위기 제안": ", ".join(MOODS[m] for m in suggest_moods(f"{title} {keywords}")),
            "장소id 제안": suggest_place(title, places) or "",
            **{column: "" for column in REVIEW_COLUMNS}})
    return sorted(rows, key=lambda r: (r["제목"], r["사진번호"]))


def merge_review(rows: Sequence[dict], earlier: Iterable[Mapping]) -> list[dict]:
    """Rows of a new fetch with what the reviewer already filled in, by picture number.
    A picture the reviewer marked that the new fetch no longer lists is kept at the end:
    a mark is never dropped silently."""
    kept = {row.get("사진번호"): row for row in earlier}
    merged, seen = [], set()
    for row in rows:
        old = kept.get(row["사진번호"], {})
        merged.append({**row, **{c: old.get(c) or "" for c in REVIEW_COLUMNS}})
        seen.add(row["사진번호"])
    merged += [{c: old.get(c) or "" for c in COLUMNS} for number, old in kept.items()
               if number not in seen and (old.get("선택") or "").strip()]
    return merged


@dataclass(frozen=True)
class Record:
    id: str
    title: str
    image_url: str
    photographer: str | None
    shot_month: str | None
    location: str | None
    license: str
    place_id: str | None
    view_station_id: str | None
    moods: tuple[str, ...]
    keywords: tuple[str, ...]
    description: str | None
    position: int


def _split(cell: str) -> list[str]:
    return [part.strip() for part in cell.replace("|", ",").split(",") if part.strip()]


def _moods(cell: str) -> tuple[list[str], list[str]]:
    """(mood ids, words that are not a mood). Ids and the shown words are both accepted."""
    ids, unknown = [], []
    for word in _split(cell):
        mood = word if word in MOODS else LABEL_TO_ID.get(word)
        (ids if mood else unknown).append(mood or word)
    return list(dict.fromkeys(ids)), unknown


def parse(rows: Iterable[Mapping], places: Mapping[str, Place]) -> tuple[list[Record], list[str]]:
    """The marked rows as records, and every reason a marked row cannot be loaded.
    With any problem nothing should be loaded: the sheet is fixed first."""
    records, problems, seen = [], [], set()
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        if not (row.get("선택") or "").strip():
            continue
        number = (row.get("사진번호") or "").strip()
        where = f"{line}행(사진번호 {number or '없음'})"

        def problem(text: str, where=where) -> None:
            problems.append(f"{where}: {text}")

        if not number.isdigit():
            problem("사진번호가 숫자가 아니에요")
            continue
        if number in seen:
            problem("같은 사진번호가 두 번 있어요")
            continue
        seen.add(number)
        title, url = (row.get("제목") or "").strip(), (row.get("원본 링크") or "").strip()
        if not title:
            problem("제목이 비어 있어요")
        if not url.startswith(("https://", "http://")):
            problem("원본 링크가 주소가 아니에요")
        license_cell = (row.get("라이선스") or "").strip()
        if not any(allowed in license_cell for allowed in ALLOWED_LICENSES):
            problem(f"라이선스가 공공누리 제1유형·제0유형이 아니에요({license_cell or '빈 칸'})")
        moods, unknown = _moods((row.get("분위기") or "").strip()
                                or (row.get("분위기 제안") or ""))
        if unknown:
            problem(f"목록에 없는 분위기예요: {', '.join(unknown)}")
        if not moods and not unknown:
            problem("분위기가 하나도 없어요")
        place_id = ((row.get("장소id") or "").strip()
                    or (row.get("장소id 제안") or "").strip() or None)
        if place_id == "-":  # the reviewer says: no place of the service
            place_id = None
        if place_id is not None and place_id not in places:
            problem(f"모르는 장소id예요: {place_id}")
        station_id = (row.get("열차역id") or "").strip() or None
        if station_id is not None and (
                station_id not in places or places[station_id].kind != "station"):
            problem(f"열차역id가 역이 아니에요: {station_id}")
        records.append(Record(
            id=f"photo_kto_{number}", title=title[:200], image_url=url,
            photographer=(row.get("촬영자") or "").strip() or None,
            shot_month=(row.get("촬영연월") or "").strip()[:6] or None,
            location=(row.get("촬영지역") or "").strip() or None,
            license=next((f"공공누리 {a}" for a in ALLOWED_LICENSES if a in license_cell), ""),
            place_id=place_id, view_station_id=station_id, moods=tuple(moods),
            keywords=tuple(_split(row.get("키워드") or "")),
            description=(row.get("설명") or "").strip() or None, position=len(records)))
    return ([] if problems else records), problems


def embedding_text(record: Record, places: Mapping[str, Place]) -> str:
    """What a picture is, in words: this is what its vector is made from."""
    place = places.get(record.place_id) if record.place_id else None
    parts = [record.title, ", ".join(MOODS[m] for m in record.moods),
             place.name if place else "", ", ".join(record.keywords), record.description or ""]
    return "\n".join(part for part in parts if part)


def content_hash(record: Record, places: Mapping[str, Place]) -> str:
    return hashlib.sha256(embedding_text(record, places).encode("utf-8")).hexdigest()
