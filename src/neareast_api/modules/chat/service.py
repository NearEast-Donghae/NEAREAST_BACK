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
from contextlib import contextmanager
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from neareast_api.core.config import Settings
from neareast_api.modules.chat import llm, prompts, retrieval, tools
from neareast_api.modules.chat.schemas import (
    Certainty,
    ChatContext,
    ChatRequest,
    ChatResponse,
    Intent,
    PersonaLoading,
    PersonaResponse,
    Proposal,
    ToolName,
)
from neareast_api.modules.recommend import service as recommend_service
from neareast_api.modules.trains import service as timetable_service

logger = logging.getLogger(__name__)

MAX_CHIPS = 3
SLOW_REQUEST_MS = 5000  # the frontend switches to the "slow" text at 5 s
MAX_REPLY_CHARS = 400  # the prompt asks for 2-4 sentences; this is the hard stop
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
NO_INFO = "아직 확인된 정보가 없어요. 지도에서 궁금한 역이나 장소를 열어 두고 다시 물어봐 주세요."
MOCK_NOTE = "(예시 데이터)"
BASIS_MARK = "토요일 3주"  # present in the timetable's basis summary
PASSING_MARK = "앞뒤 정차역"  # present in the passing-time basis
PASSING_NOTE = "이 역은 여객열차가 서지 않아 타고 내릴 수 없고, 열차는 관찰만 할 수 있어요."
PASSING_DOC = "safety_passing_station"
DEFAULT_CHIPS = ["동해역 열차 리듬", "동해역 근처"]
SAFETY_NO = "선로·건널목 안이나 차단기 안쪽에서의 촬영·진입은 권하지 않아요."
SAFETY_ALTERNATIVE = "대신 역 맞이방이나 승강장처럼 공개된 장소에서 열차를 보는 방법을 권해요."
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

    @property
    def known_ids(self) -> list[str]:
        """Known context ids, the open marker first."""
        return [p.id for p in ([self.focused] if self.focused else []) + list(self.pins)]

    @property
    def anchor(self) -> retrieval.Place | None:
        """What "here/this" refers to: the open marker, else the first pin."""
        return self.focused or (self.pins[0] if self.pins else None)

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


def answer(request: ChatRequest, settings: Settings) -> ChatResponse:
    try:
        content = retrieval.load_content(settings.content_dir)
    except retrieval.ContentError:
        logger.exception("chat content unavailable")
        raise ChatUnavailableError from None
    context = dataclasses.replace(
        _resolve(request.context, content, request.message, request.history),
        timetable=timetable_service.load_or_none(settings.content_dir),
    )
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


def _clip(reply: str) -> str:
    """A chat bubble stays short: whole sentences up to MAX_REPLY_CHARS."""
    if len(reply) <= MAX_REPLY_CHARS:
        return reply
    kept = ""
    for sentence in re.split(r"(?<=[.!?…])\s+", reply):
        if len(kept) + len(sentence) + 1 > MAX_REPLY_CHARS:
            # A long unbroken run (a train list) that would otherwise vanish: when most of
            # the bubble is still empty, keep its beginning up to an item boundary.
            room = MAX_REPLY_CHARS - len(kept) - 2
            if len(kept) < MAX_REPLY_CHARS // 2 and room > 20:
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


def _has_nearby(place: retrieval.Place) -> bool:
    return bool(tools.nearby_spots(place.id, walk_minutes=10**6))


def _finalize(draft: Draft, context: ResolvedContext, mode: str = "mock") -> ChatResponse:
    """Every rule here holds no matter what the responder (mock or LLM) produced."""
    raw = context.raw
    content = context.content
    pinned = {p.id for p in context.pins}
    blocked = set(raw.dismissed) | set(context.unknown_ids)
    reply = _clip(draft.reply)
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
    if ungrounded and reply != NO_TIME_WITHOUT_TOOL:
        if reply != NO_INFO:
            _log_replaced(f"no documents ({draft.intent})", reply)
        reply = NO_INFO
        chips = [c for c in chips if content.place_in_text(c) is not None]
        proposals = []  # nothing to go by: no places are offered on the back of it either
    # Safety (principle 6): a clear "not recommended" plus a public alternative, always.
    # Only when the question itself is about tracks/crossings: a safe question the model
    # happened to label "safety" (e.g. photos on the platform) keeps its real answer.
    if draft.intent == "safety" and any(w in context.message for w in RISK_WORDS):
        if "권하지 않아요" not in reply:  # hedged answers ("가능할 수도…") are replaced
            _log_replaced("safety without a clear refusal", reply)
            reply = SAFETY_NO
        if not any(w in reply for w in ALTERNATIVE_WORDS):
            reply = f"{reply.rstrip()} {SAFETY_ALTERNATIVE}"
        if "safety_track_crossing" in content.docs and "safety_track_crossing" not in sources:
            sources.insert(0, "safety_track_crossing")
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

    # Chips must lead somewhere: no "nearby" chip for a place without nearby data.
    kept = []
    for chip in chips:
        place = content.place_in_text(chip)
        if place is not None and any(w in chip for w in NEARBY_WORDS) and not _has_nearby(place):
            continue
        if chip not in kept:
            kept.append(chip)
    # At least two chips so a conversation can continue by tapping alone.
    fillers = []
    if subject is not None and subject.kind == "station":
        fillers += [f"{subject.name} 열차 리듬"]
        if _has_nearby(subject):
            fillers.append(f"{subject.name} 근처")
    fillers += DEFAULT_CHIPS
    for chip in fillers:
        if len(kept) >= 2:
            break
        if chip not in kept:
            kept.append(chip)
    chips = kept

    return ChatResponse(
        reply=reply,
        intent=draft.intent,
        chips=chips[:MAX_CHIPS],
        proposals=proposals,
        sources=sources,
        tool_used=draft.tool_used,
        mode=mode,
    )


# --- LLM responder: one structured call, a second only after a tool request ------

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
    static = prompts.build_static(content)
    screen = prompts.build_context(
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

    def call(tool_result: dict | None, label: str) -> dict:
        messages = prompts.build_messages(
            static=static, history=history, context=screen,
            message=request.message, tool_result=tool_result,
        )
        with stages.timed(f"llm_{label}"):
            return llm.chat_json(settings, messages, prompts.DRAFT_SCHEMA, label, deadline)

    first = call(None, "first")
    # Walking time: asked in words the server knows, or by the model's own tool request.
    risky = any(word in request.message for word in RISK_WORDS)
    pair = None if risky else _walk_pair(context)
    asked = first.get("tool_request") or {}
    by_ride = any(word in request.message for word in RIDE_WORDS)
    if pair is None and not risky and not by_ride and asked.get("name") == "walk_time":
        ends = [content.place(asked.get(key) or "") for key in ("station_id", "place_id")]
        if None not in ends and ends[0] != ends[1]:
            pair = (ends[0], ends[1])
    if pair is not None:
        return _walk_draft(*pair)
    run = None
    if first.get("tool_request"):
        with stages.timed("tool"):
            run = _run_tool(first["tool_request"], context)
        final = call(run.result, "after_tool")  # a second tool request is ignored
    else:
        final = first
    draft = _parse_draft(final)
    provided = {d.id for d in docs}
    draft.sources = [s for s in draft.sources if s in provided]
    draft.reply = _tidy(draft.reply, first_turn=not request.history)
    if run is not None and run.name is not None:
        draft.tool_used = run.name
        draft.tool_certainty = run.certainty
        draft.tool_source = run.source
        draft.tool_basis = run.result.get("basis")
        draft.tool_place = run.place_id
        draft.evidence = "\n".join(
            [json.dumps(run.result, ensure_ascii=False, default=str), run.evidence])
        draft.train_count = run.train_count
    # Documents the model was given are evidence too, cited or not ("하루 6편" from a doc).
    draft.evidence = "\n".join([draft.evidence, *(d.body for d in docs)])
    draft.vector_docs = vector_docs
    draft.named_docs = tuple(h.doc.id for h in hits if h.reason != "vector")
    return draft


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
        )
    except (KeyError, TypeError) as exc:
        raise llm.LLMError(f"answer did not match the schema: {exc}") from exc


def _weakest(certainties) -> Certainty | None:
    present = [c for c in certainties if c]
    return max(present, key=_CERTAINTY_ORDER.index) if present else None


def _nearby_candidates(
    place_id: str, context: ResolvedContext, gap: tools.Gap | None = None
) -> list[dict]:
    """Places worth proposing: not already pinned, not dismissed. For a gap they come from
    the shared rule (modules/recommend), so the chat and the plan screen propose the
    same places for the same reason. Until the catalog has coordinates the rule cannot
    measure a walk, and the example walking minutes are used instead."""
    skip = {p.id for p in context.pins} | set(context.raw.dismissed)
    anchor = context.content.place(place_id)
    if gap is not None and anchor is not None and anchor.lat is not None:
        return [
            {"id": s.target, "name": context.content.places[s.target].name,
             "walk_minutes": s.walk_min, "reason": s.reason, "notes": s.notes}
            for s in recommend_service.suggest(context.content.places, place_id, gap.start, gap.end,
                                           skip=skip, date=context.raw.date)
        ]
    return [
        {"id": s.place_id, "name": p.name, "walk_minutes": s.walk_minutes}
        for s in tools.nearby_spots(place_id)
        if s.place_id not in skip and (p := context.content.place(s.place_id)) is not None
    ]


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


def _run_tool(request: dict, context: ResolvedContext) -> ToolRun:
    """Run one requested tool. Certainty comes from the result, never from the model."""
    name = request.get("name")
    content = context.content
    # Tools may only look at places the user put in play (screen or message): the
    # model must not pick a station on its own, e.g. guess Donghae when none is open.
    in_play = {p.id for p in ([context.focused] if context.focused else []) + list(context.pins)}
    for place in (context.mentioned, context.recalled):
        if place:
            in_play.add(place.id)
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
        return ToolRun(name, {"tool": name, "place": anchor.name, "spots": spots,
                              "source": tools.SOURCE}, None, anchor.id, tools.SOURCE)

    return ToolRun(None, {"tool": name, "error": "알 수 없는 도구"})


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
            reply="아직 확인된 정보가 없어요. 지도에서 궁금한 역이나 장소를 열어 두고 다시 물어봐 주세요.",
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
    content = context.content
    anchor = context.anchor
    spots = tools.nearby_spots(anchor.id) if anchor else []
    stations = [
        p.name for p in (content.place(s.place_id) for s in spots) if p and p.kind == "station"
    ]
    where = f"{anchor.name} 근처" if anchor else "가까운 역"
    reply = (
        "선로·건널목 안이나 차단기 안쪽에서의 촬영·진입은 권하지 않아요. "
        f"대신 {where}의 역 맞이방·승강장처럼 공개된 장소에서 볼 수 있는지 찾아볼게요."
    )
    if stations:
        reply += f" 걸어갈 만한 역으로 {', '.join(stations)}이 있어요."
    sources = ["safety_track_crossing"]
    for hit in content.search(message):
        if hit.doc.kind == "safety" and hit.doc.id not in sources:
            reply += f" 참고로, {hit.doc.summary}"
            sources.append(hit.doc.id)
            break
    return Draft(
        reply=reply,
        intent="safety",
        chips=["안전하게 볼 수 있는 곳", "이 역 열차 시각", "근처에 같이 볼 곳"],
        sources=sources,
        tool_used="nearby_spots" if spots else None,
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
            if place is not None and place.kind == "poi":
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
    spots = [
        (place, s.walk_minutes)
        for s in tools.nearby_spots(anchor.id)
        if (place := content.place(s.place_id)) is not None
    ]
    if not spots:
        return Draft(
            reply=f"{anchor.name} 근처에서 걸어갈 만한 곳은 아직 확인된 정보가 없어요.",
            intent="proposal",
            chips=[f"{anchor.name} 열차 리듬", "동해역 근처"],
        )
    listed = ", ".join(f"{place.name}(도보 {minutes}분)" for place, minutes in spots)
    return Draft(
        reply=f"{MOCK_NOTE} {anchor.name} 근처에 {listed}이 있어요. 담을지는 직접 골라 주세요.",
        intent="proposal",
        chips=[f"{anchor.name} 열차 리듬", "빈 시간은 그대로 둘래요"],
        proposals=[
            DraftProposal(
                type="add",
                target=place.id,
                reason=f"{anchor.name}에서 도보 {minutes}분이에요",
            )
            for place, minutes in spots
        ],
        tool_used="nearby_spots",
        tool_source=tools.SOURCE,
        tool_place=anchor.id,
    )
