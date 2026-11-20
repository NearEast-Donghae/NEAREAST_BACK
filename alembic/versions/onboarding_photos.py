"""Pictures for the picture-choosing onboarding, and each user's choice.

Two new tables; nothing existing is changed. If another revision lands on
0006_train_type_contract first (0007_station_coordinates is open in another pull
request), only down_revision here needs to point at it.
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "onboarding_photos"
down_revision = "0006_train_type_contract"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
EMBEDDING_VALUE = Vector(1536).with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "onboarding_photos",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("photographer", sa.String(length=120), nullable=True),
        sa.Column("shot_month", sa.String(length=6), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("license", sa.String(length=60), nullable=False),
        sa.Column("place_id", sa.String(length=64), nullable=True),
        sa.Column("view_station_id", sa.String(length=64), nullable=True),
        sa.Column("moods", JSON_VALUE, nullable=False),
        sa.Column("keywords", JSON_VALUE, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding", EMBEDDING_VALUE, nullable=True),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["view_station_id"], ["places.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_onboarding_photos_place_id"), "onboarding_photos", ["place_id"],
                    unique=False)
    op.create_table(
        "user_tastes",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("photo_ids", JSON_VALUE, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade():
    op.drop_table("user_tastes")
    op.drop_index(op.f("ix_onboarding_photos_place_id"), table_name="onboarding_photos")
    op.drop_table("onboarding_photos")
