"""Fuller knowledge documents for four places, each with the pages its facts come from.

Only facts found on the listed pages are written; anything not confirmed (opening hours,
fees) is left out. The seed file carries the same text for new databases; this revision
brings a database that already ran 0002 up to it. The text changes, so scripts/ingest.py
embeds these four documents again. source_url holds one URL per line.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "knowledge_enrichment"
down_revision = "market_aliases"
branch_labels = None
depends_on = None

SEANTOUR = "https://www.seantour.kr/newseantour/recommend/thematour/themaTourDetail.do?tourNttSn=THEME0000000428"
BADAON = "https://badaon.or.kr/seantour_map/travel/destination/detail.do?destId=FCID111738"
TELLTRIP = "https://www.telltrip.com/domestic-travel/chuam-beach-candlestick-rock-suspension-bridge/"
INDEPENDENCE_HALL = "http://sbook.allabout.co.kr/magazine/i815/sm-31/pt-post/nd-323"
GANGWON_MAP = "https://www.gangwon.to/page/gwtour2022/images/map/tourmap_04.pdf"

# document id -> (keywords, body, source URLs)
AFTER = {
    "poi_chotdaebawi": (
        ["촛대바위", "추암해변", "해암정", "출렁다리", "일출"],
        "촛대바위는 추암역 근처에 있어요. 동해시와 삼척시 경계에 있는 일출 명소이고, "
        "애국가 첫 소절의 배경 영상에 나온 곳이에요.\n\n"
        "뾰족한 바위들은 석회암 풍화층이 파도에 깎여 남은 것이에요. 작은 동산 꼭대기의 "
        "전망대에서 새해 첫 일출을 보는 사람이 많아요.\n\n"
        "주변에는 마을 앞 약 150m 백사장의 추암해변과, 고려 공민왕 때 심동로가 지은 정자 "
        "해암정이 있어요. 촛대바위에서 약 200m 떨어진 곳에는 2019년 6월에 개통한 길이 약 72m의 "
        "해상 출렁다리가 있어요.",
        [SEANTOUR, BADAON, TELLTRIP],
    ),
    "poi_nongoldam": (
        ["논골담길", "논골담", "묵호등대", "벽화"],
        "논골담길은 묵호역 근처에 있어요. 묵호항에서 묵호등대까지 이어지는 비탈 골목이고, "
        "담벼락에는 주민들의 생활과 이야기를 담은 벽화가 있어요.\n\n"
        "1970년대 호황기 묵호항의 역사와 어촌 이야기를 담은 길이에요. 꼭대기의 묵호등대는 "
        "1963년 6월 8일에 처음 불을 밝혔어요.",
        [INDEPENDENCE_HALL, GANGWON_MAP],
    ),
    "poi_mangsang_beach": (
        ["망상해수욕장"],
        "망상해수욕장은 동해시의 관광지예요. 넓은 백사장과 울창한 송림, 맑은 바닷물이 있어요.\n\n"
        "동해의 다른 해변보다 수심이 얕아서 바다 쪽으로 100m를 들어가도 깊이가 1m 안팎이에요. "
        "가족 물놀이에 알맞아요.",
        [INDEPENDENCE_HALL, GANGWON_MAP],
    ),
    "poi_dojjebigol": (
        ["도째비골", "스카이밸리", "해랑전망대"],
        "도째비골 스카이밸리는 동해시의 관광지예요. 묵호등대, 논골담길과 같은 묵호권역에 있어요.\n\n"
        "해랑전망대는 도깨비 방망이(도째비) 모양을 본뜬 구조물이에요. 운영 시간은 아직 확인된 "
        "정보가 없어요.",
        [GANGWON_MAP],
    ),
}
BEFORE = {
    "poi_chotdaebawi": (["촛대바위"], "촛대바위는 추암역 근처에 있어요.", []),
    "poi_nongoldam": (["논골담길", "논골담"], "논골담길은 묵호역 근처에 있어요.", []),
    "poi_mangsang_beach": (
        ["망상해수욕장"],
        "망상해수욕장은 동해시의 관광지예요. 자세한 소개는 아직 확인된 정보가 없어요.",
        [],
    ),
    "poi_dojjebigol": (
        ["도째비골", "스카이밸리"],
        "도째비골 스카이밸리는 동해시의 관광지예요. 운영 시간은 아직 확인된 정보가 없어요.",
        [],
    ),
}


def write(documents):
    keywords = (
        "CAST(:keywords AS JSONB)" if op.get_bind().dialect.name == "postgresql" else ":keywords"
    )
    statement = sa.text(
        f"UPDATE knowledge_documents SET keywords = {keywords}, body = :body, "
        "source_url = :source WHERE id = :id"
    )
    for documentId, (words, body, urls) in documents.items():
        op.execute(
            statement.bindparams(
                keywords=json.dumps(words, ensure_ascii=False),
                body=body,
                source="\n".join(urls) or None,
                id=documentId,
            )
        )


def upgrade():
    write(AFTER)


def downgrade():
    write(BEFORE)
