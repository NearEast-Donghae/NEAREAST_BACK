"""Request/response contract shared with the frontend (POST /api/v1/chat/messages).

Unknown fields are rejected (422) so naming drift between front and back surfaces
immediately instead of at integration time.
"""

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HISTORY_KEEP = 10  # most recent messages actually used
HISTORY_ACCEPT = 200  # more than this is not a chat client; reject

PlaceId = Annotated[str, Field(min_length=1, max_length=64)]
Intent = Literal["knowledge", "train_lookup", "safety", "proposal", "smalltalk"]
ToolName = Literal["station_rhythm", "search_trains", "nearby_spots"]
# timetable: public timetable as-is / estimated: interpolated from neighbouring stations
# planned: scheduled but same-day operation unconfirmed (e.g. freight) / None: not time-related
Certainty = Literal["timetable", "estimated", "planned"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatContext(_Strict):
    date: dt.date | None = None
    pins: list[PlaceId] = Field(default_factory=list, max_length=50)
    focused: PlaceId | None = None
    depth: Literal["reference", "light", "full"] = "light"
    dismissed: list[PlaceId] = Field(default_factory=list, max_length=100)


class ChatTurn(_Strict):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(_Strict):
    message: str = Field(min_length=1, max_length=1000)
    context: ChatContext = Field(default_factory=ChatContext)
    # Clients may send the whole conversation; only the latest HISTORY_KEEP messages are used.
    history: list[ChatTurn] = Field(default_factory=list, max_length=HISTORY_ACCEPT)

    @model_validator(mode="after")
    def _keep_recent_history(self) -> "ChatRequest":
        self.history = self.history[-HISTORY_KEEP:]
        return self


class Proposal(_Strict):
    type: Literal["add", "remove"]
    target: PlaceId
    reason: str
    # Principle 1: a proposal never changes the plan until the user accepts it.
    needs_confirm: Literal[True] = True
    certainty: Certainty | None = None


class ChatResponse(_Strict):
    reply: str
    intent: Intent
    chips: list[str] = Field(default_factory=list, max_length=3)
    proposals: list[Proposal] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    tool_used: ToolName | None = None
    # llm: answered by the model / fallback: the model failed, built-in responder answered
    # mock: NEAREAST_CHAT_MOCK=true (no model configured)
    mode: Literal["llm", "fallback", "mock"]


class ChatUnavailable(_Strict):
    detail: Literal["chat unavailable"] = "chat unavailable"


class PersonaLoading(_Strict):
    default: str
    train: str
    place: str = Field(description="Contains {place}; the frontend fills in the place name")
    knowledge: str
    safety: str
    slow: str


class PersonaResponse(_Strict):
    name: str
    greeting: str
    loading: PersonaLoading
