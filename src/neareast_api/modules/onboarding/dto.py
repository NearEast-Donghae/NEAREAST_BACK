from pydantic import BaseModel

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
