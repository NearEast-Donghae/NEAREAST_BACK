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
