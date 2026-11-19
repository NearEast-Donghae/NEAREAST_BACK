from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from neareast_api.modules.trains.schemas import TrainType


class TimelineItemDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time: str = Field(max_length=50)
    icon: str = Field(default="", max_length=20)
    description: str = Field(max_length=2000)
    type: Literal["start", "train", "visit", "watch", "meal", "break", "end"] | None = None
    placeId: str | None = Field(default=None, max_length=64)
    startsAt: datetime | None = None
    endsAt: datetime | None = None

    @model_validator(mode="after")
    def checkTimes(self):
        if any(
            moment is not None and moment.tzinfo is None for moment in (self.startsAt, self.endsAt)
        ):
            raise ValueError("Use timezone-aware times")
        if self.startsAt and self.endsAt:
            if self.endsAt < self.startsAt:
                raise ValueError("End time must not precede start time")
        return self


class RouteLegDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fromPlaceId: str = Field(max_length=64)
    toPlaceId: str = Field(max_length=64)
    mode: Literal["walk", "train", "car", "transit", "unknown"]
    durationMinutes: int | None = Field(default=None, ge=0, le=10080)
    distanceMeters: int | None = Field(default=None, ge=0)
    coordinates: list[tuple[float, float]] = Field(default_factory=list, max_length=20000)
    certainty: Literal["verified", "estimated", "unknown"] = "unknown"
    source: str | None = Field(default=None, max_length=300)


class ItineraryCreateDto(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: Literal["planner", "ai"]
    title: str = Field(min_length=1, max_length=120)
    travelDate: date | None = None
    travelDateLabel: str = Field(max_length=80)
    timeRange: str = Field(max_length=80)
    stations: list[str] = Field(default_factory=list, max_length=100)
    trains: list[str] = Field(default_factory=list, max_length=100)
    activities: list[str] = Field(default_factory=list, max_length=100)
    timeline: list[TimelineItemDto] = Field(default_factory=list, max_length=300)
    legs: list[RouteLegDto] = Field(default_factory=list, max_length=100)
    planningRequestId: str | None = None


class ItineraryUpdateDto(ItineraryCreateDto):
    expectedRevision: int = Field(ge=1)


class ItineraryDto(ItineraryCreateDto):
    id: str
    revision: int
    createdAt: datetime
    updatedAt: datetime


class PlanningConditionsDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    travelDate: date | None = None
    endDate: date | None = None
    startTime: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    endTime: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    departurePlaceId: str | None = Field(default=None, max_length=64)
    arrivalPlaceId: str | None = Field(default=None, max_length=64)
    requiredPlaceIds: list[str] = Field(default_factory=list, max_length=50)
    preferredTrainTypes: list[TrainType] = Field(default_factory=list, max_length=10)
    selectedTrainIds: list[str] = Field(default_factory=list, max_length=50)
    interests: list[str] = Field(default_factory=list, max_length=30)
    maxWalkingMinutes: int | None = Field(default=None, ge=0, le=1440)

    @model_validator(mode="after")
    def checkDates(self):
        if self.endDate and self.travelDate and self.endDate < self.travelDate:
            raise ValueError("End date must not precede travel date")
        sameDay = self.endDate is None or self.endDate == self.travelDate
        if sameDay and self.startTime and self.endTime and self.endTime <= self.startTime:
            raise ValueError("End time must be later on the same day")
        return self


class PlanningRequestCreateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["planner", "chat"]
    conversationId: str | None = None
    conditions: PlanningConditionsDto = Field(default_factory=PlanningConditionsDto)


class PlanningRequestUpdateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=1)
    conditions: PlanningConditionsDto


class PlanningRequestDto(PlanningRequestCreateDto):
    model_config = ConfigDict(from_attributes=True)
    id: str
    revision: int
    createdAt: datetime
    updatedAt: datetime
