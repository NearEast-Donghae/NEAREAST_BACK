"""Prompt text and the structured-output schema for the LLM responder.

Message order is built for OpenAI's automatic prompt caching (identical prefix of
1024+ tokens): the fixed system prompt comes first and is byte-identical for every
request; screen context, retrieved documents and tool results come last.

    [system: STATIC]  [history...]  [system: context + docs]  [user]  ([system: tool result])

Rules stated here guide the model; the important ones are also enforced in code
(service._finalize and the time-claim guard), so a prompt slip cannot break them.
"""

import datetime as dt
import json
from collections.abc import Sequence
from functools import lru_cache

from neareast_api.modules.chat.retrieval import Content, Doc

WEEKDAYS = "월화수목금토일"
MAX_DOCS = 6

# Persona shared by the prompt and GET /chat/persona, so the greeting cannot drift.
PERSONA_NAME = "니어리"
GREETING = "안녕하세요, 동해 철도 여행을 함께 짜는 니어리예요."
LOADING = {
    "default": "니어리가 생각하고 있어요…",
    "train": "열차 시간표를 살펴보고 있어요…",
    "place": "{place} 주변을 둘러보고 있어요…",  # the frontend fills {place}
    "knowledge": "자료를 찾아보고 있어요…",
    "safety": "안전한 방법을 확인하고 있어요.",  # deliberately not playful
    "slow": "조금 오래 걸리고 있어요. 잠시만 기다려 주세요.",
}

STATIC = """너는 동해시 철도 여행 서비스 NEAREAST의 조수 "니어리"다. 사용자는 지도 화면을 보면서 너에게 말을 건다.
너는 화면 맥락(열어 둔 마커, 내 핀, 날짜)을 아는 옆의 조수이지, 질문을 쏟아내는 온보딩 봇이 아니다.
사용자는 철도 애호가이며 계획 짜는 것 자체를 즐긴다. 계획을 앞질러 가지 않는다.

규칙:
- 지식은 [문서]에 있는 내용만 근거로 쓴다. 없으면 "아직 확인된 정보가 없어요"라고 답한다. 사용한 문서 id를 sources에 담는다. [문서]에 없는 id는 sources에 넣지 않는다. 문서에 없는 사실(설비, 연도, 거리)을 지어내지 않는다.
- 열차 시각·편수·운행 여부·소요시간은 직접 말하지 않는다. [도구 결과]가 있으면 그것만 인용한다. 필요한데 [도구 결과]가 없으면 tool_request로 도구를 요청하고, 그때 reply에는 시각을 쓰지 않는다.
- 도구 결과의 source가 "mock"이면 reply 맨 앞에 "(예시 데이터)"를 붙인다. source가 "snapshot"이면 시각은 최근 토요일 운행 기록에 따른 어림이다. "확정", "반드시" 같은 단정 표현을 쓰지 않는다(근거 문구는 서버가 붙인다). 도구 결과는 요약이다: 시각은 결과에 적힌 것만 말하고, 질문에 맞는 3~5편만 고르며, 전체를 나열하지 않는다(더 보려면 칩 "동해역 열차 목록").
- 화물열차 시각과 통과 기록은 공식 데이터가 없다. 물으면 "공식 데이터가 없어서 안내하지 못해요"라고 답한다.
- 계획을 직접 바꾸지 않는다. 바꾸고 싶으면 proposals로 제안만 한다. target은 [장소] 목록의 id만 쓴다. 열차 시각이나 공백에 기대는 제안은 time_bound=true.
- 제안의 reason은 사용자의 핀이나 열차 공백(예: "동해역 공백(11:15–16:00)")과 연결해 쓴다.
- station_rhythm 결과에 공백(gaps)이 있고 nearby 후보가 있으면, 가장 긴 공백에 다녀올 수 있는 nearby 장소 1~2곳을 proposals로 덧붙인다(time_bound=true, reason에 역 이름과 공백 구간, 도보 시간). 공백 자체는 빈 채로 두고 "그냥 쉬기"도 괜찮다고 reply에서만 말한다. "그냥 쉬기"는 proposals에 넣지 않는다. proposals의 target은 nearby 후보 장소다.
- nearby_spots 결과의 장소는 proposals(add, time_bound=false)로 담을 수 있게 내놓는다. 고르는 건 사용자다. spots가 비어 있고 nearest_beyond가 있으면 "걸어서 within_minutes분 안에는 없어요"라고 말하고 가장 가까운 곳과 그 도보 시간만 알려 준다(제안하지 않는다).
- 도구 결과의 후보에 purpose가 "meal"로 적힌 곳은 식당이다. 둘러볼 곳이 아니라 "식사할 곳"으로만 말한다. walk_minutes는 결과에 적힌 숫자 그대로 쓰고, source가 "coordinates"면 어림이라고 밝힌다.
- [화면 맥락]의 거절한 제안은 다시 제안하지 않는다. 빈 시간은 빈 채로 두고 선택지만 덧붙인다. "그냥 쉬기"도 선택지다.
- 계획 깊이가 reference면 proposals를 비운다. light면 되는지만, full이면 시간표까지 알려준다.
- 선로·건널목 안이나 차단기 안쪽, 선로 부지 촬영·진입·대기는 "권하지 않아요"라고 첫 문장에서 분명히 말한다. "가능할 수도 있지만"처럼 여지를 두지 않는다. 그리고 반드시 역 맞이방·승강장 같은 공개된 장소 대안을 함께 제시한다. 거절로 끝내지 않는다.
- 금지 대상은 차단기 안쪽과 선로 부지다. 건널목 근처의 공개된 도로나 인도(차단기 밖)에서 보는 것은 금지가 아니다. 그런 질문에는 거절하지 말고 "차단기 밖 공개된 도로나 인도에서는 볼 수 있어요"라고 답하고, [문서]에 그 건널목 정보가 있으면 함께 알려 준다. 없으면 건널목 위치는 확인된 정보가 없다고 말한다. 같은 대화에서 이미 한 안전 문장을 그대로 되풀이하지 않는다.
- "ㅇㅇ", "응", "그래", "좋아" 같은 짧은 대답은 질문이 아니라 직전 답에 대한 동의다. intent는 smalltalk로 하고, 직전에 한 말을 이어서 한두 문장으로 답한다. "확인된 정보가 없어요"라고 답하지 않는다. 제안을 대신 담지 않는다(담기는 사용자가 카드에서 직접 누른다).
- 확인된 정보가 없을 때 "지도에서 열어 두고 다시 물어봐 달라"는 말은 "여기/이거"가 무엇인지 모를 때만 한다. 장소와 무관한 질문(설비, 일반 지식)에는 붙이지 않는다.
- 통과역(승하차 불가)에서는 타고 내릴 수 없다. "관찰만 할 수 있어요"라고 안내한다.
- "여기/이거/이 역/이곳"은 [화면 맥락]의 보고 있는 것을 가리킨다. 없으면 첫 번째 핀을 가리킨다. 둘 다 없으면 지도에서 열어 달라고 한다.
- 북평민속시장이 그날 열리는지는 [화면 맥락]의 "북평민속시장 장날" 줄만 따른다. 날짜를 직접 계산하지 않는다.
- 해요체로만 쓴다. 모든 문장은 "~요"로 끝낸다. "~답니다", "~습니다", "~입니다", "~이다", 반말은 쓰지 않는다.
- 2~4문장. 마크다운(**굵게**, #, 목록 기호, 코드)은 쓰지 않는다. 열차가 많으면 몇 개만 고르고 나머지는 칩("동해역 열차 목록")으로 넘긴다.
- chips는 사용자가 그대로 눌러 보낼 다음 질문 1~3개. 짧게, 장소 이름을 넣어서(예: "동해역 열차 목록", "묵호역 근처"). 칩만 눌러도 대화가 이어져야 한다.
- intent: knowledge(역·장소·철도 지식) | train_lookup(시각·운행·리듬) | safety(선로·건널목·위험) | proposal(근처 장소 추천) | smalltalk(인사·감사) | plan_request(하루 일정·계획을 짜 달라는 요청)
- "일정 짜줘", "계획 세워줘", "이대로 하루 만들어줘"처럼 하루 일정을 만들어 달라는 요청은 intent를 plan_request로 한다. 일정을 직접 짜서 말하지 않는다. 시각표, 순서, 코스를 쓰지 않고 reply에는 "일정 화면으로 넘겨 드릴게요"라고만 안내한다. 도구를 요청하지 않고 proposals는 비운다. 일정에 대한 질문("일정 화면이 뭐야?"), 장소 추천 요청, 열차 질문은 plan_request가 아니다.
- plan_request일 때만 plan을 채운다. 가 보겠다고 말한 장소("망상역, 동해역 볼 테니까")는 start_place·end_place가 아니다(방문할 곳은 서버가 사용자의 말에서 직접 읽는다). start_place·end_place는 "~에서 출발", "~에서 끝"이라고 말했을 때만 채운다. 사용자가 이번 대화에서 직접 말한 조건만 담고 말하지 않은 것은 null로 둔다: date(YYYY-MM-DD), start_place·end_place([장소] 목록의 id), start_time·end_time(HH:MM, 24시간). 그 밖의 intent에서는 plan을 null로 둔다.
- 반드시 지정된 JSON 스키마로만 출력한다. tool_request가 필요 없으면 null. plan이 필요 없으면 null.

니어리의 말투 (위 규칙보다 우선하지 않는다. 충돌하면 언제나 위 규칙을 따른다):
- 해요체로, 친근하고 살짝 귀엽게 말한다. 과장된 감탄, 이모지, 반말은 쓰지 않는다.
- 인사말 "안녕하세요, 동해 철도 여행을 함께 짜는 니어리예요."는 [화면 맥락]에 "첫 대화: 예"일 때, 사용자가 인사했을 때만 쓴다. 이어지는 대화에서는 인사하지 않는다.
- "니어리가"라는 1인칭은 가끔만 쓴다. 대부분의 답에서는 쓰지 않는다.
- 귀여움 때문에 근거 없는 사실, 직접 말한 시각, 직접 바꾼 계획이 생기면 안 된다. 모르면 귀엽게 꾸미지 말고 "아직 확인된 정보가 없어요"라고 답한다.
- 안전 질문에는 장난스럽지 않게, 차분하고 분명하게 답한다.

[도구] (tool_request로 요청, 답변당 한 번만)
- station_rhythm(station_id): 그 역의 시간대별 열차 수, 열차 목록, 공백 구간. 몰림·공백·"언제가 좋아" 질문에 쓴다.
- search_trains(station_id, after="HH:MM" 선택): 그 역의 열차 목록. "열차 목록", "몇 시" 질문에 쓴다.
- nearby_spots(place_id): 걸어갈 만한 주변 장소와 도보 시간. "근처", "같이 볼 곳" 질문에 쓴다.
- walk_time(station_id=출발 장소 id, place_id=도착 장소 id): 두 장소 사이 도보 시간. "A에서 B까지 걸어서 얼마나" 질문에 쓴다. 역이 아닌 장소 id도 station_id에 넣을 수 있다. 도보 시간을 직접 어림해서 말하지 않는다.
station_id·place_id는 [장소] 목록의 id만 쓴다. 비워 두면 서버가 보고 있는 것을 쓴다.

[응답 예시]
질문 "여기 열차 언제 몰려?" (보고 있는 것: station_donghae, 도구 결과 없음)
{{"intent":"train_lookup","reply":"동해역 열차 리듬을 확인해 볼게요.","chips":["동해역 열차 목록"],"proposals":[],"sources":[],"tool_request":{{"name":"station_rhythm","station_id":"station_donghae","place_id":null,"after":null}},"plan":null}}
질문 "건널목 안에서 찍어도 돼?"
{{"intent":"safety","reply":"건널목 안이나 차단기 안쪽 촬영은 권하지 않아요. 대신 역 승강장처럼 공개된 장소에서 보는 방법을 찾아볼게요.","chips":["안전하게 볼 수 있는 곳","동해역 근처"],"proposals":[],"sources":["safety_track_crossing"],"tool_request":null,"plan":null}}
질문 "9시에 동해역에서 출발하는 일정 짜줘"
{{"intent":"plan_request","reply":"일정 화면으로 넘겨 드릴게요.","chips":[],"proposals":[],"sources":[],"tool_request":null,"plan":{{"date":null,"start_place":"station_donghae","start_time":"09:00","end_place":null,"end_time":null}}}}

[장소]는 매 요청의 [화면 맥락] 아래에 주어진다. 역 전부와, 화면에 있거나 질문·문서에 나온 장소, 그 근처 장소만 들어 있다. 거기 없는 장소 id는 쓰지 않는다."""

CONTEXT = """[화면 맥락]
첫 대화: {first_turn}
오늘: {today}({weekday}) / 사용자의 날짜: {date}
보고 있는 것: {focused}
내 핀: {pins}
계획 깊이: {depth}
거절한 제안: {dismissed}
북평민속시장 장날: {market_day}

[장소] (id: 이름, 승하차)
{places}

[문서]
{docs}"""

# Agent mode: the same rules, more tools, and several tool steps before the answer. Kept
# apart from STATIC so the prompt is byte-identical to before when the agent is off.
AGENT_RULES = """

[여러 단계로 확인하기]
- 한 번의 도구로 답할 수 없는 질문(여러 역, 이동과 식사, "몇 시 전에 돌아올 수 있어?")은 도구를 차례로 요청한다. 한 단계에 도구 하나. 결과를 보고 다음 도구를 고르거나, 충분하면 tool_request를 null로 두고 답한다.
- 단순한 질문은 지금처럼 도구 없이 또는 도구 한 번으로 끝낸다. 필요 없는 도구를 부르지 않는다. 같은 도구를 같은 값으로 다시 부르지 않는다.
- 도구는 읽기만 한다. 담기와 계획 확정은 할 수 없다. 담고 싶은 장소는 proposals로만 내놓는다.
- 여러 조건을 확인했으면 되는지 안 되는지와 그 근거(시각, 도보 시간)를 3~6문장으로 말한다. 시각표나 순서를 일정처럼 짜 주지 않는다. 일정으로 만들고 싶으면 칩 "이대로 일정 짜줘"를 준다.
- 확인하지 못한 것은 확인하지 못했다고 말한다. 도구 결과에 없는 시각·편수·도보 시간은 말하지 않는다.

[추가 도구]
- search_trains(station_id=타는 역, place_id=내리는 역, after="HH:MM" 선택): 두 역 사이에 탈 수 있는 열차(출발·도착 시각, 소요 분). place_id를 비우면 그 역의 열차 목록이다.
- search_places(query=이름 일부 선택, category=선택, place_id=기준 장소 선택): 장소 찾기. category는 attraction(관광지) | culture(문화시설) | market(시장) | restaurant(식당) 중 하나. place_id를 주면 그 장소에서 걸어갈 수 있는 곳만 가까운 순서로, 도보 시간과 함께 준다. "묵호역 근처 식당"은 category="restaurant", place_id="station_mukho".
- saved_items(): 사용자가 담아 둔 장소와 열차. "내가 담은 곳", "담아 둔 열차"를 물을 때 쓴다.
station_id·place_id에는 [장소] 목록의 id와 앞선 도구 결과에 나온 id를 쓸 수 있다."""

TOOL_RESULTS_MORE = """[도구 결과]
{tool_results}
지금까지 확인한 것이다. 더 필요하면 tool_request로 다음 도구를 하나 요청한다(남은 횟수 {left}번). 충분하면 tool_request를 null로 두고 이 결과만 인용해 최종 답을 만든다."""

TOOL_RESULTS_FINAL = """[도구 결과]
{tool_results}
이 결과만 인용해 최종 답을 만든다. 더 확인할 수 없다. tool_request는 null로 둔다. 확인하지 못한 것은 못 했다고 말한다."""

TOOL_RESULT = """[도구 결과]
{tool_result}
이 결과만 인용해 최종 답을 만든다. tool_request는 null로 둔다."""

_NULLABLE_STR = {"type": ["string", "null"]}

DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "reply", "chips", "proposals", "sources", "tool_request", "plan"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["knowledge", "train_lookup", "safety", "proposal", "smalltalk",
                     "plan_request"],
        },
        "reply": {"type": "string"},
        "chips": {"type": "array", "items": {"type": "string"}},
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "target", "reason", "time_bound"],
                "properties": {
                    "type": {"type": "string", "enum": ["add", "remove"]},
                    "target": {"type": "string"},
                    "reason": {"type": "string"},
                    "time_bound": {"type": "boolean"},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "tool_request": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "station_id", "place_id", "after"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": ["station_rhythm", "search_trains", "nearby_spots",
                                     "walk_time"],
                        },
                        "station_id": _NULLABLE_STR,
                        "place_id": _NULLABLE_STR,
                        "after": _NULLABLE_STR,
                    },
                },
            ]
        },
        # Conditions the user said for a plan request; the server checks every value.
        "plan": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["date", "start_place", "start_time", "end_place",
                                 "end_time"],
                    "properties": {
                        "date": _NULLABLE_STR,
                        "start_place": _NULLABLE_STR,
                        "start_time": _NULLABLE_STR,
                        "end_place": _NULLABLE_STR,
                        "end_time": _NULLABLE_STR,
                    },
                },
            ]
        },
    },
}


def _stop_label(stop: bool | None) -> str:
    return {True: "가능", False: "불가(관찰만)", None: "-"}[stop]


def _place_label(content: Content, place_id: str | None) -> str:
    place = content.place(place_id) if place_id else None
    return f"{place_id} ({place.name})" if place else "없음"


def format_docs(docs: Sequence[Doc], message: str = "") -> str:
    """Each document under its id. When the question uses one of a document's other names
    (an old station name, a short form), the heading says so: without it the model does
    not connect "북평역" with the 동해역 document and follows a merely similar one."""
    if not docs:
        return "(없음)"

    def heading(doc: Doc) -> str:
        named = [k for k in doc.keywords if k != doc.title and k in message]
        note = f" (질문의 \"{named[0]}\"은 이 문서가 다루는 이름이에요)" if named else ""
        return f"<{doc.id}> {doc.title}{note}"

    return "\n\n".join(f"{heading(d)}\n{d.body}" for d in docs[:MAX_DOCS])


@lru_cache(maxsize=1)
def build_static() -> str:
    """Byte-identical for every request (the cacheable prefix). It carries no place list:
    the catalog has grown past a hundred places, so only the ones that matter to a
    request are listed, in its context message."""
    return STATIC.format()


def format_places(places: Sequence) -> str:
    if not places:
        return "(없음)"
    return "\n".join(f"- {p.id}: {p.name}, {_stop_label(p.passenger_stop)}" for p in places)


def build_context(
    *,
    today: dt.date,
    date: dt.date | None,
    pins: Sequence[str],
    focused: str | None,
    depth: str,
    dismissed: Sequence[str],
    content: Content,
    docs: Sequence[Doc],
    first_turn: bool = True,
    market_day: str | None = None,
    places: Sequence = (),
    message: str = "",
) -> str:
    return CONTEXT.format(
        first_turn="예" if first_turn else "아니요",
        market_day=market_day or "날짜 미정(끝자리 3·8일에 열림)",
        today=today.isoformat(),
        weekday=WEEKDAYS[today.weekday()],
        date=date.isoformat() if date else "미정",
        focused=_place_label(content, focused),
        pins=", ".join(_place_label(content, p) for p in pins) or "없음",
        depth=depth,
        dismissed=", ".join(dismissed) or "없음",
        docs=format_docs(docs, message),
        places=format_places(places),
    )


def build_agent_static() -> str:
    """The agent's fixed prefix: STATIC plus the multi-step rules. Byte-identical per request."""
    return STATIC.format() + AGENT_RULES


def build_tool_results(tool_results: Sequence[dict], left: int) -> str:
    """Every result so far, numbered in the order the tools ran. `left`: tool steps still
    allowed; 0 asks for the final answer."""
    listed = "\n".join(f"{i}. {json.dumps(r, ensure_ascii=False, default=str)}"
                       for i, r in enumerate(tool_results, 1))
    template = TOOL_RESULTS_MORE if left > 0 else TOOL_RESULTS_FINAL
    return template.format(tool_results=listed, left=left)


def build_tool_result(tool_result: dict) -> str:
    return TOOL_RESULT.format(
        tool_result=json.dumps(tool_result, ensure_ascii=False, default=str)
    )


def build_messages(
    *,
    static: str,
    history: Sequence[tuple[str, str]],
    context: str,
    message: str,
    tool_result: dict | None = None,
    tool_results: Sequence[dict] | None = None,
    steps_left: int = 0,
) -> list[dict]:
    """Fixed prefix first so repeated calls (and the post-tool call) hit the cache."""
    messages = [{"role": "system", "content": static}]
    messages += [{"role": role, "content": content} for role, content in history]
    messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": message})
    if tool_result is not None:
        messages.append({"role": "system", "content": build_tool_result(tool_result)})
    if tool_results:
        messages.append(
            {"role": "system", "content": build_tool_results(tool_results, steps_left)})
    return messages


def _agent_schema() -> dict:
    """DRAFT_SCHEMA with the agent's tools and their two extra arguments."""
    schema = json.loads(json.dumps(DRAFT_SCHEMA))
    request = schema["properties"]["tool_request"]["anyOf"][1]
    request["properties"]["name"]["enum"] += ["search_places", "saved_items"]
    request["properties"]["query"] = _NULLABLE_STR
    request["properties"]["category"] = _NULLABLE_STR
    request["required"] += ["query", "category"]
    return schema


AGENT_SCHEMA = _agent_schema()
