from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from neareast_api.modules.recommend.companions import Companion


class DestinationOptionDto(BaseModel):
    placeId: str
    name: str
    trainsPerDay: int
    trainTypes: list[str]
    featured: bool  # shown as its own chip; the rest go under "그 외"
    label: str  # "하루 18편 · KTX-이음, ITX-마음"


class CompanionOptionDto(BaseModel):
    id: Companion
    label: str
    maxWalkingMinutes: int | None
    indoorFirst: bool
    reason: str | None  # the sentence shown to the user when this choice changes a result


class OnboardingOptionsDto(BaseModel):
    destinations: list[DestinationOptionDto]
    companions: list[CompanionOptionDto]
    targetDate: str
    basis: str


class OriginOptionDto(BaseModel):
    stationName: str
    stationCode: str | None
    placeId: str | None  # set when the station is on the service map
    trainsPerDay: int
    trainTypes: list[str]
    firstDeparture: str
    lastDeparture: str
    fastestMinutes: int
    startsHere: bool  # at least one of those trains starts its run at this station
    featured: bool
    label: str


class OnboardingStationDto(BaseModel):
    placeId: str
    name: str


class OriginsDto(BaseModel):
    destination: OnboardingStationDto
    origins: list[OriginOptionDto]
    certainty: str  # times come from the snapshot: "estimated"
    targetDate: str
    basis: str


# --- Choosing pictures -------------------------------------------------------------

MIN_PICKS = 3  # fewer pictures than this say too little about a taste
MAX_PICKS = 12


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoodDto(BaseModel):
    id: str
    label: str


class PhotoCreditDto(BaseModel):
    photographer: str | None
    source: str
    license: str
    text: str  # the line to show next to the picture, e.g. "ⓒ한국관광공사 포토코리아-김지호"


class PhotoDto(BaseModel):
    id: str
    title: str
    imageUrl: str
    moods: list[str]  # mood ids, see PhotoBoardDto.moods for the words
    placeId: str | None  # where it was taken, when that is a place of the service
    placeName: str | None
    shotMonth: str | None  # "202311"
    credit: PhotoCreditDto


class PhotoBoardDto(BaseModel):
    photos: list[PhotoDto]
    moods: list[MoodDto]
    minPicks: int
    maxPicks: int


class TasteRequestDto(_Strict):
    photoIds: list[Annotated[str, StringConstraints(min_length=1, max_length=64)]] = Field(
        min_length=MIN_PICKS, max_length=MAX_PICKS)


class TasteMoodDto(BaseModel):
    id: str
    label: str
    count: int  # how many of the chosen pictures carry it
    share: float  # count / pictures chosen, 0..1


class AccessDto(BaseModel):
    stationId: str
    stationName: str
    walkMinutes: int  # straight-line estimate, the same number the rest of the service gives
    withinWalk: bool  # false: over 30 minutes on foot, better said as "bus or taxi"


class GoldenTrainDto(BaseModel):
    trainNo: str
    trainType: str | None
    time: str
    passing: bool  # true: it does not stop there, the time is an estimate between stations
    near: Literal["sunrise", "sunset"]
    minutesFromSun: int  # negative: before sunrise/sunset


class WatchDto(BaseModel):
    stationId: str
    stationName: str
    boarding: bool  # false: trains pass or carry freight only; watching, not riding
    trainsPerDay: int
    # Trains within 40 minutes of sunrise or sunset, at most the 4 nearest to each.
    golden: list[GoldenTrainDto]
    basis: str  # whose times these are; a spot between stations sees the train minutes apart
    note: str | None


class TastePlaceDto(BaseModel):
    placeId: str
    name: str
    kind: str
    category: str | None
    reason: str  # why it is offered, shown as it is
    match: Literal["photo", "mood", "similar"]
    photoIds: list[str]  # the chosen pictures taken here
    coverPhotoId: str | None  # a picture of the place to show on its card
    moods: list[str]
    access: AccessDto | None  # the nearest station one can get off at; null for a station
    watch: WatchDto | None  # set only where a train is in view: a station, or a picture says so


class LightDto(BaseModel):
    date: str
    sunrise: str
    sunset: str
    basis: str


class TasteDto(BaseModel):
    photoIds: list[str]
    moods: list[TasteMoodDto]
    summary: str
    places: list[TastePlaceDto]  # offers only: nothing is put into a plan or a saved list
    light: LightDto
    certainty: Literal["estimated"] | None  # of the train times; null without a timetable
    basis: str | None
    updatedAt: datetime | None
