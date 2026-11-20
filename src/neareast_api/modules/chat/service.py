"""Chat business rules. Framework-free: the router converts errors to HTTP.

A responder (mock now, LLM in step 3) only produces a Draft. Plan principles are
enforced afterwards in `_finalize`, in code, so a responder that ignores the rules
cannot break them:
- proposals always need confirmation and never target dismissed/unknown ids
- `certainty` is copied from tool results, never chosen by the responder
- unknown focused/pin ids are ignored, disclosed in the reply, kept out of sources
- sources only name knowledge documents that exist
- a reply that states train times without a tool result is replaced
"""

import dataclasses
import datetime as dt
import json
import logging
import re
import time
import typing
from contextlib import contextmanager
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from neareast_api.core.config import Settings
from neareast_api.modules.chat import llm, prompts, retrieval, tools
from neareast_api.modules.chat.schemas import (
    Certainty,
    ChatAction,
    ChatContext,
    ChatRequest,
    ChatResponse,
    ChatStep,
    Intent,
    PersonaLoading,
    PersonaResponse,
    Proposal,
    ToolName,
)
from neareast_api.modules.itineraries.dto import PlanningConditionsDto, SelectedTrainDto
from neareast_api.modules.recommend import companions
from neareast_api.modules.recommend import service as recommend_service
from neareast_api.modules.trains import service as timetable_service

logger = logging.getLogger(__name__)

MAX_CHIPS = 3
SLOW_REQUEST_MS = 5000  # the frontend switches to the "slow" text at 5 s
MAX_REPLY_CHARS = 400  # the prompt asks for 2-4 sentences; this is the hard stop
MAX_AGENT_REPLY_CHARS = 600  # an answer built on several lookups may run to 6 sentences
# Agent loop: another tool step is started only with this much of the request budget left
# (a step is a tool run plus a model call, and the final answer still has to follow).
AGENT_STEP_RESERVE_SECONDS = 4.5
AGENT_OUT_OF_TIME = "시간이 오래 걸려 여기까지만 확인했어요. 나머지는 다시 물어봐 주세요."
PLACE_SEARCH_CATEGORIES = {"attraction": "관광지", "culture": "문화시설", "market": "시장",
                           "restaurant": "식당"}
MAX_PLACES_FOUND = 6
TOOL_NAMES = frozenset(typing.get_args(ToolName))
KST = ZoneInfo("Asia/Seoul")
# Clock times ("10:20", "오후 4시 10분", "10시대") and train counts ("3편") must be backed
# by a tool result or a cited document. Durations ("도보 7분", "1시간 거리") are not clock times.
CLOCK = re.compile(
    r"(\d{1,2}):(\d{2})"
    r"|(?:(오전|아침|오후|저녁|밤|낮)\s*)?(\d{1,2})\s*시(?!간|즌)(?:\s*(\d{1,2})\s*분|\s*(반))?"
)
TRAIN_COUNT = re.compile(r"(\d+)\s*편")
NO_TIME_WITHOUT_TOOL = (
    "열차 시각과 편수는 확인된 시간표 결과로만 안내할 수 있어요. "
    "역을 열어 두고 열차 리듬을 물어봐 주세요."
)
NO_INFO = "아직 확인된 정보가 없어요."
# Only when the question points at "here/this" with nothing open: otherwise the hint has
# nothing to do with what was asked ("완목 신호기는 없어?").
NO_INFO_HINT = "지도에서 궁금한 역이나 장소를 열어 두고 다시 물어봐 주세요."
DEICTIC_WORDS = ("여기", "이거", "이곳", "거기", "저기", "이 역", "이 장소")
ACK_REPLY = "네, 알겠어요. 더 궁금한 열차나 장소가 있으면 편하게 물어봐 주세요."
MOCK_NOTE = "(예시 데이터)"
BASIS_MARK = "토요일 3주"  # present in the timetable's basis summary
PASSING_MARK = "앞뒤 정차역"  # present in the passing-time basis
PASSING_NOTE = "이 역은 여객열차가 서지 않아 타고 내릴 수 없고, 열차는 관찰만 할 수 있어요."
PASSING_DOC = "safety_passing_station"
DEFAULT_CHIPS = ["동해역 열차 리듬", "동해역 근처"]
SAFETY_NO = "선로·건널목 안이나 차단기 안쪽에서의 촬영·진입은 권하지 않아요."
# The same refusal is not said twice in one conversation: a repeat gets the short form.
SAFETY_NO_AGAIN = "이번에도 차단기 안쪽이나 선로 쪽은 권하지 않아요."
SAFETY_ALTERNATIVE = "대신 역 맞이방이나 승강장처럼 공개된 장소에서 열차를 보는 방법을 권해요."
SAFETY_ALTERNATIVE_AT = "대신 {station} 승강장이나 맞이방처럼 공개된 장소에서 열차를 볼 수 있어요."
# Watching from a public road or pavement outside the barrier is allowed (principle 6 is
# about going inside): one line that says where the limit is, then the real answer.
SAFETY_PUBLIC = ("건널목은 차단기 밖의 공개된 도로나 인도에서 보는 건 괜찮아요. "
                 "차단기 안쪽과 선로 부지에는 들어가지 않는 게 원칙이에요.")
SAFETY_PUBLIC_AGAIN = "차단기 밖 공개된 도로나 인도에서만 봐 주세요."
SAFETY_PUBLIC_MARKS = ("차단기 밖", "차단기 안쪽")
NO_CROSSING_LIST = ("어느 건널목이 보기 좋은지는 아직 확인된 정보가 없어요. "
                    "봐 둔 지점이 있으면 지도에 직접 찍어 두고 물어봐 주세요.")
# Where the user wants to stand. Inside wins: "건널목 근처 선로 안에서" is still inside.
_INSIDE_ASK = ("안에", "안쪽", "안으로", "위에서", "가운데", "들어가", "들어갈", "넘어", "따라",
               "옆에 서", "건널목에서", "선로에서", "철길에서", "철로에서", "부지")
_PUBLIC_ASK = ("도로", "인도", "보도", "차단기 밖", "밖에서", "근처", "주변", "멀리서", "육교",
               "건너편", "볼까", "보고 싶", "보러", "구경")
# A sentence that sends the user inside, unless it says not to.
_INSIDE_SAID = ("선로 안", "선로 위", "선로로", "선로에 ", "차단기 안", "건널목 안", "철길 위",
                "철길 안", "넘어가", "넘어서", "들어가")
_NEGATION = ("않", "안 돼", "안돼", "금지", "위험", "마세요", "말아", "말고", "피해", "불가", "없어요")
ALTERNATIVE_WORDS = ("공개된", "승강장", "맞이방")
# The safety rewrite applies only when the user actually asks about these places.
RISK_WORDS = ("선로", "건널목", "차단기", "철길", "철로")
# "Leave the gap empty" is a valid choice (principle 2), not a question that needs documents.
UNKNOWN_MARK = "아직 모르는 곳"
UNKNOWN_FOCUSED = f"지금 열어 둔 장소는 {UNKNOWN_MARK}이라 빼고 답할게요."
UNKNOWN_PINS = f"핀 가운데 {UNKNOWN_MARK}이 있어 빼고 답할게요."
BOARDING_WORDS = ("탈 수", "타도", "타고", "타려", "탑승", "승차", "하차", "내릴", "내려", "정차")
KEEP_EMPTY_WORDS = ("그대로", "쉬기", "쉴래", "쉴게", "비워")
# Chips that ask for nearby places; they dead-end when the place has no nearby data.
NEARBY_WORDS = ("근처", "주변", "같이 볼", "볼 곳", "갈 곳", "가는 길")
_CERTAINTY_ORDER = ("timetable", "estimated", "planned")


class ChatUnavailableError(Exception):
    """No responder can answer right now (LLM or tool unavailable)."""


@dataclass(frozen=True)
class DraftProposal:
    type: str
    target: str
    reason: str
    # True when the proposal depends on train times; certainty then comes from the tool.
    time_bound: bool = False


@dataclass
class Draft:
    reply: str
    intent: Intent
    chips: list[str] = field(default_factory=list)
    proposals: list[DraftProposal] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    tool_used: ToolName | None = None
    tool_certainty: Certainty | None = None
    tool_source: str | None = None  # "mock": example data / "snapshot": shared timetable
    tool_basis: str | None = None  # what snapshot times rest on; always shown to the user
    tool_place: str | None = None  # place the tool was run for (subject of the answer)
    evidence: str = ""  # tool result as text; times in the reply are checked against it
    train_count: int | None = None  # trains in the tool result, bounds "N편" claims
    # Documents found only by similarity (not by the screen or a name in the question).
    vector_docs: tuple[str, ...] = ()
    # Documents found by the screen context or by a name in the question.
    named_docs: tuple[str, ...] = ()
    plan: dict | None = None  # plan conditions the model heard; checked in _plan_handoff
    steps: list[ChatStep] = field(default_factory=list)  # what was looked up, in order


@dataclass(frozen=True)
class SavedSelection:
    """The logged-in user's saved items, as far as the chat needs them."""

    placeIds: tuple[str, ...] = ()
    mustPlaceIds: tuple[str, ...] = ()
    trains: tuple[tuple[str, str, str], ...] = ()  # (train no, ride|observe, station id)


@dataclass(frozen=True)
class ResolvedContext:
    """Request context with ids checked against the place catalog."""

    raw: ChatContext
    content: retrieval.Content
    focused: retrieval.Place | None
    pins: tuple[retrieval.Place, ...]
    unknown_ids: tuple[str, ...]
    mentioned: retrieval.Place | None = None  # first catalog place named in the message
    message: str = ""
    # Place the user named most recently in earlier turns: the default when this message
    # and the screen name none ("그럼 오후 열차는?").
    recalled: retrieval.Place | None = None
    unknown_already_told: bool = False  # an earlier reply already carried the notice
    first_turn: bool = True  # no earlier turns: the only time the persona greets
    timetable: timetable_service.Timetable | None = None  # None: train tools use example data
    saved: SavedSelection = SavedSelection()
    # Everything the user typed in this conversation (earlier turns and this message):
    # what a responder says the user "said" is checked against it.
    user_text: str = ""
    screen_pins: tuple[str, ...] = ()  # pins the request itself carried, before saved places
    assistant_turns: tuple[str, ...] = ()  # earlier replies, oldest first

    @property
    def last_reply(self) -> str:
        return self.assistant_turns[-1] if self.assistant_turns else ""

    @property
    def known_ids(self) -> list[str]:
        """Known context ids, the open marker first."""
        return [p.id for p in ([self.focused] if self.focused else []) + list(self.pins)]

    @property
    def anchor(self) -> retrieval.Place | None:
        """What "here/this" refers to: the open marker, else the first pin on the screen.
        Saved places count as pins too, but a place saved some time ago is not what the
        conversation is about: the place the user last named comes before those."""
        if self.focused:
            return self.focused
        if self.saved.placeIds:
            on_screen = next((p for p in self.pins if p.id in self.screen_pins), None)
            if on_screen or self.recalled:
                return on_screen or self.recalled
        return self.pins[0] if self.pins else None

    @property
    def station(self) -> retrieval.Place | None:
        candidates = ([self.focused] if self.focused else []) + list(self.pins)
        return next((p for p in candidates if p.kind == "station"), None)


def persona() -> PersonaResponse:
    return PersonaResponse(
        name=prompts.PERSONA_NAME,
        greeting=prompts.GREETING,
        loading=PersonaLoading(**prompts.LOADING),
    )


class _Stages:
    """Milliseconds spent per stage of one request (retrieval, model calls, tool, ...)."""

    def __init__(self):
        self.ms: dict[str, int] = {}
        self._started = time.perf_counter()

    @contextmanager
    def timed(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = self.ms.get(name, 0) + round((time.perf_counter() - started) * 1000)

    def total_ms(self) -> int:
        return round((time.perf_counter() - self._started) * 1000)


def warm_up(settings: Settings) -> None:
    """Load chat content at startup so a broken file shows in the deploy log, not in the
    first user's request. Never raises: the API (health, docs) must still start."""
    try:
        content = retrieval.load_content(settings.content_dir)
    except retrieval.ContentError as exc:
        logger.error("chat content unavailable, chat will answer 503: %s", exc)
        return
    logger.info("chat content loaded: %d places, %d documents", len(content.places), len(content.docs))
    if not settings.chat_mock and not settings.openai_api_key.get_secret_value():
        logger.warning("NEAREAST_CHAT_MOCK=false but no API key: answers will be fallback")


def answer(
    request: ChatRequest, settings: Settings, saved: SavedSelection | None = None
) -> ChatResponse:
    """`saved` is what a logged-in user has saved: those places count as pins, on top of
    the pins the request carries, so they are known to Nearie and never proposed again."""
    try:
        content = retrieval.load_content(settings.content_dir)
    except retrieval.ContentError:
        logger.exception("chat content unavailable")
        raise ChatUnavailableError from None
    saved = saved or SavedSelection()
    screen_pins = tuple(request.context.pins)
    if saved.placeIds:
        pins = list(dict.fromkeys([*saved.placeIds, *screen_pins]))
        request = request.model_copy(update={
            "context": request.context.model_copy(update={"pins": pins})})
    context = dataclasses.replace(
        _resolve(request.context, content, request.message, request.history),
        timetable=timetable_service.load_or_none(settings.content_dir),
        saved=saved, screen_pins=screen_pins,
        user_text="\n".join([*(t.content for t in request.history if t.role == "user"),
                             request.message]),
    )
    if (agreed := _agreement(context, "mock" if settings.chat_mock else "llm")) is not None:
        return agreed  # "ㅇㅇ" after a plan card or a plan question: no responder needed
    if settings.chat_mock:
        return _finalize(_mock_draft(request.message, context), context, "mock")

    # Model path: bounded by one deadline for the whole request, timed per stage.
    stages = _Stages()
    deadline = llm.Deadline(settings.chat_deadline_seconds)
    response = failure = None
    try:
        draft = _llm_draft(request, context, settings, deadline, stages)
        with stages.timed("finalize"):
            response = _finalize(draft, context, "llm")
    except llm.LLMError as exc:
        failure = str(exc)
        logger.warning("llm responder failed, answering with the built-in responder: %s", exc)
    except Exception as exc:  # a demo must not die on an unexpected model/tool error
        failure = f"crash: {type(exc).__name__}"
        logger.exception("llm responder crashed, answering with the built-in responder")
    if response is None:
        with stages.timed("fallback"):
            response = _finalize(_mock_draft(request.message, context), context, "fallback")
    total = stages.total_ms()
    llm.record_request(settings, {
        "mode": response.mode, "total_ms": total, "stages": dict(stages.ms),
        "intent": response.intent, "tool_used": response.tool_used, "failure": failure,
    })
    if total > SLOW_REQUEST_MS:
        logger.warning("slow chat request: %d ms, stages %s", total, dict(stages.ms))
    return response


def _resolve(
    context: ChatContext, content: retrieval.Content, message: str = "", history=()
) -> ResolvedContext:
    unknown: list[str] = []
    focused = None
    if context.focused is not None:
        focused = content.place(context.focused)
        if focused is None:
            unknown.append(context.focused)
    pins = []
    for pin in context.pins:
        place = content.place(pin)
        if place is None:
            if pin not in unknown:
                unknown.append(pin)
        elif place not in pins:
            pins.append(place)
    # Only the user's own words count: assistant text names places it merely suggested.
    recalled = next(
        (p for t in reversed(history) if t.role == "user"
         and (p := content.place_in_text(t.content)) is not None),
        None,
    )
    return ResolvedContext(
        context, content, focused, tuple(pins), tuple(unknown),
        content.place_in_text(message), message, recalled,
        any(t.role == "assistant" and UNKNOWN_MARK in t.content for t in history),
        not history,
        assistant_turns=tuple(t.content for t in history if t.role == "assistant"),
    )


def _subject(draft: Draft, context: ResolvedContext) -> retrieval.Place | None:
    """The place the answer is about: tool target, else named place, else open marker."""
    if draft.tool_place:
        return context.content.place(draft.tool_place)
    return context.mentioned or context.focused


def _clock_claims(text: str) -> list[tuple[frozenset[int], int | None]]:
    """Clock times in text as (possible 24h hours, minute or None for "N시/N시대")."""
    claims = []
    for m in CLOCK.finditer(text):
        if m.group(1) is not None:
            # "10:00대" names the hour band, like "10시대": check the hour, not the minute.
            band = m.group(2) == "00" and text[m.end():m.end() + 1] == "대"
            claims.append((frozenset({int(m.group(1))}), None if band else int(m.group(2))))
            continue
        meridiem, hour = m.group(3), int(m.group(4))
        minute = int(m.group(5)) if m.group(5) else 30 if m.group(6) else None
        if meridiem in ("오후", "저녁", "밤") and hour < 12:
            hours = {hour + 12}
        elif meridiem in ("오전", "아침", "낮") or hour >= 12:
            hours = {hour}
        else:  # bare "4시" may mean 04:00 or 16:00
            hours = {hour, hour + 12}
        claims.append((frozenset(hours), minute))
    return claims


def unverified_time_claims(reply: str, evidence: str, train_count: int | None) -> list[str]:
    """Clock times and train counts in the reply that the evidence does not contain."""
    known = {(h, m) for hours, m in _clock_claims(evidence) for h in hours}
    known_hours = {h for h, _ in known}
    problems = []
    for hours, minute in _clock_claims(reply):
        ok = hours & known_hours if minute is None else any((h, minute) in known for h in hours)
        if not ok:
            problems.append(f"time {sorted(hours)}:{minute}")
    for m in TRAIN_COUNT.finditer(reply):
        count = int(m.group(1))
        if m.group(0) not in evidence and not (train_count is not None and count <= train_count):
            problems.append(f"count {count}")
    return problems


def _passing_is_relevant(
    draft: Draft, context: ResolvedContext, subject: retrieval.Place, sources: list[str]
) -> bool:
    """A passing station that is merely open on the map must not tag unrelated answers
    (a greeting, a question about something else) with the boarding rule."""
    if draft.tool_place == subject.id or context.mentioned == subject:
        return True  # the question or the tool is explicitly about this station
    return (
        draft.intent == "train_lookup"
        or subject.id in sources
        or any(w in context.message for w in BOARDING_WORDS)
    )


# An answer that opens by saying there is nothing confirmed about what was asked.
NO_INFO_OPENING = re.compile(r"확인된 정보(?:가|는|도)?\s*(?:아직\s*)?없|확인된 정보로[^.!?]*어려")
MARKET_TALK = ("장날", "장이 서", "장이 열", "장 서", "장이 안")
HAPYEONG_DOC = "safety_hapyeong_crossing"


def _asked_docs(context: ResolvedContext) -> list[str]:
    """Documents the question is plainly about, whatever the responder cites: the
    Hapyeong crossing case, and the market for a market-day question. They are given to
    the model and always listed as sources."""
    message, docs = context.message, context.content.docs
    asked = []
    if "하평" in message:
        asked.append(HAPYEONG_DOC)
    about_market = context.mentioned is not None and context.mentioned.id == tools.MARKET_ID
    if about_market or any(word in message for word in MARKET_TALK):
        asked.append(tools.MARKET_ID)
    return [doc_id for doc_id in asked if doc_id in docs]


# --- walking time between two places: computed here, never left to the responder ---

WALK_WORDS = ("걸어", "걸으", "도보", "걷")
# ...and asking how long or whether: "걸어갈 만한 데 있어?" is a nearby question, not this.
WALK_ASK_WORDS = ("얼마나", "몇 분", "걸려", "걸리", "갈 수 있", "가까", "멀어", "멀까", "먼가")
WALK_ESTIMATE = ("{a}에서 {b}까지는 걸어서 약 {minutes}분이에요. 직선거리에 1.3을 곱해 어림한 값이라 "
                 "실제 길에 따라 달라질 수 있어요.")
WALK_NO_POSITION = "{a}에서 {b}까지는 아직 위치 정보가 없어 도보 시간을 계산할 수 없어요."
# A question about going by train or road is not a walking question, whatever tool the
# responder asks for ("기차로 얼마나 걸려?").
RIDE_WORDS = ("기차", "열차", "KTX", "버스", "택시", "차로", "자전거")
WALK_TOO_FAR = "{a}에서 {b}까지는 걸어가기에는 멀어요. 열차나 버스·택시를 권해요."


def _walk_pair(context: ResolvedContext) -> tuple[retrieval.Place, retrieval.Place] | None:
    """(from, to) when the message asks how long a walk between two places takes: two
    places named in that order, or one named and "here" (the open marker or first pin)."""
    message = context.message
    if not (any(word in message for word in WALK_WORDS)
            and any(word in message for word in WALK_ASK_WORDS)):
        return None
    if any(word in message for word in RIDE_WORDS):
        return None  # comparing with, or asking about, another way to go
    named = context.content.places_in_text(message)
    if len(named) >= 2:
        return named[0], named[1]
    here = context.anchor
    if len(named) == 1 and here is not None and here != named[0]:
        return here, named[0]
    return None


def _walk_draft(a: retrieval.Place, b: retrieval.Place) -> Draft:
    """The whole answer is written from the computed result, so no minutes a responder
    made up can reach the user. Coordinates arriving in the catalog switch the estimate
    on; nothing here needs to change."""
    walk = recommend_service.walk_between(a, b)
    template = {None: WALK_ESTIMATE, "no_coordinates": WALK_NO_POSITION,
                "too_far": WALK_TOO_FAR}[walk.reason]
    reply = template.format(a=a.name, b=b.name, minutes=walk.minutes)
    chips = [f"{p.name} 열차 리듬" for p in (a, b) if p.kind == "station"][:1]
    chips += [f"{b.name} 근처", f"{a.name} 근처"]  # ones that lead nowhere are dropped later
    return Draft(reply=reply, intent="proposal", chips=chips, tool_used="walk_time",
                 tool_place=b.id, evidence=reply)


def market_day_sentence(date: dt.date) -> str:
    is_day, nearest = tools.market_day(date)
    if is_day:
        return f"{date.month}월 {date.day}일은 북평민속시장 장날이에요."
    weekday = prompts.WEEKDAYS[nearest.weekday()]
    return (f"{date.month}월 {date.day}일은 장날이 아니에요. "
            f"가장 가까운 장날은 {nearest.month}월 {nearest.day}일({weekday})이에요.")


def _with_market_day(reply: str, date: dt.date) -> str:
    """Replace whatever the responder said about market days with the computed verdict."""
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", reply)
                 if s and not any(w in s for w in MARKET_TALK)]
    return " ".join([*sentences, market_day_sentence(date)])


def _log_replaced(rule: str, original: str) -> None:
    """Keep what the responder actually said, so "why did it answer that?" can be traced."""
    logger.warning("rule replaced the reply [%s]; original: %r", rule, original[:300])


def _clip(reply: str, limit: int = MAX_REPLY_CHARS) -> str:
    """A chat bubble stays short: whole sentences up to `limit` characters."""
    if len(reply) <= limit:
        return reply
    kept = ""
    for sentence in re.split(r"(?<=[.!?…])\s+", reply):
        if len(kept) + len(sentence) + 1 > limit:
            # A long unbroken run (a train list) that would otherwise vanish: when most of
            # the bubble is still empty, keep its beginning up to an item boundary.
            room = limit - len(kept) - 2
            if len(kept) < limit // 2 and room > 20:
                piece = sentence[:room]
                # Cut between list items if possible ("…), " then ", "), else between words.
                cut = piece.rfind("), ") + 1
                for separator in (", ", " "):
                    if cut > room // 2:
                        break
                    cut = piece.rfind(separator)
                piece = piece[:cut] if cut > room // 2 else piece
                kept = f"{kept} {piece.rstrip(' ,')}…".strip()
            break
        kept = f"{kept} {sentence}".strip()
    return kept


def _located(place: retrieval.Place | None) -> bool:
    return place is not None and place.lat is not None and place.lng is not None


def _has_nearby(place: retrieval.Place, content: retrieval.Content) -> bool:
    """A "near X" chip leads somewhere: with coordinates, places within a short walk;
    without them, the example table."""
    if _located(place):
        return bool(recommend_service.nearby(content.places, place.id))
    return bool(tools.nearby_spots(place.id, walk_minutes=10**6))


# --- plan request: handed to the plan screen, never planned in the chat -----------

def asks_entry(message: str) -> bool:
    """A question about the tracks that is about going inside (or does not say where).
    False only when the user names a public place and nothing inside."""
    inside = any(w in message for w in _INSIDE_ASK)
    return inside or not any(w in message for w in _PUBLIC_ASK)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _without_invitations(reply: str) -> str:
    """The reply without sentences that send the user inside the barrier."""
    return " ".join(
        s for s in _sentences(reply)
        if not (any(w in s for w in _INSIDE_SAID) and not any(n in s for n in _NEGATION)))


# "가능할 수도 있지만 조심해요": leaves the door open. Asked about going inside, such a
# sentence goes, unless it is about a public place ("승강장에서는 괜찮아요").
_PERMISSION = ("가능할", "가능해", "괜찮", "해도 돼", "어도 돼", "조심하면", "조심해서", "조심해요")


def _without_hedges(reply: str) -> str:
    return " ".join(
        s for s in _sentences(reply)
        if not (any(w in s for w in _PERMISSION)
                and not any(n in s for n in (*_NEGATION, *ALTERNATIVE_WORDS, "도로", "인도"))))


_LEFTOVER_LINK = re.compile(r"^(?:대신|다만|하지만|그래도|그 대신)[,\s]+")


def _without_refusals(reply: str) -> str:
    """The reply without its "권하지 않아요" sentences, when something else is left. The
    sentence after a removed one loses its "대신"."""
    kept, dropped = [], False
    for sentence in _sentences(reply):
        if "권하지 않아요" in sentence:
            dropped = True
            continue
        kept.append(_LEFTOVER_LINK.sub("", sentence) if dropped else sentence)
        dropped = False
    return " ".join(kept) if kept else reply


def _safety_alternative(context: "ResolvedContext") -> str:
    """A public place to go instead, by name when a station that takes passengers is in play."""
    for place in (context.mentioned, context.focused, *context.pins, context.recalled):
        if place is not None and place.kind == "station" and place.passenger_stop is not False:
            return SAFETY_ALTERNATIVE_AT.format(station=place.name)
    return SAFETY_ALTERNATIVE


def _safe_reply(reply: str, context: "ResolvedContext") -> str:
    """Principle 6 on top of what the responder wrote: one line that draws the limit, the
    rest of the answer kept (minus anything that sends the user inside), a public
    alternative, and no line the conversation has already heard."""
    earlier = "\n".join(context.assistant_turns)
    body = _without_invitations(reply)
    if asks_entry(context.message):
        body = _without_hedges(body)
        lead = SAFETY_NO_AGAIN if SAFETY_NO in earlier else SAFETY_NO
        if SAFETY_NO in body and lead != SAFETY_NO:
            body = body.replace(SAFETY_NO, "").strip()
        if "권하지 않아요" not in body:
            body = f"{lead} {body}".strip()
        if not any(w in body for w in ALTERNATIVE_WORDS):
            body = f"{body} {_safety_alternative(context)}"
        return body
    # A public place was asked about: a refusal is not the answer. The limit is stated
    # once by the line below (or was, earlier in the conversation).
    body = body.replace(SAFETY_PUBLIC, "").replace(SAFETY_NO, "").strip()
    body = _without_refusals(body)
    told = any(m in earlier for m in ("차단기 밖", SAFETY_NO))
    says_where = "차단기 밖" in body or ("밖" in body and any(w in body for w in ("도로", "인도")))
    if not says_where:
        body = f"{SAFETY_PUBLIC_AGAIN if told else SAFETY_PUBLIC} {body}".strip()
    return body


# "ㅇㅇ", "응", "그래", "좋아": an answer to what was just said, not a question of its own.
AGREEMENT = re.compile(
    r"^\s*(?:ㅇㅇ+|ㅇㅋ+|ㅇ|응+|웅+|엉|어+|네+|넵|넹|예|그래+|그럼|좋아+|좋아요|좋네|오케이|오키|ok|okay|콜|"
    r"ㄱㄱ+|알겠어|알겠어요|알았어|그렇게\s?해|그렇게\s?해\s?줘|그걸로\s?해\s?줘|부탁해)[\s.!~ㅎㅋ]*$",
    re.IGNORECASE)
PLAN_CARD_MARK = "여행 조건을 정리했어요"
PLAN_AGREED = ("좋아요. 아래 카드에서 조건을 확인하고 일정 화면으로 이어가 주세요. "
               "일정은 일정 화면에서 만들어져요.")
PLAN_DATE_AGAIN = "날짜만 정하면 돼요. 아래에서 고르거나 날짜를 알려 주세요."
PLAN_PINS_AGAIN = "어디를 볼지만 알려 주세요. 예를 들면 \"동해역이랑 추암역 볼래\"처럼요."
PROPOSAL_AGREED = ("좋아요. 담기는 제안 카드에서 직접 눌러 주세요. 제가 대신 담지는 않아요.")


def is_agreement(message: str) -> bool:
    return AGREEMENT.match(message) is not None


def _agreement(context: "ResolvedContext", mode: str) -> ChatResponse | None:
    """A bare yes right after a plan answer continues that plan, whatever the responder
    would make of two letters. Other yeses go to the responder (see `_finalize`)."""
    if not is_agreement(context.message):
        return None
    last = context.last_reply
    if PLAN_CARD_MARK in last:
        return _plan_handoff(Draft(reply="", intent="plan_request"), context, mode,
                             lead=PLAN_AGREED)
    if last.startswith((PLAN_NEEDS_DATE[:10], PLAN_DATE_AGAIN[:8])):
        return _plan_handoff(Draft(reply="", intent="plan_request"), context, mode,
                             ask_again=True)
    if last.startswith((PLAN_NEEDS_PINS[:12], PLAN_PINS_AGAIN[:8])):
        return _plan_handoff(Draft(reply="", intent="plan_request"), context, mode,
                             ask_again=True)
    return None


# "일정 짜줘", "계획 세워줘", "이대로 하루 만들어줘": a thing to plan, then asking to make it.
# Talking about plans ("계획 짜는 팁", "일정은 내가 짤게") does not match.
PLAN_REQUEST = re.compile(
    r"(?:일정|계획|하루|코스|동선|스케줄|루트)[^.?!]{0,15}?"
    r"(?:짜|세워|세우|만들어|잡아|구성해)\s?(?:줘|주세요|주실|줄래|주라|주면|달라|서\s)"
)
# "일정은 내가 직접 짤게": the user keeps the planning. Not a request, whatever the responder says.
OWN_PLAN = re.compile(
    r"(?:내가|제가|직접|알아서|혼자)[^.?!]{0,10}(?:짤게|짤래|짤 ?거|짜 ?볼게|세울게|정할게|할게)")
OWN_PLAN_REPLY = "좋아요, 일정은 직접 짜시면 돼요. 궁금한 열차나 장소가 있으면 언제든 물어봐 주세요."
# "계획 짜는 팁 있어?": asking how to plan is a question, not a request to have one made.
PLAN_ADVICE = re.compile(r"(?:짜는|세우는|만드는)\s*(?:팁|법|방법|요령|노하우)|(?:팁|요령|노하우)[^.?!]{0,6}(?:있|알려|줘)")
PLAN_ADVICE_REPLY = ("일정은 일정 화면에서 직접 짤 수 있어요. 보고 싶은 열차와 가고 싶은 곳을 먼저 담아 두면 "
                     "편해요. 역을 열어 두고 열차가 언제 몰리는지 물어보는 것도 좋아요.")
PLAN_HANDOFF = ("{month}월 {day}일 여행 조건을 정리했어요. {what} "
                "아래 카드에서 조건을 확인한 뒤 일정 화면에서 일정을 만들어 주세요.")
PLAN_OBSERVE_ONLY = "여객열차가 서지 않는 역({names})은 관찰만 할 수 있어요."
# Saying where one means to go ("동해역 볼 테니까", "추암 가고 싶어"), as opposed to asking
# about a place ("묵호등대는 언제 볼 수 있어?").
_VISIT_TALK = re.compile(
    r"볼\s?(?:테니|거야|게|래|까|려고|\s?예정)|보고\s?싶|보려고|보러|"
    r"갈\s?(?:테니|거야|게|래|까|려고|\s?예정)|가고\s?싶|가려고|가\s?볼|"
    r"들르|들를|들러|방문할|방문하고|구경할|구경하고|찍을|찍고\s?싶")
_SPOKEN_CLOCK = re.compile(
    r"(오전|오후|아침|낮|저녁|밤)?\s*(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분|(반))?\s*"
    r"(?:쯤|경|정도)?\s*(부터|에\s?출발|출발|에\s?시작|시작|까지|에\s?끝|에\s?마무리|에\s?돌아)")
PLAN_NEEDS_PINS = ("어느 역에서 출발하거나 어디를 방문하고 싶으세요? 대화로 알려 주셔도 되고, "
                   "지도에서 관심 장소를 담아 주셔도 돼요.")
PLAN_NEEDS_DATE = "어느 날짜로 짤까요? 날짜를 알려 주시면 지금 정한 조건을 일정 화면에서 확인할 수 있어요."
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_KOREAN_DATE = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
_RELATIVE_DAYS = {"오늘": 0, "내일": 1, "모레": 2}
_TRAVEL_TALK = re.compile(r"출발|도착|떠나|돌아|복귀|부터|까지|시작|끝내|마무리")
_DAY_TALK = re.compile(r"\d{1,2}\s*[월일]|\d{4}-\d|\d{1,2}/\d{1,2}|오늘|내일|모레|요일|주말")


def is_plan_request(message: str) -> bool:
    return PLAN_REQUEST.search(message) is not None


def date_in_message(message: str, today: dt.date) -> dt.date | None:
    """A date the user spelled out: 2026-11-21, 11월 21일 (the next one), 오늘/내일/모레."""
    try:
        if found := _ISO_DATE.search(message):
            return dt.date(*map(int, found.groups()))
        if found := _KOREAN_DATE.search(message):
            month, day = map(int, found.groups())
            date = dt.date(today.year, month, day)
            return date if date >= today else dt.date(today.year + 1, month, day)
    except ValueError:
        return None
    for word, days in _RELATIVE_DAYS.items():
        if word in message:
            return today + dt.timedelta(days=days)
    return None


def _wants_plan(intent: str, message: str) -> bool:
    """A plan request by the responder's reading or by the wording. Asking to plan
    something on the tracks is a safety question first."""
    if (any(word in message for word in RISK_WORDS) or OWN_PLAN.search(message)
            or PLAN_ADVICE.search(message)):
        return False
    return intent == "plan_request" or is_plan_request(message)


def _plan_turns(context: ResolvedContext) -> list[str]:
    """The user's turns that are about the trip, oldest first, this message last: earlier
    turns count when they ask for a plan or talk about setting out, getting back or where
    they mean to go. A place merely asked about ("묵호등대는 언제…") is not part of a plan."""
    turns = (context.user_text or context.message).split("\n")
    return [*(t for t in turns[:-1]
              if is_plan_request(t) or _TRAVEL_TALK.search(t) or _VISIT_TALK.search(t)),
            turns[-1]]


def spoken_times(text: str) -> tuple[str | None, str | None]:
    """Start and end of the day as the user spelled them out: "9시부터 오후 5시까지".
    An hour that could be morning or evening with nothing to tell which is left out."""
    start = end = None
    for half, hour, minute, thirty, role in _SPOKEN_CLOCK.findall(text):
        hour, minute = int(hour), 30 if thirty else int(minute or 0)
        if hour > 24 or minute > 59:
            continue
        is_start = role.startswith("부터") or "출발" in role or "시작" in role
        if half in ("오후", "저녁", "밤") and hour < 12:
            hour += 12
        elif half == "낮" and hour < 6:
            hour += 12
        elif not half and hour <= 12:
            if is_start and hour < 6:
                continue  # "3시부터": 03:00 or 15:00
            if not is_start and hour <= 10:
                hour += 12  # a day that ends "5시까지" ends at 17:00
        if hour > 23:
            continue
        value = f"{hour:02d}:{minute:02d}"
        if is_start:
            start = value
        else:
            end = value
    return start, end


def _plan_conditions(
    date: dt.date, said: dict, context: ResolvedContext
) -> PlanningConditionsDto:
    """Planning conditions from what is saved, what is pinned on the screen and what the
    user said. Values the responder reported are used only after checking them here."""
    content = context.content

    # A condition counts as said only if the user's own words carry it (see _plan_turns).
    # Anything else is the responder's guess (seen on the real model: a saved station
    # given as the departure) and is left out.
    turns = _plan_turns(context)
    spoken = "\n".join(turns)
    named = {p.id for p in content.places_in_text(spoken)}
    hours = {int(h) for h in re.findall(r"(?<!\d)(\d{1,2})\s*(?:시|:\d{2})", spoken)}

    def place(value: object) -> str | None:
        return value if isinstance(value, str) and value in named else None

    def clock(value: object) -> str | None:
        if not (isinstance(value, str) and _HHMM.fullmatch(value)):
            return None
        hour = int(value[:2])
        return value if hour in hours or hour - 12 in hours else None  # "7시" may be 19:00

    heard_start, heard_end = spoken_times(spoken)
    start = clock(said.get("start_time")) or heard_start
    end = clock(said.get("end_time")) or heard_end
    if start and end and end <= start:
        end = None  # not a same-day range: leave the end for the plan screen
    # Departure and arrival stay strict: only what the responder reported and the user's
    # words confirm. Every other place the user named for this trip is a place to visit.
    departure, arrival = place(said.get("start_place")), place(said.get("end_place"))
    visits = [p.id for turn in turns for p in content.places_in_text(turn)
              if p.id not in (departure, arrival)]
    # Required: places saved as "must", the pins on the screen (a pin is a decision) and
    # the places named for the trip. Places saved as "interested" are known to the plan
    # screen from the saved list.
    known = {p.id for p in context.pins}
    required = list(dict.fromkeys(
        [*(p for p in (*context.saved.mustPlaceIds, *context.screen_pins) if p in known),
         *visits]))
    # TODO(plan): how ride/observe is expressed is still being agreed with the planner
    # owner. Until then both fields are filled: selectedTrainIds (train numbers, what the
    # planner reads today) and selectedTrains (what each train is for, and where).
    trains = [SelectedTrainDto(trainNo=no, action=action, stationId=station)
              for no, action, station in context.saved.trains]
    return PlanningConditionsDto(
        travelDate=date,
        startTime=start,
        endTime=end,
        departurePlaceId=departure,
        arrivalPlaceId=arrival,
        requiredPlaceIds=required,
        companions=context.raw.companions,
        maxWalkingMinutes=companions.rule(context.raw.companions).maxWalkingMinutes,
        selectedTrainIds=list(dict.fromkeys(train.trainNo for train in trains)),
        selectedTrains=trains,
    )


def _names(places, limit: int = 4) -> str:
    return ", ".join(p.name for p in places[:limit]) + (" 등" if len(places) > limit else "")


def _plan_handoff(draft: Draft, context: ResolvedContext, mode: str, *,
                  lead: str | None = None, ask_again: bool = False) -> ChatResponse:
    """The whole answer to a plan request is written here: whatever the responder said
    (an itinerary, times, places to add) is dropped. With every condition known the
    client gets an action to open the plan screen; otherwise one question, places first.
    `lead` replaces the opening when the user agrees to a card already shown; `ask_again`
    is a bare yes to a question that a yes does not answer."""
    raw = context.raw
    content = context.content
    today = dt.datetime.now(KST).date()
    said = draft.plan or {}
    # The day: this message, then the screen, then the latest earlier turn about the trip.
    earlier = _plan_turns(context)[:-1]
    date = date_in_message(context.message, today) or raw.date or next(
        (d for t in reversed(earlier) if (d := date_in_message(t, today)) is not None), None)
    # A date from the responder is taken only when the user did speak of a day earlier.
    if (date is None and isinstance(said.get("date"), str)
            and _DAY_TALK.search(context.user_text or context.message)):
        try:
            date = dt.date.fromisoformat(said["date"])
        except ValueError:
            date = None
    if draft.reply and draft.reply != PLAN_HANDOFF:
        logger.info("plan request: responder reply dropped, server text used")

    action = None
    conditions = _plan_conditions(date or today, said, context)
    hasSpokenPlace = bool(conditions.departurePlaceId or conditions.arrivalPlaceId)
    visits = [p for p in map(content.place, conditions.requiredPlaceIds) if p is not None]
    inPlay = [*visits, *(p for p in context.pins if p not in visits)]
    stations = [p for p in inPlay if p.kind == "station"]
    chips = [f"{p.name} 열차 리듬" for p in stations[:1]]
    chips += [f"{p.name} 근처" for p in inPlay[:1] if _has_nearby(p, content)]
    if not inPlay and not context.saved.trains and not hasSpokenPlace:
        reply, chips = (PLAN_PINS_AGAIN if ask_again else PLAN_NEEDS_PINS), []
    elif date is None:
        reply = PLAN_DATE_AGAIN if ask_again else PLAN_NEEDS_DATE
        # Each chip is a complete answer: tapping it comes back as a plan request with a date.
        days = [today, today + dt.timedelta(days=1)]
        snapshot = (context.timetable.meta.get("target_date") if context.timetable else None)
        try:
            target = dt.date.fromisoformat(snapshot) if snapshot else None
        except ValueError:
            target = None
        if target is not None and target > days[-1]:
            days.insert(0, target)
        labels = {today: "오늘", today + dt.timedelta(days=1): "내일"}
        chips = [f"{labels.get(d) or f'{d.month}월 {d.day}일'}로 일정 짜줘" for d in days]
    else:
        what = []
        if visits:
            what.append(f"방문할 곳은 {len(visits)}곳({_names(visits)})이에요.")
        others = [p for p in context.pins if p not in visits]
        if others:
            what.append(f"담아 둔 곳 {len(others)}곳({_names(others)})도 일정 화면에서 고를 수 있어요.")
        if context.saved.trains:
            what.append(f"담아 둔 열차 {len({no for no, _, _ in context.saved.trains})}편도 조건에 있어요.")
        if hasSpokenPlace:
            what.append("출발·도착 장소는 대화에서 말씀하신 대로예요.")
        if conditions.startTime or conditions.endTime:
            what.append("시간은 {}~{}이에요.".format(
                conditions.startTime or "", conditions.endTime or ""))
        if conditions.maxWalkingMinutes is not None:
            what.append(companions.rule(raw.companions).reason)
        passing = [p for p in visits if p.kind == "station" and p.passenger_stop is False]
        if passing:
            what.append(PLAN_OBSERVE_ONLY.format(names=_names(passing)))
        reply = PLAN_HANDOFF.format(month=date.month, day=date.day, what=" ".join(what))
        if lead:
            reply = lead
        action = ChatAction(type="plan_request", payload=conditions)
    for chip in DEFAULT_CHIPS:
        if len(chips) < 2 and chip not in chips:
            chips.append(chip)
    return ChatResponse(reply=reply, intent="plan_request", action=action,
                        chips=chips[:MAX_CHIPS], mode=mode)


def _finalize(draft: Draft, context: ResolvedContext, mode: str = "mock") -> ChatResponse:
    """Every rule here holds no matter what the responder (mock or LLM) produced."""
    if _wants_plan(draft.intent, context.message):
        return _plan_handoff(draft, context, mode)
    if draft.intent == "plan_request" and PLAN_ADVICE.search(context.message):
        draft = dataclasses.replace(  # a question about planning: answered, nothing handed over
            draft, intent="smalltalk", reply=PLAN_ADVICE_REPLY, proposals=[], plan=None)
    elif draft.intent == "plan_request" and OWN_PLAN.search(context.message):
        draft = dataclasses.replace(  # planning stays with the user: nothing is handed over
            draft, intent="smalltalk", reply=OWN_PLAN_REPLY, proposals=[], plan=None)
    elif draft.intent == "plan_request":  # asked about the tracks: answered as safety
        draft = dataclasses.replace(draft, intent="safety", plan=None)
    raw = context.raw
    content = context.content
    pinned = {p.id for p in context.pins}
    blocked = set(raw.dismissed) | set(context.unknown_ids)
    reply = _clip(draft.reply, MAX_AGENT_REPLY_CHARS if len(draft.steps) > 1 else MAX_REPLY_CHARS)
    chips = list(draft.chips)
    sources = [s for s in dict.fromkeys(draft.sources) if s in content.docs]
    if draft.intent != "smalltalk" and draft.tool_used != "walk_time":
        sources += [doc_id for doc_id in _asked_docs(context) if doc_id not in sources]

    # "Nothing confirmed about that" while citing documents that were only similar to the
    # question (not on screen, not named in it): the answer did not come from those
    # documents, so they are not its sources. A partial answer that cites a document the
    # question did name ("입장료는 확인된 정보가 없어요" about a named place) is left alone.
    opening = re.split(r"(?<=[.!?])\s+", reply, maxsplit=1)[0]
    disowned = (draft.tool_used is None and NO_INFO_OPENING.search(opening) is not None
                and all(s in draft.vector_docs for s in sources))
    if disowned and sources:
        _log_replaced("no information, yet sources", reply)
        sources = []

    # A place may be proposed only when something ties it to this conversation: the user
    # put it in play (screen, pins, named in the message), a tool returned it, or a
    # document about what is in play (or one the answer cites) is also about that place.
    # A model's own idea of what is nearby is not a proposal. The built-in responder only
    # proposes from tools, so this guards the model path.
    grounded = None
    if mode == "llm":
        grounded = set(context.known_ids) | {p.id for p in content.places_in_text(context.message)}
        if context.recalled is not None:
            grounded.add(context.recalled.id)
        for doc_id in (*sources, *draft.named_docs):
            grounded.add(doc_id)
            grounded.update(content.docs[doc_id].places)
        grounded.update(pid for pid in content.places if f'"{pid}"' in draft.evidence)
    offered = [p for p in draft.proposals
               if content.place(p.target) is not None
               and (grounded is None or p.target in grounded)
               and not disowned]
    if len(offered) < len([p for p in draft.proposals if content.place(p.target) is not None]):
        logger.warning("ungrounded proposals dropped: %s", ", ".join(
            p.target for p in draft.proposals if p not in offered))
    proposals = []
    # "Leave it empty" is the user's choice (principle 2): nothing is proposed on top of it.
    declined = any(w in context.message for w in KEEP_EMPTY_WORDS)
    if raw.depth != "reference" and not declined:  # reference: information only
        for p in offered:
            if p.target in blocked:
                continue
            if p.target == draft.tool_place:
                continue  # the place the gap/nearby lookup was about is not a suggestion
            if (p.type == "add") == (p.target in pinned):  # no-op add/remove
                continue
            if any((q.type, q.target) == (p.type, p.target) for q in proposals):
                continue  # the same proposal twice is one proposal
            proposals.append(
                Proposal(
                    type=p.type,
                    target=p.target,
                    reason=p.reason,
                    needs_confirm=True,
                    certainty=draft.tool_certainty if p.time_bound else None,
                )
            )

    # Times only from tools or cited documents: every clock time and train count in the
    # reply must appear there, even when some other tool was used.
    cited = "\n".join(content.docs[s].body for s in sources)
    evidence = f"{draft.evidence}\n{cited}"
    unverified = unverified_time_claims(reply, evidence, draft.train_count)
    if unverified and draft.tool_used is None and sources:
        # An answer from documents (opening hours, a date): a time the document does not
        # give, such as one worked out from it, costs that sentence, not the whole answer.
        # Train answers stay all-or-nothing below: half a timetable would mislead.
        kept = [sentence for sentence in re.split(r"(?<=[.!?])\s+", reply)
                if not unverified_time_claims(sentence, evidence, draft.train_count)]
        if kept:
            _log_replaced(f"sentences with unverified time ({', '.join(unverified)})", reply)
            reply, unverified = " ".join(kept), []
    if unverified:
        _log_replaced(f"unverified time ({', '.join(unverified)})", reply)
        reply = NO_TIME_WITHOUT_TOOL
    # Knowledge only from documents: an ungrounded answer becomes the fixed no-info reply.
    # A proposal-intent answer counts as grounded when the model did propose catalog places
    # (even if depth/pins/dismissed filtered them out) or the user is declining to fill time.
    declining = declined
    ungrounded = draft.tool_used is None and not sources and (
        draft.intent == "knowledge"
        or (draft.intent == "proposal" and not offered and not declining)
    )
    if ungrounded and reply != NO_TIME_WITHOUT_TOOL and is_agreement(context.message):
        reply = PROPOSAL_AGREED if "담" in context.last_reply else ACK_REPLY  # a yes, not a question
        draft = dataclasses.replace(draft, intent="smalltalk")
        proposals = []
    elif ungrounded and reply != NO_TIME_WITHOUT_TOOL:
        if not reply.startswith(NO_INFO):
            _log_replaced(f"no documents ({draft.intent})", reply)
        pointing = (any(w in context.message for w in DEICTIC_WORDS)
                    and context.anchor is None and context.mentioned is None)
        reply = f"{NO_INFO} {NO_INFO_HINT}" if pointing else NO_INFO
        chips = [c for c in chips if content.place_in_text(c) is not None]
        proposals = []  # nothing to go by: no places are offered on the back of it either
    # Safety (principle 6): a clear "not recommended" plus a public alternative, always.
    # Only when the question itself is about tracks/crossings: a safe question the model
    # happened to label "safety" (e.g. photos on the platform) keeps its real answer.
    if draft.intent == "safety" and any(w in context.message for w in RISK_WORDS):
        safe = _safe_reply(reply, context)
        if safe != reply:
            _log_replaced("safety wording", reply)
            reply = safe
        if "safety_track_crossing" in content.docs and "safety_track_crossing" not in sources:
            sources.insert(0, "safety_track_crossing")
    elif "권하지 않아요" in reply and "권하지 않아요" in "\n".join(context.assistant_turns):
        # The refusal was given earlier and this message does not ask about the tracks
        # ("안전하게 볼 수 있는 곳"): the answer is the alternative, not the refusal again.
        reply = _without_refusals(reply)
    # A companion rule that shaped the places offered is said out loud, never silent.
    who = companions.rule(raw.companions)
    shaped = ((draft.tool_used == "nearby_spots" and draft.tool_source == tools.COORDINATES)
              or (draft.tool_used == "station_rhythm" and proposals))
    if who.reason and who.reason not in reply and shaped and draft.intent != "safety":
        reply = f"{reply.rstrip()} {who.reason}"
    # Example data is always labelled.
    if draft.tool_source == tools.MOCK and MOCK_NOTE not in reply:
        reply = f"{MOCK_NOTE} {reply}"
    # Snapshot times are estimates: the answer always says what they rest on.
    if draft.tool_source == tools.SNAPSHOT and draft.tool_basis and not unverified:
        # Sentence by sentence: the model mentioning one basis must not hide the other.
        for sentence in re.split(r"(?<=[.])\s+", draft.tool_basis):
            mark = PASSING_MARK if PASSING_MARK in sentence else BASIS_MARK
            if mark not in reply:
                reply = f"{reply.rstrip()} {sentence}"
    # Passing stations: no boarding, observation only, with the rule as a source.
    subject = _subject(draft, context)
    if (subject is not None and subject.kind == "station" and subject.passenger_stop is False
            and draft.tool_used != "walk_time"  # a walk there is not about boarding
            and _passing_is_relevant(draft, context, subject, sources)):
        if "관찰" not in reply:  # any wording of "observe only" is enough
            reply = f"{reply.rstrip()} {PASSING_NOTE}"
        if PASSING_DOC in content.docs and PASSING_DOC not in sources:
            sources.append(PASSING_DOC)
    # Market day is date arithmetic: computed here, never left to the responder.
    about_market = tools.MARKET_ID in sources or (
        context.mentioned is not None and context.mentioned.id == tools.MARKET_ID
    )
    if raw.date is not None and about_market:
        reply = _with_market_day(reply, raw.date)
    if context.unknown_ids:
        # Ids are for developers (log), not for the user; say it once per conversation.
        logger.warning("unknown place ids ignored: %s", ", ".join(context.unknown_ids))
        if not context.unknown_already_told:
            notice = (
                UNKNOWN_FOCUSED if raw.focused in context.unknown_ids else UNKNOWN_PINS
            )
            reply = f"{notice} {reply}"

    # Chips must lead somewhere: no "nearby" chip for a place with nothing near it. The
    # same goes for the chips added below to keep the conversation going.
    def leads_somewhere(chip: str) -> bool:
        place = content.place_in_text(chip)
        return not (place is not None and any(w in chip for w in NEARBY_WORDS)
                    and not _has_nearby(place, content))

    kept = []
    for chip in chips:
        if leads_somewhere(chip) and chip not in kept:
            kept.append(chip)
    # At least two chips so a conversation can continue by tapping alone.
    fillers = []
    if subject is not None and subject.kind == "station":
        fillers += [f"{subject.name} 열차 리듬", f"{subject.name} 근처"]
    fillers += DEFAULT_CHIPS + ["묵호역 열차 리듬"]
    for chip in fillers:
        if len(kept) >= 2:
            break
        if chip not in kept and leads_somewhere(chip):
            kept.append(chip)
    chips = kept

    return ChatResponse(
        reply=reply,
        intent=draft.intent,
        chips=chips[:MAX_CHIPS],
        proposals=proposals,
        sources=sources,
        tool_used=draft.tool_used,
        steps=draft.steps if mode == "llm" else [],
        mode=mode,
    )


# --- LLM responder: one structured call, a second only after a tool request ------

MAX_PROMPT_PLACES = 40


def _relevant_places(context: ResolvedContext, docs) -> list[retrieval.Place]:
    """The places a request can be about, instead of the whole catalog: every station
    (there are few, and they are what the service is about), what is on the screen, what
    the user named in this conversation, the places the retrieved documents are about,
    and what is within a short walk of the screen and the named places. Tool results
    name their own places, so those need not be listed here."""
    content = context.content
    chosen: dict[str, retrieval.Place] = {}

    def add(place: retrieval.Place | None) -> None:
        if place is not None and len(chosen) < MAX_PROMPT_PLACES:
            chosen.setdefault(place.id, place)

    in_play = [context.focused, *context.pins,
               *content.places_in_text(context.user_text or context.message), context.recalled]
    for place in in_play:
        add(place)
    for place in content.places.values():
        if place.kind == "station":
            add(place)
    for doc in docs:
        for place_id in doc.places:
            add(content.place(place_id))
    for place in [p for p in in_play if p is not None]:
        if _located(place):
            for _, near in recommend_service.nearby(content.places, place.id):
                add(near)
    return list(chosen.values())

MARKDOWN = re.compile(r"\*\*|__|^#+\s*|`", re.MULTILINE)
LIST_MARKER = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+", re.MULTILINE)


@dataclass(frozen=True)
class ToolRun:
    name: ToolName | None  # None when the request could not be served
    result: dict
    certainty: Certainty | None = None
    place_id: str | None = None
    source: str | None = None
    evidence: str = ""  # the whole day's trains, beyond the digest the model saw
    train_count: int | None = None

    @property
    def summary(self) -> str:
        return _step_summary(self)


def _llm_draft(
    request: ChatRequest, context: ResolvedContext, settings: Settings,
    deadline: llm.Deadline | None = None, stages: _Stages | None = None,
) -> Draft:
    content = context.content
    stages = stages or _Stages()
    # Step 3 (vectors) is optional at every turn: switched off, no vectors stored, vectors
    # from another model, or the question embedding failing or taking too long. The answer
    # is then built from steps 1 and 2 alone; it never becomes an error.
    embed = None
    if settings.chat_vector_search and content.vector_ready(settings.embedding_model):
        def embed(text):
            return llm.embed_query(settings, text, deadline)
    with stages.timed("retrieval"):
        try:
            hits = content.search(request.message, context.known_ids, embed=embed,
                                  min_similarity=settings.vector_min_similarity)
        except llm.LLMError as exc:
            logger.warning("vector search skipped for this question: %s", exc)
            hits = content.search(request.message, context.known_ids)
    docs = _select_docs(hits, context)
    vector_docs = tuple(h.doc.id for h in hits if h.reason == "vector")
    # A passing station in play: give the model the rule it must follow.
    subject = context.mentioned or context.focused
    if (subject is not None and subject.kind == "station" and subject.passenger_stop is False
            and PASSING_DOC in content.docs and all(d.id != PASSING_DOC for d in docs)):
        docs = [*docs[: prompts.MAX_DOCS - 1], content.docs[PASSING_DOC]]
    # Documents the question is plainly about are always in front of the model.
    for doc_id in _asked_docs(context):
        if all(d.id != doc_id for d in docs):
            docs = [*docs[: prompts.MAX_DOCS - 1], content.docs[doc_id]]

    raw = context.raw
    agent = settings.chat_agent
    static = prompts.build_agent_static() if agent else prompts.build_static()
    schema = prompts.AGENT_SCHEMA if agent else prompts.DRAFT_SCHEMA
    screen = prompts.build_context(
        places=_relevant_places(context, docs),
        message=request.message,
        today=dt.datetime.now(KST).date(),
        date=raw.date,
        pins=[p.id for p in context.pins],
        focused=context.focused.id if context.focused else None,
        depth=raw.depth,
        dismissed=raw.dismissed,
        content=content,
        docs=docs,
        first_turn=not request.history,
        market_day=market_day_sentence(raw.date) if raw.date else None,
    )
    history = [(t.role, t.content) for t in request.history]

    def call(label: str, tool_result: dict | None = None,
             tool_results: list[dict] | None = None, steps_left: int = 0) -> dict:
        messages = prompts.build_messages(
            static=static, history=history, context=screen, message=request.message,
            tool_result=tool_result, tool_results=tool_results, steps_left=steps_left,
        )
        with stages.timed(f"llm_{label}"):
            return llm.chat_json(settings, messages, schema, label, deadline)

    first = call("first")
    # Walking time: asked in words the server knows, or by the model's own tool request.
    # A plan request is handed over as it is: neither a walk nor any other tool is run.
    wants_plan = _wants_plan(str(first.get("intent")), request.message)
    risky = any(word in request.message for word in RISK_WORDS)
    pair = None if risky or wants_plan else _walk_pair(context)
    asked = first.get("tool_request") or {}
    by_ride = any(word in request.message for word in RIDE_WORDS)
    if (pair is None and not risky and not by_ride and not wants_plan and not agent
            and asked.get("name") == "walk_time"):
        ends = [content.place(asked.get(key) or "") for key in ("station_id", "place_id")]
        if None not in ends and ends[0] != ends[1]:
            pair = (ends[0], ends[1])
    if pair is not None:
        draft = _walk_draft(*pair)
        draft.steps = [ChatStep(tool="walk_time", summary=_walk_run(*pair).summary)]
        return draft
    runs: list[ToolRun] = []
    out_of_time = False
    if not first.get("tool_request") or wants_plan:
        final = first
    elif not agent:
        with stages.timed("tool"):
            runs.append(_run_tool(first["tool_request"], context))
        final = call("after_tool", tool_result=runs[0].result)  # a second request is ignored
    else:
        final, out_of_time = _agent_steps(first, call, context, settings, deadline, stages, runs)
    draft = _parse_draft(final)
    provided = {d.id for d in docs}
    draft.sources = [s for s in draft.sources if s in provided]
    draft.reply = _tidy(draft.reply, first_turn=not request.history)
    _apply_runs(draft, runs)
    if out_of_time:
        # The budget ran out before the model could answer: what was looked up is said in
        # the server's words, and nothing the model wrote before the lookups is kept.
        draft.reply = " ".join([*(step.summary for step in draft.steps), AGENT_OUT_OF_TIME])
        draft.proposals = []
    if (len(runs) == 1 and runs[0].name == "nearby_spots"
            and runs[0].result.get("nearest_beyond")):
        # Nothing near: the sentence and its numbers are the server's, and a place
        # half an hour away is information, not a proposal.
        result = runs[0].result
        draft.reply = _none_nearby(result["place"], result["within_minutes"],
                                   result["nearest_beyond"])
        draft.proposals = []
    # Documents the model was given are evidence too, cited or not ("하루 6편" from a doc).
    draft.evidence = "\n".join([draft.evidence, *(d.body for d in docs)])
    draft.vector_docs = vector_docs
    draft.named_docs = tuple(h.doc.id for h in hits if h.reason != "vector")
    return draft


def _agent_steps(first: dict, call, context: ResolvedContext, settings: Settings,
                 deadline: llm.Deadline | None, stages: _Stages,
                 runs: list[ToolRun]) -> tuple[dict, bool]:
    """Tool steps until the model answers, the step limit is reached or the request budget
    is nearly spent. Returns the model's final output and whether time ran out before it
    could answer (the caller then writes the reply from the steps). `runs` is filled as
    the steps happen, so it holds what was checked even when a call fails."""
    current, found, seen = first, set(), set()
    limit = settings.chat_agent_max_steps
    for step in range(1, limit + 1):
        asked = current.get("tool_request")
        if not asked:
            break
        key = json.dumps(asked, sort_keys=True, ensure_ascii=False)
        repeated = key in seen  # the same lookup again: nothing new to learn, answer now
        seen.add(key)
        if not repeated:
            with stages.timed(f"tool_{step}"):
                run = _run_tool(asked, context, frozenset(found), agent=True)
            runs.append(run)
            found |= _found_ids(run)
        short = deadline is not None and deadline.remaining() < AGENT_STEP_RESERVE_SECONDS
        left = 0 if repeated or short or step == limit else limit - step
        try:
            current = call(f"step_{step}", tool_results=[r.result for r in runs],
                           steps_left=left)
        except llm.LLMError as exc:
            if not any(r.name for r in runs):
                raise  # nothing was learned: the built-in responder answers
            logger.warning("agent stopped after %d step(s): %s", len(runs), exc)
            return {**first, "proposals": [], "tool_request": None}, True
        if left == 0:
            break
    return current, False


def _select_docs(hits: list[retrieval.Hit], context: ResolvedContext) -> list[retrieval.Doc]:
    """Fit the prompt budget without losing what was asked about: the open marker's own
    document and explicitly named documents come before documents that only relate to pins."""
    focused_id = context.focused.id if context.focused else None
    ranked = sorted(
        hits,
        key=lambda h: 0 if h.doc.id == focused_id else 1 if h.reason == "keyword" else 2,
    )  # stable: search order is kept within each rank
    return [h.doc for h in ranked][: prompts.MAX_DOCS]


def _tidy(reply: str, first_turn: bool) -> str:
    """No markdown in a chat bubble; the persona greeting only opens a conversation."""
    reply = MARKDOWN.sub("", reply)
    reply = LIST_MARKER.sub("", reply)  # chat bubbles are prose, not lists
    reply = re.sub(r"\s*\n+\s*", " ", reply).strip()
    if not first_turn and reply.startswith(prompts.GREETING):
        reply = reply[len(prompts.GREETING):].strip() or reply
    return reply


def _parse_draft(raw: dict) -> Draft:
    try:
        return Draft(
            reply=str(raw["reply"]),
            intent=raw["intent"],
            chips=[str(c) for c in raw["chips"]],
            proposals=[
                DraftProposal(p["type"], p["target"], p["reason"], bool(p["time_bound"]))
                for p in raw["proposals"]
                if p.get("type") in ("add", "remove")
            ],
            sources=[str(s) for s in raw["sources"]],
            plan=raw.get("plan") if isinstance(raw.get("plan"), dict) else None,
        )
    except (KeyError, TypeError) as exc:
        raise llm.LLMError(f"answer did not match the schema: {exc}") from exc


def _weakest(certainties) -> Certainty | None:
    present = [c for c in certainties if c]
    return max(present, key=_CERTAINTY_ORDER.index) if present else None


def _nearby_candidates(
    place_id: str, context: ResolvedContext, gap: tools.Gap | None = None
) -> list[dict]:
    """Places worth proposing: not already pinned, not dismissed. They come from the
    shared rule (modules/recommend): for a gap the places that fit in it, otherwise the
    places within a short walk. Walking minutes are computed from coordinates by the one
    function the walk-time answer uses too. Only an anchor without coordinates falls back
    to the example table, and that answer is labelled as example data."""
    skip = {p.id for p in context.pins} | set(context.raw.dismissed)
    places = context.content.places
    anchor = context.content.place(place_id)
    who = companions.rule(context.raw.companions)
    if _located(anchor) and gap is not None:
        return [
            {"id": s.target, "name": places[s.target].name, "walk_minutes": s.walk_min,
             "reason": s.reason, "notes": s.notes, "purpose": s.purpose}
            for s in recommend_service.suggest(places, place_id, gap.start, gap.end,
                                               skip=skip, date=context.raw.date,
                                               max_walk=who.maxWalkingMinutes,
                                               indoor_first=who.indoorFirst)
        ]
    if _located(anchor):
        return [{"id": place.id, "name": place.name, "walk_minutes": minutes}
                for minutes, place in recommend_service.nearby(
                    places, place_id, skip=skip, within=_walk_limit(context),
                    indoor_first=who.indoorFirst)]
    return [
        {"id": s.place_id, "name": p.name, "walk_minutes": s.walk_minutes}
        for s in tools.nearby_spots(place_id)
        if s.place_id not in skip and (p := context.content.place(s.place_id)) is not None
    ]


def _none_nearby(name: str, within: int, beyond: dict) -> str:
    """Nothing within a short walk: said the same way by every responder, numbers from
    the one walking function."""
    return (f"{name}에서 걸어서 {within}분 안에는 둘러볼 만한 곳이 없어요. 가장 가까운 곳은 "
            f"{beyond['name']}(걸어서 약 {beyond['walk_minutes']}분)이에요. {tools.WALK_BASIS}")


def _walk_limit(context: ResolvedContext) -> int:
    """How far "nearby" reaches, one way: the default, or less for some companions."""
    limit = companions.rule(context.raw.companions).maxWalkingMinutes
    return min(limit or recommend_service.NEARBY_WALK_MIN, recommend_service.NEARBY_WALK_MIN)


def _nearest_beyond(place_id: str, context: ResolvedContext) -> dict | None:
    """The nearest place worth a visit when none is within a short walk, so "nothing
    nearby" still says how far the first one is. Same walking function as everything else."""
    places = context.content.places
    skip = {p.id for p in context.pins} | set(context.raw.dismissed)
    found = recommend_service.within_walk(
        places, places[place_id], recommend_service.VISIT_CATEGORIES, skip)
    return {"id": found[0][1].id, "name": found[0][1].name,
            "walk_minutes": found[0][0]} if found else None


def _normalize_after(value: object) -> str | None:
    """The model's `after` as HH:MM. "오후 4시" is understood; anything else is ignored
    (the whole list is better than an error)."""
    claims = _clock_claims(value) if isinstance(value, str) else []
    if not claims:
        return None
    hours, minute = claims[0]
    hour = min(hours)  # a bare "4시" keeps the wider list
    if hour > 23 or (minute or 0) > 59:
        return None
    return f"{hour:02d}:{minute or 0:02d}"


NO_TRAIN_DATA = ("이 역은 여객열차 운행 기록이 없어요. 화물열차 시각은 공식 데이터가 없어 "
                 "안내할 수 없다고 말한다")


MAX_TRAINS_SHOWN = 10  # a busy station has 60+ trains: the model gets a digest, not the day


def _all_trains(trains) -> str:
    """Every train as text: what stated times are verified against."""
    return " | ".join(t.label() for t in trains)


def _rhythm_result(rhythm: tools.StationRhythm) -> dict:
    """A digest for the model (the full list made it recite 60 trains and run out of
    tokens). "N시대" keys cannot be misread as clock times."""
    busiest = max(rhythm.hourly, key=lambda h: (rhythm.hourly[h], -h))
    in_busiest = [t.label() for t in rhythm.trains if int(t.time[:2]) == busiest]
    return {
        "station_id": rhythm.station_id,
        "total_trains": len(rhythm.trains),
        "first_train": rhythm.trains[0].label(),
        "last_train": rhythm.trains[-1].label(),
        "hourly_counts": {f"{hour}시대": f"{count}편" for hour, count in sorted(rhythm.hourly.items())},
        "busiest": f"{busiest}시대 {rhythm.hourly[busiest]}편",
        "busiest_trains": in_busiest[:MAX_TRAINS_SHOWN],
        "gaps": [f"{g.start}~{g.end}" for g in rhythm.gaps],
        "source": rhythm.source,
        "basis": rhythm.basis,
    }


def _run_tool(request: dict, context: ResolvedContext, found: frozenset[str] = frozenset(),
              agent: bool = False) -> ToolRun:
    """Run one requested tool. Certainty comes from the result, never from the model.
    `found`: place ids earlier tool steps returned (agent mode), which later steps may use."""
    name = request.get("name")
    content = context.content
    # Tools may only look at places the user put in play (screen or message): the
    # model must not pick a station on its own, e.g. guess Donghae when none is open.
    in_play = {p.id for p in ([context.focused] if context.focused else []) + list(context.pins)}
    for place in (context.mentioned, context.recalled):
        if place:
            in_play.add(place.id)
    if agent:  # a multi-stop question names several places; earlier steps found others
        in_play |= {p.id for p in content.places_in_text(context.message)} | found
        if name == "saved_items":
            return _saved_items(context)
        if name == "search_places":
            return _search_places(request, context, in_play)
        if name == "walk_time":
            ends = [content.place(request.get(key) or "") for key in ("station_id", "place_id")]
            if None in ends or ends[0] == ends[1] or not {e.id for e in ends} <= in_play:
                return ToolRun(None, {"tool": name, "error": "두 장소를 알 수 없어요"})
            return _walk_run(*ends)
    named = context.mentioned
    recalled = context.recalled if context.recalled and context.recalled.kind == "station" else None
    default_station = (
        named if named and named.kind == "station" else context.station or recalled
    )
    station_id = request.get("station_id") or (default_station.id if default_station else None)
    if station_id not in in_play:
        station_id = None
    station = content.place(station_id) if station_id else None
    date = context.raw.date

    if name in ("station_rhythm", "search_trains"):
        if station is None or station.kind != "station":
            return ToolRun(None, {"tool": name, "error": "기준 역이 없어요. 지도에서 역을 열어 달라고 안내한다"})
        if name == "station_rhythm":
            rhythm = tools.station_rhythm(station.id, date, context.timetable)
            if rhythm is None:
                return ToolRun(None, {"tool": name, "error": NO_TRAIN_DATA}, place_id=station.id)
            certainty = _weakest(g.certainty for g in rhythm.gaps) or _weakest(
                t.certainty for t in rhythm.trains
            )
            result = _rhythm_result(rhythm)
            # Candidates for filling a gap, so proposals can name a real place.
            longest = max(rhythm.gaps, key=tools.gap_minutes, default=None)
            result["nearby"] = _nearby_candidates(station.id, context, longest)
        elif (agent and context.timetable is not None
              and (to := content.place(request.get("place_id") or "")) is not None
              and to.kind == "station" and to.id != station.id and to.id in in_play):
            return _trips_run(station, to, _normalize_after(request.get("after")), context)
        else:
            after = _normalize_after(request.get("after"))
            whole = tools.station_rhythm(station.id, date, context.timetable)
            if whole is None:
                return ToolRun(None, {"tool": name, "error": NO_TRAIN_DATA}, place_id=station.id)
            trains = tools.search_trains(date, station=station.id, after=after,
                                         timetable=context.timetable)
            certainty = _weakest(t.certainty for t in trains)
            result = {"station_id": station.id, "after": after,
                      "total_trains": len(trains),
                      "trains": [t.label() for t in trains[:MAX_TRAINS_SHOWN]],
                      "more": max(len(trains) - MAX_TRAINS_SHOWN, 0),
                      "source": whole.source, "basis": whole.basis}
            rhythm = whole
        result.update(tool=name, station_name=station.name,
                      passenger_stop=station.passenger_stop)
        return ToolRun(name, result, certainty, station.id, result["source"],
                       evidence=_all_trains(rhythm.trains), train_count=len(rhythm.trains))

    if name == "nearby_spots":
        default_place = context.mentioned or context.anchor or context.recalled
        place_id = request.get("place_id") or (default_place.id if default_place else None)
        anchor = content.place(place_id) if place_id in in_play else None
        if anchor is None:
            return ToolRun(None, {"tool": name, "error": "장소를 알 수 없어요"})
        spots = _nearby_candidates(anchor.id, context)
        source = tools.COORDINATES if _located(anchor) else tools.SOURCE
        result = {"tool": name, "place": anchor.name, "spots": spots, "source": source}
        if source == tools.COORDINATES:
            result["walk_basis"] = tools.WALK_BASIS
            if not spots and (beyond := _nearest_beyond(anchor.id, context)):
                result["within_minutes"] = _walk_limit(context)
                result["nearest_beyond"] = beyond  # none within a short walk: the first one
        return ToolRun(name, result, None, anchor.id, source)

    return ToolRun(None, {"tool": name, "error": "알 수 없는 도구"})


# --- agent tools: read-only, and only about places already in play ------------------

MAX_TRIPS_SHOWN = 8


def _trips_run(origin: retrieval.Place, to: retrieval.Place, after: str | None,
               context: ResolvedContext) -> ToolRun:
    """Trains one can ride from one station to another (both real stops)."""
    found = timetable_service.search(context.timetable, origin.id, to.id, after)
    lines = [f"{t.dep} {origin.name} 출발 → {t.arr} {to.name} 도착, "
             f"{t.type or t.line} {t.no}, {t.minutes}분" for t in found.trains]
    basis = context.timetable.basis().summary
    result = {"tool": "search_trains", "from": origin.name, "to": to.name, "after": after,
              "total_trains": len(lines), "trains": lines[:MAX_TRIPS_SHOWN],
              "more": max(len(lines) - MAX_TRIPS_SHOWN, 0),
              "source": tools.SNAPSHOT, "basis": f"{basis}의 어림 시각이에요."}
    if not lines:
        result["note"] = "두 역을 잇는 열차가 없어요(한쪽이 통과역이거나 그 시각 이후 열차가 없어요)."
    return ToolRun("search_trains", result, "estimated" if lines else None, origin.id,
                   tools.SNAPSHOT, evidence="\n".join(lines), train_count=len(lines))


def _walk_run(a: retrieval.Place, b: retrieval.Place) -> ToolRun:
    """Walking time as one step of several; the single-question answer is _walk_draft."""
    walk = recommend_service.walk_between(a, b)
    result = {"tool": "walk_time", "from": a.name, "to": b.name, "to_id": b.id,
              "walk_minutes": walk.minutes, "source": tools.COORDINATES}
    if walk.minutes is not None:
        result["walk_basis"] = tools.WALK_BASIS
    else:
        result["note"] = ("위치 정보가 없어 계산할 수 없어요" if walk.reason == "no_coordinates"
                          else "걸어가기에는 멀어요. 열차나 버스·택시를 권해요")
    return ToolRun("walk_time", result, None, b.id, tools.COORDINATES)


def _search_places(request: dict, context: ResolvedContext, in_play: set[str]) -> ToolRun:
    """Places by name, by category, or by what one can walk to from a place in play.
    Categories come from the tourism data; a place without one is found by name only."""
    content = context.content
    query = str(request.get("query") or "").strip()
    category = request.get("category")
    if category not in PLACE_SEARCH_CATEGORIES:
        category = None
    near = content.place(request.get("place_id") or "")
    if near is not None and near.id not in in_play:
        near = None
    if not query and category is None:
        return ToolRun(None, {"tool": "search_places", "error": "찾을 이름이나 분류가 없어요"})

    def matches(place: retrieval.Place) -> bool:
        named = not query or any(query in label for label in (place.name, *place.aliases))
        return named and (category is None or place.category == category)

    if near is not None and _located(near):
        within = _walk_limit(context)
        rows = [{"id": p.id, "name": p.name, "category": p.category, "walk_minutes": minutes}
                for minutes, p in recommend_service.within_walk(
                    content.places, near, tuple(PLACE_SEARCH_CATEGORIES) if category is None
                    else (category,))
                if matches(p) and minutes <= within]
    else:
        rows = [{"id": p.id, "name": p.name, "category": p.category}
                for p in sorted(content.places.values(), key=lambda p: p.name)
                if matches(p) and (near is None or p.id != near.id)]
    result = {"tool": "search_places", "query": query or None, "category": category,
              "near": near.name if near else None, "total": len(rows),
              "places": rows[:MAX_PLACES_FOUND], "source": tools.COORDINATES}
    if near is not None and _located(near):
        result.update(within_minutes=_walk_limit(context), walk_basis=tools.WALK_BASIS)
    elif near is not None:
        result["note"] = f"{near.name}의 위치 정보가 없어 가까운 순서로 고르지 못했어요."
    return ToolRun("search_places", result, None, near.id if near else None, tools.COORDINATES)


def _saved_items(context: ResolvedContext) -> ToolRun:
    """What the user has saved. Reading only: nothing is saved or removed here."""
    content, saved = context.content, context.saved
    places = [{"id": p.id, "name": p.name, "must": p.id in saved.mustPlaceIds}
              for p in map(content.place, saved.placeIds) if p is not None]
    trains = [{"train_no": no, "for": "탑승" if action == "ride" else "관찰",
               "station": (content.place(station).name if content.place(station) else station)}
              for no, action, station in saved.trains]
    return ToolRun("saved_items", {"tool": "saved_items", "places": places, "trains": trains})


def _step_summary(run: ToolRun) -> str:
    """One line for the user about what a step found, written from the result."""
    r = run.result
    tool = run.name or r.get("tool")
    if run.name is None:
        return {"station_rhythm": "열차 리듬", "search_trains": "열차", "nearby_spots": "근처 장소",
                "walk_time": "도보 시간", "search_places": "장소",
                "saved_items": "담아 둔 것"}.get(tool, "요청한 것") + "을 확인하지 못했어요."
    if tool == "station_rhythm":
        return (f"{r['station_name']} 열차 리듬을 확인했어요"
                f"(하루 {run.train_count}편, 공백 {len(r.get('gaps', []))}번).")
    if tool == "search_trains" and "to" in r:
        return (f"{r['from']}에서 {r['to']}까지 가는 열차 {r['total_trains']}편을 확인했어요."
                if r["total_trains"] else f"{r['from']}에서 {r['to']}까지 가는 열차는 찾지 못했어요.")
    if tool == "search_trains":
        when = f" {r['after']} 이후" if r.get("after") else ""
        return f"{r['station_name']}{when} 열차 {r['total_trains']}편을 확인했어요."
    if tool == "nearby_spots":
        if r.get("spots"):
            return f"{r['place']} 근처 장소 {len(r['spots'])}곳을 찾았어요."
        return f"{r['place']} 근처에는 걸어갈 만한 곳을 찾지 못했어요."
    if tool == "walk_time":
        if r["walk_minutes"] is None:
            return f"{r['from']}에서 {r['to']}까지 도보 시간은 계산하지 못했어요."
        return f"{r['from']}에서 {r['to']}까지 도보 시간을 계산했어요(약 {r['walk_minutes']}분)."
    if tool == "search_places":
        what = PLACE_SEARCH_CATEGORIES.get(r.get("category"), "장소")
        where = f"{r['near']} 근처 " if r.get("near") else ""
        return (f"{where}{what} {r['total']}곳을 찾았어요." if r["total"]
                else f"{where}{what}은 찾지 못했어요.")
    if tool == "saved_items":
        return f"담아 둔 장소 {len(r['places'])}곳과 열차 {len(r['trains'])}편을 확인했어요."
    return "확인했어요."


def _found_ids(run: ToolRun) -> set[str]:
    """Place ids a step returned, which the next steps may look at."""
    r = run.result
    rows = [*r.get("spots", []), *r.get("nearby", []), *r.get("places", [])]
    ids = {row["id"] for row in rows if isinstance(row, dict) and "id" in row}
    if r.get("nearest_beyond"):
        ids.add(r["nearest_beyond"]["id"])
    if r.get("to_id"):
        ids.add(r["to_id"])
    return ids


_SOURCE_ORDER = (tools.MOCK, tools.SNAPSHOT, tools.COORDINATES)  # which one the answer shows


def _apply_runs(draft: Draft, runs: list[ToolRun]) -> None:
    """What the tools returned, onto the draft the server rules read. With several steps:
    the weakest certainty, every result as evidence, and the train lookup (if any) as the
    tool the answer is "from", since the rules about times hang on it."""
    done = [run for run in runs if run.name is not None]
    draft.steps = [ChatStep(tool=tool, summary=run.summary) for run in runs
                   if (tool := run.name or run.result.get("tool")) in TOOL_NAMES]
    if not done:
        return
    main = next((run for run in done if run.name in ("station_rhythm", "search_trains")),
                done[-1])
    draft.tool_used = main.name
    draft.tool_place = main.place_id
    draft.tool_certainty = _weakest(run.certainty for run in done)
    sources = {run.source for run in done}
    draft.tool_source = next((s for s in _SOURCE_ORDER if s in sources), main.source)
    bases = dict.fromkeys(run.result["basis"] for run in done if run.result.get("basis"))
    draft.tool_basis = " ".join(bases) or None
    draft.evidence = "\n".join(
        part for run in done
        for part in (json.dumps(run.result, ensure_ascii=False, default=str), run.evidence))
    counts = [run.train_count for run in done if run.train_count is not None]
    draft.train_count = max(counts) if counts else None


# --- mock responder: deterministic keyword routing, no keys or network -----------

_SAFETY = RISK_WORDS
_TRAIN = ("열차", "기차", "시각", "시간표", "몇 시", "몇시", "몇 편", "편수", "운행", "언제", "몰려", "리듬")
_KEEP_EMPTY = KEEP_EMPTY_WORDS
_PROPOSAL = ("추천", "근처", "볼 곳", "볼 수 있는", "갈 곳", "뭐 하", "뭘 하", "일정")
_SMALLTALK = ("안녕", "고마", "감사")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _mock_draft(message: str, context: ResolvedContext) -> Draft:
    if _has(message, _SAFETY):
        return _mock_safety(message, context)
    if is_plan_request(message):
        return Draft(reply="", intent="plan_request")  # _plan_handoff writes the answer
    if (pair := _walk_pair(context)) is not None:
        return _walk_draft(*pair)
    if _has(message, _TRAIN):
        return _mock_train(message, context)
    if _has(message, _KEEP_EMPTY):
        return Draft(
            reply="좋아요, 빈 시간은 비워 둘게요. 필요하면 언제든 근처 선택지를 다시 볼 수 있어요.",
            intent="smalltalk",
            chips=["근처에 같이 볼 곳", "열차 리듬 다시 보기"],
        )
    if _has(message, _PROPOSAL):
        return _mock_nearby(message, context)
    if is_agreement(message):
        return Draft(reply=PROPOSAL_AGREED if "담" in context.last_reply else ACK_REPLY,
                     intent="smalltalk", chips=[])
    if _has(message, _SMALLTALK):
        hint = "지도에서 역이나 장소를 열어 두고 물어보면 그 기준으로 답할게요."
        return Draft(
            reply=f"{prompts.GREETING} {hint}" if context.first_turn else f"네, 좋아요. {hint}",
            intent="smalltalk",
            chips=["동해역 열차 리듬", "근처에 같이 볼 곳"],
        )
    return _mock_knowledge(message, context)


def _mock_knowledge(message: str, context: ResolvedContext) -> Draft:
    hits = context.content.search(message, context.known_ids)
    # An explicit mention ("평능역 알려줘") beats the open marker; "여기/이거" falls back to it.
    hit = next((h for h in hits if h.reason == "keyword"), hits[0] if hits else None)
    if hit is None:
        return Draft(
            reply=NO_INFO,
            intent="knowledge",
            chips=["동해역 열차 리듬", "근처에 같이 볼 곳"],
        )
    place = context.content.place(hit.doc.id)
    if place is not None and place.kind == "station":
        chips = [f"{place.name} 열차 리듬", f"{place.name} 근처"]
    elif place is not None:
        chips = [f"{place.name} 근처", "동해역 열차 리듬"]
    else:
        chips = ["동해역 열차 리듬", "근처에 같이 볼 곳"]
    reply = hit.doc.summary
    if hit.reason == "keyword":  # quote the paragraph that answers the named thing
        terms = [k for k in hit.doc.keywords if k in message and k != hit.doc.title]
        for paragraph in hit.doc.body.split("\n\n"):
            if any(t in paragraph for t in terms):
                reply = paragraph.strip()
                break
    return Draft(reply=reply, intent="knowledge", chips=chips, sources=[hit.doc.id])


def _mock_safety(message: str, context: ResolvedContext) -> Draft:
    """The limit and the alternative are written by `_safe_reply`; this adds what is known."""
    content = context.content
    anchor = context.anchor
    spots = [] if anchor is None or _located(anchor) else tools.nearby_spots(anchor.id)
    stations = [
        p.name for p in (content.place(s.place_id) for s in spots) if p and p.kind == "station"
    ]
    parts = []
    if stations:
        parts.append(f"걸어갈 만한 역으로 {', '.join(stations)}이 있어요.")
    sources = ["safety_track_crossing"]
    for hit in content.search(message):
        if hit.doc.kind == "safety" and hit.doc.id not in sources:
            parts.append(f"참고로, {hit.doc.summary}")
            sources.append(hit.doc.id)
            break
    if not asks_entry(message) and len(sources) == 1:
        parts.append(NO_CROSSING_LIST)  # no list of crossings: the user marks the spot
    station = next((p for p in (context.mentioned, context.station, context.recalled)
                    if p is not None and p.kind == "station"), None)
    return Draft(
        reply=" ".join(parts),
        intent="safety",
        chips=[f"{station.name} 열차 리듬" if station else "동해역 열차 리듬",
               "하평 건널목 사례", "근처에 같이 볼 곳"],
        sources=sources,
        tool_used="nearby_spots" if stations else None,
    )


def _mock_train(message: str, context: ResolvedContext) -> Draft:
    named = context.content.place_in_text(message)
    recalled = context.recalled if context.recalled and context.recalled.kind == "station" else None
    station = named if named and named.kind == "station" else context.station or recalled
    if station is None:
        return Draft(
            reply="어느 역 기준으로 볼까요? 지도에서 역을 열어 두거나 핀을 찍어 주세요.",
            intent="train_lookup",
            chips=["동해역 열차 리듬", "묵호역 열차 리듬", "추암역 열차 리듬"],
        )
    rhythm = tools.station_rhythm(station.id, context.raw.date, context.timetable)
    if rhythm is None or not rhythm.trains:
        return Draft(
            reply=f"{station.name}은 여객열차 운행 기록이 없어요. "
                  "화물열차 시각은 공식 데이터가 없어서 안내하지 못해요.",
            intent="train_lookup",
            chips=["동해역 열차 리듬", f"{station.name} 근처"],
        )
    evidence = _all_trains(rhythm.trains)
    note = f"{MOCK_NOTE} " if rhythm.source == tools.MOCK else ""
    if "목록" in message:
        trains = tools.search_trains(context.raw.date, station=station.id,
                                     timetable=context.timetable)
        listed = ", ".join(t.label() for t in trains[:6])
        more = f" 등 {len(trains)}편" if len(trains) > 6 else ""
        return Draft(
            reply=f"{note}{station.name} 열차: {listed}{more}.",
            intent="train_lookup",
            chips=[f"{station.name} 열차 리듬", f"{station.name} 근처"],
            evidence=evidence,
            train_count=len(rhythm.trains),
            tool_used="search_trains",
            tool_source=rhythm.source,
            tool_basis=rhythm.basis,
            tool_place=station.id,
        )
    busiest_hour = max(rhythm.hourly, key=lambda h: (rhythm.hourly[h], -h))
    draft = Draft(
        reply=(
            f"{note}{station.name}에는 하루 {len(rhythm.trains)}편이 "
            f"{'지나가요' if station.passenger_stop is False else '서요'}. "
            f"{busiest_hour}시대에 가장 많아요({rhythm.hourly[busiest_hour]}편)."
        ),
        intent="train_lookup",
        chips=[f"{station.name} 열차 목록", f"{station.name} 근처", "빈 시간은 그대로 둘래요"],
        evidence=evidence,
        train_count=len(rhythm.trains),
        tool_used="station_rhythm",
        tool_source=rhythm.source,
        tool_basis=rhythm.basis,
        tool_place=station.id,
    )
    if rhythm.gaps:
        # The longest gap is where the day opens up for sightseeing.
        gap = max(rhythm.gaps, key=tools.gap_minutes)
        draft.reply += f" {gap.start}~{gap.end}에는 열차가 없어요."
        draft.tool_certainty = gap.certainty
        for spot in _nearby_candidates(station.id, context, gap):
            place = context.content.place(spot["id"])
            if place is not None and place.kind == "poi" and spot.get("purpose") != "meal":
                draft.proposals.append(
                    DraftProposal(
                        type="add",
                        target=place.id,
                        reason=spot.get("reason")
                        or f"{station.name} 공백({gap.start}–{gap.end})에 다녀올 수 있어요"
                        f"(도보 {spot['walk_minutes']}분)",
                        time_bound=True,
                    )
                )
                break
    return draft


def _mock_nearby(message: str, context: ResolvedContext) -> Draft:
    content = context.content
    anchor = content.place_in_text(message) or context.anchor or context.recalled
    if anchor is None:
        return Draft(
            reply="어디 근처를 볼까요? 지도에서 역이나 장소를 열어 두거나 핀을 찍어 주세요.",
            intent="proposal",
            chips=["동해역 근처", "묵호역 근처", "추암역 근처"],
        )
    measured = _located(anchor)
    spots = [
        (place, spot["walk_minutes"])
        for spot in (_nearby_candidates(anchor.id, context) if measured else
                     [{"id": s.place_id, "walk_minutes": s.walk_minutes}
                      for s in tools.nearby_spots(anchor.id)])
        if (place := content.place(spot["id"])) is not None
    ]
    rhythm_chip = f"{anchor.name} 열차 리듬" if anchor.kind == "station" else "동해역 열차 리듬"
    if not spots:
        reply = f"{anchor.name} 근처에서 걸어갈 만한 곳은 아직 확인된 정보가 없어요."
        if measured and (beyond := _nearest_beyond(anchor.id, context)):
            reply = _none_nearby(anchor.name, _walk_limit(context), beyond)
        return Draft(
            reply=reply,
            intent="proposal",
            chips=[rhythm_chip, "묵호역 근처"],
            tool_used="nearby_spots" if measured else None,  # looked, and found nothing near
            tool_place=anchor.id if measured else None,
        )
    if measured:
        listed = ", ".join(f"{place.name}(걸어서 약 {minutes}분)" for place, minutes in spots)
        reply = (f"{anchor.name} 근처에 {listed}이 있어요. {tools.WALK_BASIS} "
                 "담을지는 직접 골라 주세요.")
    else:
        listed = ", ".join(f"{place.name}(도보 {minutes}분)" for place, minutes in spots)
        reply = f"{MOCK_NOTE} {anchor.name} 근처에 {listed}이 있어요. 담을지는 직접 골라 주세요."
    return Draft(
        reply=reply,
        intent="proposal",
        chips=[rhythm_chip, "빈 시간은 그대로 둘래요"],
        proposals=[
            DraftProposal(
                type="add",
                target=place.id,
                reason=(f"{anchor.name}에서 걸어서 약 {minutes}분이에요" if measured
                        else f"{anchor.name}에서 도보 {minutes}분이에요"),
            )
            for place, minutes in spots
        ],
        tool_used="nearby_spots",
        tool_source=tools.COORDINATES if measured else tools.SOURCE,
        tool_place=anchor.id,
    )
