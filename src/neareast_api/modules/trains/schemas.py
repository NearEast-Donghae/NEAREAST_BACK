"""Timetable API contract. Every answer carries `basis`: what the times rest on."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Direction = Literal["U", "D"]  # U 상행 / D 하행
TIME = r"^([01]\d|2[0-3]):[0-5]\d$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Basis(_Model):
    certainty: Literal["estimated"] = "estimated"
    summary: str = Field(description="한 줄 근거, 화면에 그대로 표시")
    target_date: str
    record_dates: list[str]
    plan_date: str
    limits: list[str] = Field(description="이 데이터로 알 수 없는 것")


# The only train types the service knows. Anything else is rejected where it is written
# (database constraint, timetable import, planning conditions) rather than shown.
TRAIN_TYPES = ("KTX-이음", "ITX-마음", "ITX-새마을", "새마을호", "누리로", "무궁화호")
TrainType = Literal["KTX-이음", "ITX-마음", "ITX-새마을", "새마을호", "누리로", "무궁화호"]


class TrainSummary(_Model):
    no: str
    type: TrainType | None = Field(description="열차 종류. 사람이 확인해 등록한 것만, 없으면 null")
    line: str
    direction: Direction
    origin: str
    destination: str
    weekly_saturday: bool = Field(description="최근 토요일 3주 모두 운행")
    in_plan: bool = Field(description="다음 조회 가능한 토요일 운행계획에도 있음")
    spread_min: int = Field(description="주별 시각 차이(분). 클수록 지연이 잦았던 열차")


class StationTrain(TrainSummary):
    time: str = Field(description="정렬 기준 시각: 출발, 없으면 도착, 통과역은 통과 시각")
    arr: str | None
    dep: str | None
    passing: bool = Field(description="true면 서지 않고 지나감(관찰만 가능), 시각은 어림")
    stop_type: str
    time_basis: str | None = Field(description="통과 시각을 어림한 근거")


class Gap(_Model):
    start: str
    end: str
    minutes: int


class Rhythm(_Model):
    hourly: dict[str, int] = Field(description='"05"~"23" 시간대별 열차 수')
    busiest_hour: str | None
    gaps: list[Gap] = Field(description="열차 사이가 60분 이상 비는 구간")


class StationTrains(_Model):
    station_id: str
    station_name: str
    boarding: bool = Field(description="false면 여객 승하차 불가 역(관찰만)")
    note: str | None
    trains: list[StationTrain]
    rhythm: Rhythm
    basis: Basis


class TripOption(TrainSummary):
    dep: str = Field(description="출발역 출발 시각")
    arr: str = Field(description="도착역 도착 시각")
    minutes: int


class TrainSearch(_Model):
    from_id: str
    to_id: str
    after: str | None
    trains: list[TripOption]
    basis: Basis


class Stop(_Model):
    name: str
    place_id: str | None = Field(description="장소 테이블에 있는 역이면 그 id")
    arr: str | None
    dep: str | None
    pass_time: str | None = Field(description="통과역의 어림 통과 시각")
    stop_type: str
    time_basis: str | None


class TrainDetail(TrainSummary):
    stops: list[Stop]
    basis: Basis
