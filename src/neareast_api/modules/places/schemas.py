"""Places API contract (map markers and the station/place detail screen)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["station", "poi", "heritage", "spot"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaceSummary(_Model):
    id: str = Field(description="지도 마커·챗봇 context·시간표가 함께 쓰는 id")
    name: str
    kind: Kind
    passenger_stop: bool | None = Field(
        description="역: true 승하차 가능 / false 승하차 불가(관찰만) / null 미확인. 역이 아니면 null")
    aliases: list[str]
    lat: float | None = Field(description="좌표 미입력이면 null")
    lng: float | None


class PlaceList(_Model):
    places: list[PlaceSummary]


class PlaceDetail(PlaceSummary):
    summary: str | None = Field(description="소개 첫 문단. 문서가 없으면 null")
    description: str | None = Field(description="소개 전문(문단은 빈 줄로 구분)")
    source: str | None = Field(description="소개의 근거 knowledge 문서 id")
    has_trains: bool = Field(description="true면 GET /stations/{id}/trains로 열차를 볼 수 있음")
