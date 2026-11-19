import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_planner_datasets"
down_revision = "0002_reference_content"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "planner_datasets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=100), nullable=False),
        sa.Column(
            "manifest",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "summary",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "planner_locations",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("coordinate_ready", sa.Boolean(), nullable=False),
        sa.Column("routing_ready", sa.Boolean(), nullable=False),
        sa.Column("visit_allowed", sa.Boolean(), nullable=True),
        sa.Column("canonical_place_id", sa.String(length=64), nullable=True),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["canonical_place_id"],
            ["places.id"],
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["planner_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dataset_id", "id"),
        sa.UniqueConstraint("dataset_id", "position"),
    )
    op.create_table(
        "planner_railway_lines",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("station_order_verified", sa.Boolean(), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["planner_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dataset_id", "id"),
        sa.UniqueConstraint("dataset_id", "position"),
    )
    op.create_table(
        "planner_stations",
        sa.Column("tago_station_id", sa.String(length=40), nullable=True),
        sa.Column("passenger_handling", sa.Boolean(), nullable=True),
        sa.Column("freight_handling", sa.Boolean(), nullable=True),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["planner_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dataset_id", "id"),
        sa.UniqueConstraint("dataset_id", "position"),
    )
    op.create_table(
        "planner_train_segments",
        sa.Column("train_number", sa.String(length=30), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("departure_location_id", sa.String(length=64), nullable=True),
        sa.Column("arrival_location_id", sa.String(length=64), nullable=True),
        sa.Column("departure_tago_id", sa.String(length=40), nullable=False),
        sa.Column("arrival_tago_id", sa.String(length=40), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrival_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "arrival_location_id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "departure_location_id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["planner_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dataset_id", "id"),
        sa.UniqueConstraint("dataset_id", "position"),
    )
    op.create_table(
        "planner_transport_hubs",
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["planner_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dataset_id", "id"),
        sa.UniqueConstraint("dataset_id", "position"),
    )
    op.create_table(
        "planner_line_stations",
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("line_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("station_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id", "line_id"],
            ["planner_railway_lines.dataset_id", "planner_railway_lines.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "station_id"],
            ["planner_stations.dataset_id", "planner_stations.id"],
        ),
        sa.PrimaryKeyConstraint("dataset_id", "line_id", "position"),
        sa.UniqueConstraint("dataset_id", "line_id", "station_id"),
    )
    op.create_index(
        op.f("ix_planner_locations_canonical_place_id"),
        "planner_locations",
        ["canonical_place_id"],
        unique=False,
    )
    op.create_index(op.f("ix_planner_locations_kind"), "planner_locations", ["kind"], unique=False)
    op.create_index(
        op.f("ix_planner_stations_tago_station_id"),
        "planner_stations",
        ["tago_station_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planner_train_segments_arrival_location_id"),
        "planner_train_segments",
        ["arrival_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planner_train_segments_arrival_tago_id"),
        "planner_train_segments",
        ["arrival_tago_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planner_train_segments_departure_location_id"),
        "planner_train_segments",
        ["departure_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planner_train_segments_departure_tago_id"),
        "planner_train_segments",
        ["departure_tago_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planner_train_segments_service_date"),
        "planner_train_segments",
        ["service_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_planner_train_segments_train_number"),
        "planner_train_segments",
        ["train_number"],
        unique=False,
    )


def downgrade():
    op.drop_table("planner_line_stations")
    op.drop_table("planner_transport_hubs")
    op.drop_table("planner_train_segments")
    op.drop_table("planner_stations")
    op.drop_table("planner_railway_lines")
    op.drop_table("planner_locations")
    op.drop_table("planner_datasets")
