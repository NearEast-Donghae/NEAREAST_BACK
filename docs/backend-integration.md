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
