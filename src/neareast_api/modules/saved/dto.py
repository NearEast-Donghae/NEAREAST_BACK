from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Priority = Literal["must", "interested"]
TrainAction = Literal["ride", "observe"]


class SavedPlaceCreateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    placeId: str = Field(min_length=1, max_length=64)
    priority: Priority = "interested"


class SavedPlaceUpdateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: Priority


class SavedPlaceDto(BaseModel):
    id: str
    placeId: str
    name: str
    kind: str
    priority: Priority
    createdAt: datetime
    updatedAt: datetime


class SavedTrainCreateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trainNo: str = Field(min_length=1, max_length=20)
    action: TrainAction
    stationId: str = Field(min_length=1, max_length=64)
    priority: Priority = "interested"


class SavedTrainUpdateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: Priority | None = None
    action: TrainAction | None = None

    @model_validator(mode="after")
    def checkChange(self):
        if self.priority is None and self.action is None:
            raise ValueError("Send priority or action")
        return self


class SavedTrainDto(BaseModel):
    id: str
    trainNo: str
    action: TrainAction
    stationId: str
    stationName: str
    priority: Priority
    # False when the train is no longer in the timetable; the fields below are then null.
    available: bool
    trainType: str | None
    line: str | None
    direction: str | None
    origin: str | None
    destination: str | None
    time: str | None = Field(description="그 역에서의 시각: 출발, 없으면 도착, 통과역은 통과 시각")
    arr: str | None
    dep: str | None
    passing: bool | None = Field(description="true면 서지 않고 지나감(관찰만 가능)")
    timeBasis: str | None = Field(description="통과 시각을 어림한 근거")
    certainty: Literal["timetable", "estimated", "planned"] | None
    createdAt: datetime
    updatedAt: datetime


class SavedDto(BaseModel):
    places: list[SavedPlaceDto]
    trains: list[SavedTrainDto]
    basis: str | None = Field(description="열차 시각이 무엇에 근거하는지")
