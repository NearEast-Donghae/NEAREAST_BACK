import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_reference_records"
down_revision = "0004_integrate_postgres"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_reference_records",
        sa.Column("collection", sa.String(80), primary_key=True),
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
    )


def downgrade():
    op.drop_table("data_reference_records")
