"""What a recommendation looks like. It is only ever offered: the user accepts it or not."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["add"] = "add"
    target: str
    gap: list[str] = Field(description="[from, to] 이 제안이 들어갈 공백")
    reason: str
    needs_confirm: Literal[True] = True
    certainty: Literal["timetable", "estimated", "planned"] = "estimated"
    notes: list[str]
    walk_min: int = Field(description="공백의 기준 장소에서 편도 도보(분)")
    purpose: Literal["visit", "meal"] = Field(
        default="visit", description="visit: 둘러볼 곳 / meal: 식사 시간과 겹치는 공백의 식당")
    stay_min: int
