from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0002_reference_content"
down_revision = "0001_service_storage"
branch_labels = None
depends_on = None


def upgrade():
    seedPath = Path(__file__).resolve().parents[1] / "seed" / "0002_reference.sql"
    op.execute(sa.text(seedPath.read_text("utf-8").replace(":", r"\:")))


def downgrade():
    # User edits must survive a reference-data downgrade.
    pass
