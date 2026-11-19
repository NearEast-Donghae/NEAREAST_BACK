"""Chat business rules. Framework-free: the router converts errors to HTTP.

A responder (mock now, LLM in step 3) only produces a Draft. Plan principles are
enforced afterwards in `_finalize`, in code, so a responder that ignores the rules
cannot break them:
- proposals always need confirmation and never target dismissed/unknown ids
- `certainty` is copied from tool results, never chosen by the responder
- unknown focused/pin ids are ignored, disclosed in the reply, kept out of sources
"""

from dataclasses import dataclass, field

from neareast_api.core.config import Settings
from neareast_api.modules.chat import tools
from neareast_api.modules.chat.schemas import (
    Certainty,
    ChatContext,
    ChatRequest,
    ChatResponse,
    Intent,
    Proposal,
    ToolName,
)

MAX_CHIPS = 3


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


@dataclass(frozen=True)
class ResolvedContext:
    """Request context with ids checked against the place catalog."""

    raw: ChatContext
    focused: tools.Place | None
    pins: tuple[tools.Place, ...]
    unknown_ids: tuple[str, ...]

    @property
    def anchor(self) -> tools.Place | None:
        """What "here/this" refers to: the open marker, else the first pin."""
        return self.focused or (self.pins[0] if self.pins else None)

    @property
    def station(self) -> tools.Place | None:
        candidates = ([self.focused] if self.focused else []) + list(self.pins)
        return next((p for p in candidates if p.kind == "station"), None)


def answer(request: ChatRequest, settings: Settings) -> ChatResponse:
    if not settings.chat_mock:
        # Step 3 adds the LLM responder; until then only mock mode can answer.
        raise ChatUnavailableError
    context = _resolve(request.context)
    return _finalize(_mock_draft(request.message, context), context)


def _resolve(context: ChatContext) -> ResolvedContext:
    unknown: list[str] = []
    focused = None
    if context.focused is not None:
        focused = tools.get_place(context.focused)
        if focused is None:
            unknown.append(context.focused)
    pins = []
    for pin in context.pins:
        place = tools.get_place(pin)
        if place is None:
            if pin not in unknown:
                unknown.append(pin)
        elif place not in pins:
            pins.append(place)
    return ResolvedContext(context, focused, tuple(pins), tuple(unknown))


def _finalize(draft: Draft, context: ResolvedContext) -> ChatResponse:
    raw = context.raw
    pinned = {p.id for p in context.pins}
    blocked = set(raw.dismissed) | set(context.unknown_ids)
    proposals = []
    if raw.depth != "reference":  # depth=reference: information only, no plan changes
        for p in draft.proposals:
            if p.target in blocked or tools.get_place(p.target) is None:
                continue
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

    reply = draft.reply
    if context.unknown_ids:
        names = ", ".join(context.unknown_ids)
        reply = f"{names}은(는) 아직 모르는 장소라 빼고 답할게요. {reply}"

    sources = [
        s for s in dict.fromkeys(draft.sources)
        if s not in context.unknown_ids and tools.get_place(s) is not None
    ]
    return ChatResponse(
        reply=reply,
        intent=draft.intent,
        chips=draft.chips[:MAX_CHIPS],
        proposals=proposals,
        sources=sources,
        tool_used=draft.tool_used,
    )


# --- mock responder: deterministic keyword routing, no keys or network -----------

_SAFETY = ("건널목", "선로", "차단기", "철길", "철로")
_TRAIN = ("열차", "기차", "시각", "시간표", "몇 시", "몇시", "운행", "언제", "몰려", "리듬")
_KEEP_EMPTY = ("그대로", "쉬기", "쉴래")
_PROPOSAL = ("추천", "근처", "볼 곳", "볼 수 있는", "갈 곳", "뭐 하", "뭘 하", "일정")
_SMALLTALK = ("안녕", "고마", "감사")
_MOCK_NOTE = "(예시 데이터)"


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _mock_draft(message: str, context: ResolvedContext) -> Draft:
    if _has(message, _SAFETY):
        return _mock_safety(context)
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
        return Draft(
            reply="안녕하세요! 지도에서 역이나 장소를 열어 두고 물어보면 그 기준으로 답할게요.",
            intent="smalltalk",
            chips=["동해역 열차 리듬", "근처에 같이 볼 곳"],
        )
    anchor = context.anchor
    if anchor is None:
        return Draft(
            reply="아직 확인된 정보가 없어요. 지도에서 궁금한 역이나 장소를 열어 두고 다시 물어봐 주세요.",
            intent="knowledge",
            chips=["동해역 열차 리듬", "근처에 같이 볼 곳"],
        )
    # Step 2 adds knowledge/ retrieval; until then no document backs an answer.
    return Draft(
        reply=f"{anchor.name}에 대해 아직 확인된 정보가 없어요. 열차 리듬이나 근처 장소는 바로 볼 수 있어요.",
        intent="knowledge",
        chips=[f"{anchor.name} 열차 리듬", "근처에 같이 볼 곳"],
    )


def _mock_safety(context: ResolvedContext) -> Draft:
    anchor = context.anchor
    spots = tools.nearby_spots(anchor.id) if anchor else []
    places = [s.place.name for s in spots if s.place.kind == "station"]
    where = f"{anchor.name} 근처" if anchor else "가까운 역"
    reply = (
        "선로·건널목 안이나 차단기 안쪽에서의 촬영·진입은 권하지 않아요. "
        f"대신 {where}의 역 맞이방·승강장처럼 공개된 장소에서 볼 수 있는지 찾아볼게요."
    )
    if places:
        reply += f" 걸어갈 만한 역으로 {', '.join(places)}이 있어요."
    return Draft(
        reply=reply,
        intent="safety",
        chips=["안전하게 볼 수 있는 곳", "이 역 열차 시각", "근처에 같이 볼 곳"],
        tool_used="nearby_spots" if spots else None,
    )


def _mock_train(message: str, context: ResolvedContext) -> Draft:
    named = tools.find_place_in_text(message)
    station = named if named and named.kind == "station" else context.station
    if station is None:
        return Draft(
            reply="어느 역 기준으로 볼까요? 지도에서 역을 열어 두거나 핀을 찍어 주세요.",
            intent="train_lookup",
            chips=["동해역 열차 리듬", "묵호역 열차 리듬", "추암역 열차 리듬"],
        )
    rhythm = tools.station_rhythm(station.id, context.raw.date)
    if rhythm is None or not rhythm.trains:
        return Draft(
            reply=f"{station.name}의 열차 정보는 아직 준비되지 않았어요.",
            intent="train_lookup",
            chips=["동해역 열차 리듬", "근처에 같이 볼 곳"],
        )
    passing_note = (
        "" if station.passenger_stop
        else " 이 역은 열차가 서지 않고 지나가서, 타고 내릴 수는 없고 관찰만 할 수 있어요."
    )
    if "목록" in message:
        trains = tools.search_trains(context.raw.date, station=station.id)
        listed = ", ".join(
            f"{t.time} {t.kind}({t.direction}{'' if t.stops else ', 통과'})" for t in trains[:6]
        )
        return Draft(
            reply=f"{_MOCK_NOTE} {station.name} 열차: {listed}.{passing_note}",
            intent="train_lookup",
            chips=[f"{station.name} 열차 리듬", "근처에 같이 볼 곳"],
            tool_used="search_trains",
        )
    busiest_hour = max(rhythm.hourly, key=lambda h: (rhythm.hourly[h], -h))
    reply = (
        f"{_MOCK_NOTE} {station.name}은 {busiest_hour}시대에 열차가 가장 많아요"
        f"({rhythm.hourly[busiest_hour]}편)."
    )
    draft = Draft(
        reply=reply,
        intent="train_lookup",
        chips=["그 시간 열차 목록", "근처에 같이 볼 곳", "빈 시간은 그대로 둘래요"],
        tool_used="station_rhythm",
    )
    if rhythm.gaps:
        # The longest gap is where the day opens up for sightseeing.
        gap = max(rhythm.gaps, key=lambda g: tools.gap_minutes(g))
        draft.reply += f" {gap.start}~{gap.end}에는 열차가 없어요."
        draft.tool_certainty = gap.certainty
        for spot in tools.nearby_spots(station.id):
            if spot.place.kind == "poi":
                draft.proposals.append(
                    DraftProposal(
                        type="add",
                        target=spot.place.id,
                        reason=f"{station.name} 공백({gap.start}–{gap.end})에 다녀올 수 있어요"
                        f"(도보 {spot.walk_minutes}분)",
                        time_bound=True,
                    )
                )
                break
    draft.reply += passing_note
    return draft


def _mock_nearby(message: str, context: ResolvedContext) -> Draft:
    anchor = tools.find_place_in_text(message) or context.anchor
    if anchor is None:
        return Draft(
            reply="어디 근처를 볼까요? 지도에서 역이나 장소를 열어 두거나 핀을 찍어 주세요.",
            intent="proposal",
            chips=["동해역 근처", "묵호역 근처", "추암역 근처"],
        )
    spots = tools.nearby_spots(anchor.id)
    if not spots:
        return Draft(
            reply=f"{anchor.name} 근처에서 걸어갈 만한 곳은 아직 확인된 정보가 없어요.",
            intent="proposal",
            chips=[f"{anchor.name} 열차 리듬"],
        )
    listed = ", ".join(f"{s.place.name}(도보 {s.walk_minutes}분)" for s in spots)
    return Draft(
        reply=f"{anchor.name} 근처에 {listed}이 있어요. 담을지는 직접 골라 주세요.",
        intent="proposal",
        chips=[f"{anchor.name} 열차 리듬", "빈 시간은 그대로 둘래요"],
        proposals=[
            DraftProposal(
                type="add",
                target=s.place.id,
                reason=f"{anchor.name}에서 도보 {s.walk_minutes}분이에요",
            )
            for s in spots
        ],
        tool_used="nearby_spots",
    )
