"""Saved places and trains per user.

If another revision lands on 0002_reference_content first, only down_revision here needs
to point at it: these two tables do not touch anything that revision could change.
"""

import sqlalchemy as sa
from alembic import op

revision = "saved_items"
down_revision = "0002_reference_content"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "saved_places",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("place_id", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "place_id"),
    )
    op.create_index(op.f("ix_saved_places_user_id"), "saved_places", ["user_id"], unique=False)
    op.create_table(
        "saved_trains",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("train_no", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("station_id", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["station_id"], ["places.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "train_no", "action", "station_id"),
    )
    op.create_index(op.f("ix_saved_trains_user_id"), "saved_trains", ["user_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_saved_trains_user_id"), table_name="saved_trains")
    op.drop_table("saved_trains")
    op.drop_index(op.f("ix_saved_places_user_id"), table_name="saved_places")
    op.drop_table("saved_places")
