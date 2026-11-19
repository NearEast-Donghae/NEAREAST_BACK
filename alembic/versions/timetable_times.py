"""Station times closer to the timetable, and 무궁화호 by its full name in the documents.

Times: the collected records are actual times, delays included. Where a train starts or
ends at one of our stations the 12/19 run plan gives the planned time itself; elsewhere
the earliest of the three recorded Saturdays is used, for arrivals as for departures
(arrivals used the middle value before). The statements are generated from the raw
records by scripts/fetch_timetable.py; the seed file carries the same values.

Document: "열차 종류 구분" names the type 무궁화호, as the timetable does. "무궁화" stays
a keyword so the document is still found by the short name.
"""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "timetable_times"
down_revision = "train_types"
branch_labels = None
depends_on = None

SEED = Path(__file__).resolve().parents[1] / "seed"
DOCUMENT = "rail_train_types"
KEYWORDS_BEFORE = ["차종", "열차 종류", "KTX-이음", "ITX-마음", "누리로", "무궁화", "화물열차"]
KEYWORDS_AFTER = ["차종", "열차 종류", "KTX-이음", "ITX-마음", "누리로", "무궁화호", "무궁화", "화물열차"]
BODY_BEFORE = (
    "동해역에는 KTX-이음, ITX-마음, 누리로, 무궁화 같은 여객열차와 화물열차가 지나가요. "
    "열차 종류 이름은 TIDS 표기를 따라요.\n\n화물열차 시각은 공식 데이터가 없어서 안내하지 못해요."
)
BODY_AFTER = BODY_BEFORE.replace("누리로, 무궁화 같은", "누리로, 무궁화호 같은")


def run(name):
    # One statement per line, executed one at a time so every database accepts it.
    for line in (SEED / name).read_text("utf-8").splitlines():
        if line.startswith("UPDATE "):
            op.execute(sa.text(line.rstrip(";").replace(":", r"\:")))


def writeDocument(keywords, body):
    value = (
        "CAST(:keywords AS JSONB)" if op.get_bind().dialect.name == "postgresql" else ":keywords"
    )
    op.execute(
        sa.text(
            f"UPDATE knowledge_documents SET keywords = {value}, body = :body WHERE id = :id"
        ).bindparams(keywords=json.dumps(keywords, ensure_ascii=False), body=body, id=DOCUMENT)
    )


def upgrade():
    run("timetable_times_after.sql")
    writeDocument(KEYWORDS_AFTER, BODY_AFTER)


def downgrade():
    run("timetable_times_before.sql")
    writeDocument(KEYWORDS_BEFORE, BODY_BEFORE)
