"""Day plan API contract (와이어프레임 05 플래너 · 06 일정 비교 · 07 세부 일정).

The request carries everything (no storage). `items` are the user's pins: the server
never drops or edits a `must` item, and it only *suggests* places for empty time.
"""

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from neareast_api.modules.trains.schemas import TIME

MAX_ITEMS = 12
Mode = Literal["train_first", "visit_first", "balanced"]
Depth = Literal["reference", "light", "full"]
Certainty = Literal["estimated"]
Clock = Annotated[str, Field(pattern=TIME)]
PlaceId = Annotated[str, Field(min_length=1, max_length=64)]
TrainNo = Annotated[str, Field(min_length=1, max_length=10)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- request ---------------------------------------------------------------------


class Anchor(_Model):
    place: PlaceId
    time: Clock


class ObserveItem(_Model):
    kind: Literal["observe"]
    train: TrainNo
    place: PlaceId | None = Field(
        default=None, description="볼 자리(역 id). 비우면 그 열차가 지나는 역 중 가장 편한 곳")
    must: bool = Field(default=False, description="true: 꼭. 안 되면 계획 대신 충돌로 응답")


class RideItem(_Model):
    kind: Literal["ride"]
    train: TrainNo
    from_: PlaceId = Field(alias="from")
    to: PlaceId
    must: bool = False


class StayItem(_Model):
    kind: Literal["visit", "station"] = Field(
        description="visit: 장소 방문 / station: 역에 머물며 구경")
    place: PlaceId
    stay_min: int = Field(default=30, ge=5, le=480)
    must: bool = False


Item = Annotated[ObserveItem | RideItem | StayItem, Field(discriminator="kind")]


class PlanRequest(_Model):
    date: dt.date
    start: Anchor
    end: Anchor
    items: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS,
                              description="사용자가 담은 것(핀). 최대 12개")
    mode: Mode = "balanced"
    dismissed: list[PlaceId] = Field(default_factory=list, max_length=100,
                                     description="거절한 제안 target. 다시 제안하지 않음")
    depth: Depth = Field(default="full", description=(
        "reference: 제안 없이 타임라인만 / light: 되는지와 요약만(타임라인 없음) / full: 전부"))

    @model_validator(mode="after")
    def _day_runs_forward(self):
        if self.start.time >= self.end.time:
            raise ValueError("end.time must be after start.time (same day)")
        return self


class CompareRequest(_Model):
    base: PlanRequest
    changed: PlanRequest


# --- response: timeline steps ----------------------------------------------------


class Passing(_Model):
    train: str
    at: str


class StartStep(_Model):
    type: Literal["start"] = "start"
    at: str
    place: str


class EndStep(_Model):
    type: Literal["end"] = "end"
    at: str
    place: str


class WaitStep(_Model):
    type: Literal["wait"] = "wait"
    from_: str = Field(alias="from")
    to: str
    place: str
    passing: list[Passing] = Field(description="기다리는 동안 덤으로 보이는 열차")


class GapStep(_Model):
    type: Literal["gap"] = "gap"
    from_: str = Field(alias="from")
    to: str
    place: str
    note: str
    passing: list[Passing] = Field(description="이 공백을 끝내는 열차(to 시각에 옴). 없으면 []")


class WalkStep(_Model):
    type: Literal["walk"] = "walk"
    from_: str = Field(alias="from", description="출발 장소 id")
    to: str
    depart: str
    arrive: str
    min: int
    certainty: Certainty = "estimated"


class RideStep(_Model):
    type: Literal["ride"] = "ride"
    item: int | None = Field(description="items의 몇 번째인지. 이동을 위해 서버가 고른 열차면 null")
    train: str
    from_: str = Field(alias="from")
    depart: str
    to: str
    arrive: str
    certainty: Certainty = "estimated"
    margin_min: int = Field(description="가장 늦게 도착하는 경우에도 출발까지 남는 시간(분)")


class ObserveStep(_Model):
    type: Literal["observe"] = "observe"
    item: int
    train: str
    place: str
    at: str
    passing_through: bool = Field(description="true: 서지 않고 지나감")
    certainty: Certainty = "estimated"
    basis: str
    margin_min: int = Field(description="가장 늦게 도착하는 경우에도 열차가 오기까지 남는 시간(분)")


class VisitStep(_Model):
    type: Literal["visit"] = "visit"
    item: int
    place: str
    from_: str = Field(alias="from")
    to: str
    passing: list[Passing] = Field(description="머무는 동안 그 역에 오는 열차. 역이 아니면 []")


Step = Annotated[
    StartStep | EndStep | WaitStep | GapStep | WalkStep | RideStep | ObserveStep | VisitStep,
    Field(discriminator="type"),
]


# --- response: the plan ----------------------------------------------------------


class Summary(_Model):
    seen: int = Field(description="본 열차 수(관찰 + 기다리거나 머무는 동안 지나간 열차)")
    ridden: int
    visits: int
    walk_min: int
    longest_gap_min: int
    end_arrival: str = Field(description="종료 장소에 도착하는 시각")


class Change(_Model):
    item: int
    replace: Item = Field(description="items[item]을 이것으로 바꿔 다시 요청하면 됨")


class Option(_Model):
    label: str
    change: Change


class Conflict(_Model):
    item: int | None = Field(description="안 되는 must 항목. 시작→종료 자체가 안 되면 null")
    reason: str
    options: list[Option] = Field(description="서버가 다시 계산해 되는 것만(최대 2개)")


class Missed(_Model):
    item: int
    reason: str


class Outside(_Model):
    item: int
    train: str
    at: str
    hint: str


class Suggestion(_Model):
    type: Literal["add"] = "add"
    target: str
    gap: list[str] = Field(description="[from, to] 이 제안이 들어갈 공백")
    reason: str
    needs_confirm: Literal[True] = True
    certainty: Certainty = "estimated"
    notes: list[str]
    walk_min: int = Field(description="공백의 기준 장소에서 편도 도보(분)")
    stay_min: int


class Plan(_Model):
    feasible: bool
    summary: Summary | None = Field(description="feasible=false면 null")
    timeline: list[Step]
    conflicts: list[Conflict]
    missed: list[Missed] = Field(description="must=false 중 못 넣은 것")
    outside: list[Outside] = Field(description="하루 밖(시작 전·종료 후)에 지나는 열차")
    suggestions: list[Suggestion] = Field(description="빈 시간 제안. 타임라인에는 절대 넣지 않음")
    basis: str


class Variant(_Model):
    mode: Mode
    label: str
    feature: str = Field(description="이 안의 특징 한 줄(서버 문장)")
    summary: Summary | None
    plan: Plan


class Variants(_Model):
    variants: list[Variant]


class Delta(_Model):
    kind: Literal["train", "place"]
    id: str
    how: Literal["observe", "ride", "passing", "visit"]


class Diff(_Model):
    gained: list[Delta]
    lost: list[Delta]
    end_arrival: list[str | None] = Field(description="[base, changed]")
    gap_change_min: int | None = Field(description="가장 긴 공백의 변화(분). 한쪽이 불가능하면 null")


class Comparison(_Model):
    base: Plan
    changed: Plan
    diff: Diff
