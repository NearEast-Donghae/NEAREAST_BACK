"""More names people use for 북평민속시장 (재래시장, 장터, 전통시장).

The seed file carries the same list for new databases; this revision brings a database
that already ran 0002 up to it. The document's text changes, so scripts/ingest.py embeds
it again. It follows saved_items only to keep one chain; the two do not depend on each other.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "market_aliases"
down_revision = "saved_items"
branch_labels = None
depends_on = None

DOCUMENT = "poi_bukpyeong_market"
BEFORE = ["북평민속시장", "북평장", "북평시장", "오일장", "장날"]
AFTER = [*BEFORE, "재래시장", "장터", "전통시장"]


def setKeywords(keywords):
    value = "CAST(:keywords AS JSONB)" if op.get_bind().dialect.name == "postgresql" else ":keywords"
    op.execute(
        sa.text(f"UPDATE knowledge_documents SET keywords = {value} WHERE id = :id").bindparams(
            keywords=json.dumps(keywords, ensure_ascii=False), id=DOCUMENT
        )
    )


def upgrade():
    setKeywords(AFTER)


def downgrade():
    setKeywords(BEFORE)
