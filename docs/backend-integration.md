# 백엔드 연동 계약과 PR 영향

작업 브랜치: `feat/auth-conversations-itineraries`.
기반은 PR #4 `feat/timetable-snapshot`의 `9abed34`이며 이 브랜치에는 PR #1~#3 이력이 포함됩니다.
GitHub 재확인 결과 PR #1~#4의 head SHA는 작업 기준과 같고 모두 open 상태입니다.
PR #4 위에 추가한 후속 작업 브랜치입니다. 기존 PR 브랜치와 main은 변경하지 않습니다.

## 프론트 연결

장소·지도·열차 조회는 공개입니다. 챗봇·대화 기록·여행 계획 접근 전에 `/auth/signup` 또는 `/auth/login`을 호출합니다.
가입은 `{email, nickname, password}`, 로그인은 `{email, password}`이며 응답은 `{user, csrfToken}`입니다.
인증은 HttpOnly 쿠키입니다. 프론트 요청에 `credentials: "include"`, 변경 요청에
`X-CSRF-Token: <csrfToken>`이 필요합니다. 새로고침 후 `/users/me`로 복원합니다.
401은 로그인 화면, 403은 CSRF/origin 확인, 404는 소유권/존재 여부, 409는 재조회/동시 요청 처리 대상입니다.

대화 흐름:
1. `POST /conversations {title?: string}` → 대화방 id.
2. `POST /conversations/{id}/messages {requestId: UUID, message, context?: 기존 ChatContext}`.
3. 응답은 저장된 assistant MessageDto. `response` 안에 기존 ChatResponse의 reply/intent/chips/proposals/sources/tool_used/mode가 있습니다.
4. 재시도에는 같은 requestId와 같은 message를 보냅니다. 완료된 결과는 재사용하고 다른 내용은 409입니다.
5. `GET /conversations` 및 `GET /conversations/{id}/messages`로 대화방·기록을 복원합니다.

history를 클라이언트가 다시 구성할 필요 없이 서버가 최근 완료 메시지를 불러옵니다.
기존 `/chat/messages`도 유지하지만 이 호환 API 자체에는 대화 자동 저장이 없습니다.

여행 흐름:
- `/planning-requests`에 source(planner/chat), conversationId?, conditions를 저장합니다.
- 조건 DTO는 여행 날짜/종료 날짜/시작·종료 시각/출발·도착 장소/필수 장소/열차 선호/관심사/도보 제한을 포함합니다.
- 폼과 챗봇의 조건을 같은 DTO로 전달합니다. 자연어에서 조건을 추출하는 로직은 기존 PR에 없어 이번에는 추가하지 않았습니다.
- `/planning-requests/{id}/generations`는 생성기 어댑터를 연결하는 자리입니다. 미연결 상태는 503이며 성공을 가장한 예시 계획을 만들지 않습니다.
- `/itineraries`의 저장 DTO는 현재 프론트 SavedTrip의 source/title/travelDateLabel/timeRange/stations/trains/activities/timeline을 수용합니다.
- 지도 경로용 legs에는 fromPlaceId/toPlaceId/mode/durationMinutes/distanceMeters/coordinates/certainty/source를 담을 수 있습니다. coordinates는 [경도, 위도] 순서로 합의하여 연결합니다.
- PUT 수정 시 expectedRevision을 전달합니다. 저장된 각 버전은 revisions에서 조회합니다.

생성기 구현 전 필요한 합의: 입력은 PlanningConditionsDto, 출력은 검증 가능한 여행 DTO, 지도 실제 도로 경로는 별도 경로 API 근거가 필요합니다.
현재 연결 훅은 반환 형식까지 구현한 실제 생성 서비스가 아닙니다.

## 기존 PR 4개와의 관계

| PR | 유지한 기능 | 이번 변경과 접점 |
| --- | --- | --- |
| #1 chat scaffold | 채팅 URL·요청·응답 구조 | 공통 인증 추가. 익명 요청은 이제 401 |
| #2 chat knowledge | 장소 id, 문서 검색 순서 | 파일 로더를 DB repository로 대체. knowledge/data 파일 삭제 |
| #3 chat LLM | 모델 호출·프롬프트·도구·응답 후처리 | 설정에서 표준 OPENAI_API_KEY도 허용. 평가 스크립트는 로그인 후 호출 |
| #4 timetable snapshot | 역별 조회·구간 검색·열차 상세 | DB 시간표 로더와 DB 적재 스크립트로 변경 |

`chat/service.py`, `chat/llm.py`, `chat/prompts.py`, `chat/tools.py`는 기반 PR과 동일합니다.
`retrieval.py`, `places/service.py`, `trains/service.py`의 저장소 접근부는 변경했습니다.
같은 부분을 팀원이 수정하거나 삭제한 MD/JSON을 다시 수정하면 병합 시 충돌 또는 수정/삭제 충돌이 발생할 수 있습니다.
라우터·config·README·pyproject·수집 스크립트도 양쪽에서 변경하면 수동 조정이 필요할 수 있습니다.

PR #1~#4가 순서대로 동일 커밋으로 main에 병합되면 그 위의 후속 변경으로 검토할 수 있습니다.
스쿼시 병합이나 PR head 변경 뒤에는 실제 최신 main과 다시 비교해야 합니다. 미래 변경까지 무충돌이라고 보장하지 않습니다.
배포에는 코드뿐 아니라 pgvector 이미지, Alembic upgrade, 프론트 인증 연결을 함께 적용해야 합니다.

프론트 저장소에는 이전 연동 확인용 미완료 로컬 변경이 별도 브랜치에 남아 있습니다.
사용자가 프론트는 팀원에게 맡긴다고 한 이후에는 수정·빌드·통합 테스트를 진행하지 않았습니다.

## AI 플래너 온보딩 (대화 대신 칩 3개)

첫 화면은 질문 3개를 칩으로 받습니다. 선택지는 전부 시간표 데이터와 서버에 적힌 규칙으로 계산하며 LLM을 쓰지 않습니다. 두 API 모두 로그인 없이 호출할 수 있습니다.

1. 어느 역으로 → `GET /api/v1/onboarding/options`의 `destinations`
2. 어디서 출발 → `GET /api/v1/onboarding/origins?destination={placeId}`
3. 누구와 함께 → 1번 응답의 `companions`

### `GET /api/v1/onboarding/options`

```jsonc
{
  "destinations": [
    // featured=true 인 역을 칩으로, 나머지는 "그 외"로 묶습니다. 여객열차가 서지 않는 역(망상역 등)은 나오지 않습니다.
    {"placeId": "station_mukho", "name": "묵호역", "trainsPerDay": 36,
     "trainTypes": ["KTX-이음", "누리로", "ITX-마음", "새마을호"], "featured": true,
     "label": "하루 36편 · KTX-이음, 누리로, ITX-마음, 새마을호"},
    {"placeId": "station_donghae", "name": "동해역", "trainsPerDay": 40, "...": "..."},
    {"placeId": "station_chuam", "name": "추암역", "trainsPerDay": 6, "...": "..."}
  ],
  "companions": [
    {"id": "alone", "label": "혼자", "maxWalkingMinutes": null, "indoorFirst": false, "reason": null},
    {"id": "couple", "label": "연인", "maxWalkingMinutes": null, "indoorFirst": false, "reason": null},
    {"id": "child", "label": "어린이", "maxWalkingMinutes": 15, "indoorFirst": true,
     "reason": "어린이와 함께라 걷는 시간을 편도 15분 이내로 잡고 실내 시설을 먼저 보여드려요."},
    {"id": "parents", "label": "부모님", "maxWalkingMinutes": 20, "indoorFirst": false,
     "reason": "부모님과 함께라 걷는 시간을 편도 20분 이내로 잡았어요."}
  ],
  "targetDate": "2026-11-21",
  "basis": "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준"
}
```

`trainsPerDay`는 그 역에서 내릴 수 있는 열차 수입니다(통과 열차 제외). `trainTypes`는 많은 순서이며 종류가 확인되지 않은 열차는 빼고 셉니다.

### `GET /api/v1/onboarding/origins?destination=station_mukho`

```jsonc
{
  "destination": {"placeId": "station_mukho", "name": "묵호역"},
  "origins": [
    {"stationName": "강릉", "stationCode": "…", "placeId": null, "trainsPerDay": 10,
     "trainTypes": ["누리로", "KTX-이음", "ITX-마음", "새마을호"],
     "firstDeparture": "05:46", "lastDeparture": "19:57", "fastestMinutes": 35,
     "startsHere": true, "featured": true, "label": "하루 10편 · 누리로, KTX-이음, ITX-마음, 새마을호"},
    {"stationName": "청량리", "stationCode": "3900388", "placeId": null, "trainsPerDay": 8,
     "trainTypes": ["KTX-이음"], "firstDeparture": "07:06", "lastDeparture": "20:14",
     "fastestMinutes": 123, "startsHere": true, "featured": true, "label": "하루 8편 · KTX-이음"},
    {"stationName": "서울", "trainsPerDay": 4, "featured": true, "...": "..."},
    {"stationName": "동대구", "trainsPerDay": 4, "featured": true, "...": "..."},
    {"stationName": "동해", "placeId": "station_donghae", "trainsPerDay": 18, "featured": false, "...": "..."}
    // 이어서 그 열차가 서는 나머지 역(상봉, 양평, 원주권 등)
  ],
  "certainty": "estimated",
  "targetDate": "2026-11-21",
  "basis": "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준"
}
```

- 그 역에서 타서 고른 역에서 내릴 수 있는 열차가 있는 역만 나옵니다. 통과만 하는 역은 출발역이 아닙니다.
- `featured`는 열차가 그 역에서 운행을 시작하는(시발) 역 가운데 편수가 많은 4곳입니다. 칩으로 먼저 보여 주고 나머지는 "그 외"로 묶으면 됩니다. 서비스 지도 안의 역(`placeId`가 있는 역)은 여행의 출발지가 아니라 `featured`가 되지 않습니다.
- 상태 코드: 모르는 역 404 / 여객열차가 서지 않는 역 422(망상역 등) / 시간표 없음 503.
- 시각은 운행 기록에 따른 어림입니다. 화면에 `basis`를 함께 보여 주세요.

### 동행(`companions`)을 넘기는 곳

값은 `alone | couple | child | parents`이고 전부 선택 칸입니다. 보내지 않으면 아무것도 바뀌지 않습니다.

- 여행 조건: `POST /planning-requests`의 `conditions.companions`. 기존 칸은 그대로입니다. 규칙이 있는 동행이면 `maxWalkingMinutes`도 함께 넣어 주세요(1번 응답의 값). 플래너는 지금도 `maxWalkingMinutes`를 읽습니다.
- 챗봇: 메시지의 `context.companions`. 근처 추천과 열차 공백 제안에 규칙이 적용되고, 적용됐을 때 답 끝에 `reason` 문장이 붙습니다. "계획 짜줘"로 만들어지는 카드의 조건에도 `companions`와 `maxWalkingMinutes`가 들어갑니다.

```jsonc
// POST /conversations/{id}/messages
{"requestId": "…", "message": "동해역 근처", "context": {"focused": "station_donghae", "companions": "parents"}}
```

동행별로 바뀌는 것은 `modules/recommend/companions.py`의 규칙이 전부입니다. 혼자·연인은 지금 바뀌는 것이 없어 `reason`이 `null`입니다.

### 챗봇 응답에서 바뀐 것 (2026-11-20)

- "망상역, 동해역, 추암역 볼 테니까 계획 짜줘"처럼 말한 장소는 카드 조건의 `requiredPlaceIds`에 들어갑니다. 앞 대화에서 말한 장소·날짜·시간도 반영됩니다(대화방의 최근 10개 메시지 안에서).
- 계획 카드 뒤의 "ㅇㅇ/응/좋아"에는 같은 `action`이 다시 실린 안내가 옵니다. 카드를 다시 그려도 되고 기존 카드를 그대로 두어도 됩니다.
- 건널목을 차단기 밖 공개 도로에서 보는 질문은 거절하지 않습니다. `intent`는 `safety` 그대로입니다.

## 니어리가 확인한 단계 (`steps`)

챗봇 응답에 `steps`가 추가됐습니다. 니어리가 답을 만들면서 무엇을 조회했는지를 순서대로 담습니다. 기존 필드는 그대로이고, 조회한 것이 없으면 빈 배열입니다.

```jsonc
// POST /conversations/{id}/messages 의 response
{
  "reply": "동해역에서 묵호역까지는 열차로 6~9분쯤 걸려요(예: 07:55→08:01). 묵호역에서 논골담길까지는 걸어서 약 20분이에요. …",
  "intent": "train_lookup",
  "tool_used": "search_trains",
  "steps": [
    {"tool": "search_trains", "summary": "동해역에서 묵호역까지 가는 열차 18편을 확인했어요."},
    {"tool": "walk_time", "summary": "묵호역에서 논골담길까지 도보 시간(약 20분)을 계산했어요."}
  ],
  "chips": ["동해역 열차 목록", "묵호역 근처"],
  "proposals": [], "sources": [], "action": null, "mode": "llm"
}
```

- `tool`: `station_rhythm | search_trains | nearby_spots | walk_time | search_places | saved_items`. 모두 읽기 전용입니다. 담기와 계획 확정은 도구가 아니라 지금처럼 사용자의 탭(제안 카드, 계획 카드)으로만 일어납니다.
- `summary`: 사용자에게 그대로 보여 줄 수 있는 한 문장입니다. 서버가 조회 결과에서 만들며 모델이 쓰지 않습니다. 조회에 실패한 단계도 "…을 확인하지 못했어요."로 들어갑니다.
- `tool_used`는 지금처럼 하나입니다. 여러 단계면 열차 조회가 있으면 그것, 없으면 마지막 조회입니다.
- `mode`가 `mock`·`fallback`이면 `steps`는 빈 배열입니다.
- 화면 제안: 답 말풍선 위에 접힌 "확인한 것 2가지"를 두고, 펼치면 `summary`를 순서대로 보여 줍니다.

### 서버 설정

| 환경변수 | 기본 | 뜻 |
| --- | --- | --- |
| `NEAREAST_CHAT_AGENT` | `false` | `true`면 한 답에 여러 번 조회(에이전트). `false`면 지금까지처럼 조회는 한 번까지 |
| `NEAREAST_CHAT_AGENT_MAX_STEPS` | `4` | 한 답에서 조회할 수 있는 최대 횟수(1~6) |

문제가 생기면 `NEAREAST_CHAT_AGENT=false`로 바꾸고 다시 띄우면 이전 동작으로 돌아갑니다. 끈 상태에서는 프롬프트와 출력 형식이 이전과 글자 단위로 같습니다. 전체 시간 상한(`NEAREAST_CHAT_DEADLINE_SECONDS`, 기본 12초)은 그대로이고, 시간이 모자라면 그때까지 확인한 것으로 답합니다.

### 제안: 단계를 실시간으로 보여 주기 (미구현, 결정 필요)

지금은 답이 끝난 뒤에 `steps`가 한꺼번에 옵니다. 여러 단계 답은 6~10초가 걸리므로, 그동안 로딩 문구 대신 실제 진행을 보여 주면 기다림이 설명됩니다.

- 방식: `POST /conversations/{id}/messages/stream` (Server-Sent Events). 같은 요청 본문을 받습니다.
- 이벤트:
  ```text
  event: step
  data: {"index": 1, "tool": "search_trains", "status": "running", "summary": "동해역에서 묵호역까지 가는 열차를 찾고 있어요…"}

  event: step
  data: {"index": 1, "tool": "search_trains", "status": "done", "summary": "동해역에서 묵호역까지 가는 열차 18편을 확인했어요."}

  event: answer
  data: { …지금의 response 전체(steps 포함)… }
  ```
- 답 본문은 글자 단위로 흘리지 않습니다. 서버 규칙(시각 대조, 안전, 제안 검사)이 완성된 답에 적용되기 때문에, 검사 전 문장이 화면에 나가면 안 됩니다. 흘리는 것은 단계뿐입니다.
- 기존 엔드포인트는 그대로 두므로 프론트는 준비되는 대로 옮기면 됩니다. 대화 저장과 `requestId` 중복 처리는 지금과 같게 합니다.
- 비용: 서버는 단계 사이에 이벤트를 내보내는 것뿐이라 작업량은 작지만, 프록시(nginx 등)의 버퍼링 해제와 프론트의 SSE 처리가 필요합니다. 시연 전에는 `steps`를 답과 함께 보여 주는 것만으로도 "무엇을 확인했는지"는 전달됩니다.
