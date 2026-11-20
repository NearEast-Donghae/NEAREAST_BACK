from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from neareast_api.db.base import Base
from neareast_api.db.entities import JSON_VALUE, currentTime


class PlannerDatasetEntity(Base):
    __tablename__ = "planner_datasets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sourceName: Mapped[str] = mapped_column("source_name", String(100))
    manifest: Mapped[dict] = mapped_column(JSON_VALUE)
    summary: Mapped[dict] = mapped_column(JSON_VALUE)
    importedAt: Mapped[datetime] = mapped_column(
        "imported_at", DateTime(timezone=True), default=currentTime
    )


class PlannerRecord:
    datasetId: Mapped[str] = mapped_column(
        "dataset_id", ForeignKey("planner_datasets.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON_VALUE)


class PlannerLocationEntity(PlannerRecord, Base):
    __tablename__ = "planner_locations"
    __table_args__ = (UniqueConstraint("dataset_id", "position"),)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30), index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    coordinateReady: Mapped[bool] = mapped_column("coordinate_ready", Boolean)
    routingReady: Mapped[bool] = mapped_column("routing_ready", Boolean)
    visitAllowed: Mapped[bool | None] = mapped_column("visit_allowed", Boolean)
    canonicalPlaceId: Mapped[str | None] = mapped_column(
        "canonical_place_id", ForeignKey("places.id"), index=True
    )


class PlannerStationEntity(PlannerRecord, Base):
    __tablename__ = "planner_stations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("dataset_id", "position"),
    )
    tagoStationId: Mapped[str | None] = mapped_column("tago_station_id", String(40), index=True)
    passengerHandling: Mapped[bool | None] = mapped_column("passenger_handling", Boolean)
    freightHandling: Mapped[bool | None] = mapped_column("freight_handling", Boolean)


class PlannerHubEntity(PlannerRecord, Base):
    __tablename__ = "planner_transport_hubs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("dataset_id", "position"),
    )


class PlannerLineEntity(PlannerRecord, Base):
    __tablename__ = "planner_railway_lines"
    __table_args__ = (UniqueConstraint("dataset_id", "position"),)
    name: Mapped[str] = mapped_column(String(200))
    stationOrderVerified: Mapped[bool] = mapped_column("station_order_verified", Boolean)


class PlannerLineStationEntity(Base):
    __tablename__ = "planner_line_stations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "line_id"],
            ["planner_railway_lines.dataset_id", "planner_railway_lines.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "station_id"],
            ["planner_stations.dataset_id", "planner_stations.id"],
        ),
        UniqueConstraint("dataset_id", "line_id", "station_id"),
    )
    datasetId: Mapped[str] = mapped_column("dataset_id", String(64), primary_key=True)
    lineId: Mapped[str] = mapped_column("line_id", String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    stationId: Mapped[str] = mapped_column("station_id", String(64))


class PlannerTrainSegmentEntity(PlannerRecord, Base):
    __tablename__ = "planner_train_segments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "departure_location_id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
        ),
        ForeignKeyConstraint(
            ["dataset_id", "arrival_location_id"],
            ["planner_locations.dataset_id", "planner_locations.id"],
        ),
        UniqueConstraint("dataset_id", "position"),
    )
    trainNumber: Mapped[str] = mapped_column("train_number", String(30), index=True)
    serviceDate: Mapped[date] = mapped_column("service_date", Date, index=True)
    departureLocationId: Mapped[str | None] = mapped_column(
        "departure_location_id", String(64), index=True
    )
    arrivalLocationId: Mapped[str | None] = mapped_column(
        "arrival_location_id", String(64), index=True
    )
    departureTagoId: Mapped[str] = mapped_column("departure_tago_id", String(40), index=True)
    arrivalTagoId: Mapped[str] = mapped_column("arrival_tago_id", String(40), index=True)
    departureAt: Mapped[datetime] = mapped_column("departure_at", DateTime(timezone=True))
    arrivalAt: Mapped[datetime] = mapped_column("arrival_at", DateTime(timezone=True))
