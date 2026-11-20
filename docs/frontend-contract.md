# 최신 프론트 연결 점검

확인 기준: `NEAREAST_FRONT` main `586fee2`(GitHub 최신 main과 로컬 일치), 통합 백엔드 `feat/integrate-postgres-local`.
프론트 디자인은 유지했습니다. 이번 프론트 수정은 `src/api/account.ts`의 계정 편집 활성화뿐입니다.

## 연결된 흐름

- 가입·로그인·세션 복원: `/auth/signup`, `/auth/login`, `/users/me`. HttpOnly 쿠키와 CSRF 계약이 맞습니다.
- 대화 생성·목록·메시지·삭제: `/conversations`. 단순 대화 저장과 응답 표시가 연결되어 있습니다.
- 여행 저장·목록·삭제: `/itineraries`. **저장 성공은 여행 일정이 AI로 생성됐다는 뜻은 아닙니다.** 프론트 생성 내용은 아직 샘플이 섞여 있습니다.
- 이번 보완: 닉네임 변경 `PATCH /users/me`, 비밀번호 변경 `PUT /users/me/password`, 탈퇴 `DELETE /users/me`.
- Vite proxy는 Host를 변경해도 Origin을 전달합니다. 백엔드 `.env`에 localhost/127.0.0.1:5173을 허용해 실제 프록시 요청의 403을 방지했습니다.

Vite proxy는 개발 서버 설정입니다. 배포에서는 프론트와 같은 도메인의 `/api`를 백엔드로 reverse proxy하는 구성을 권장합니다. 별도 API 주소를 쓰면 `VITE_API_BASE_URL`, 허용 Origin, HTTPS 쿠키 정책을 함께 맞춰야 합니다.

Vite 5173을 실제로 띄워 프록시 경유 회원가입 201, 공개 장소·열차 200, 본인 조회 200, 닉네임 변경 200, 대화·여행 저장 201, 여행 조회 200, 비밀번호 변경·탈퇴 204, 탈퇴 후 조회 401을 확인했습니다. 임시 계정은 삭제했습니다. 프론트 production build도 통과했습니다. 브라우저 화면 자동화 검증은 하지 않았습니다.

## 백엔드에는 있으나 프론트 연결이 남은 기능

| 기능 | 현재 프론트 | 연결할 백엔드 / 수정 |
| --- | --- | --- |
| 지도 장소 목록·소개 | KakaoMap의 장소 검색, stations.ts 이름 목록, 역 팝업의 하드코딩 | `GET /places`, `/places/{place_id}`. Kakao는 지도 표시용으로 유지하고 서비스 장소 ID는 DB ID 사용 |
| 열차 검색·상세 | TrainInfoView의 EXAMPLE_RESULTS, MainPage의 DUMMY_STOPS | `/trains?from={stationId}&to={stationId}`, `/trains/{train_no}`, `/stations/{station_id}/trains` |
| 장소·열차 담기 동기화 | savedPlaces/savedTrains가 공용 localStorage 키 사용 | `/saved` 및 `/saved/places`, `/saved/trains`. 로그인 사용자별 로드·로그아웃 시 초기화 필요 |
| 챗봇의 여행 조건·계획 전환 | conversations.ts가 action을 버리고 reply/chips/add proposals만 사용 | `response.action.type === plan_request`의 `payload`를 `/planning-requests`의 conditions로 전달 |
| 챗봇에 현재 선택 전달 | sendConversationMessage가 request의 savedPlaces/conditions를 보내지 않음 | 유효한 DB ID를 context.pins/focused/date에 전달하고 저장 목록은 `/saved`와 동기화 |
| 실제 일정 생성 | MainPage는 비교 화면 전환 후 DUMMY_TIMELINE 저장. PlanningPage는 프론트에서 샘플 일정 생성 | `POST /planning-requests` → `POST /planning-requests/{id}/generations`의 itinerary 표시 |
| 여행 수정·이력 | 목록·삭제만 사용 | `PUT /itineraries/{id}`에 expectedRevision, `GET /itineraries/{id}/revisions` |
| 경로 표시 | 실제 생성 legs를 읽지 않음 | itinerary.legs[].coordinates는 `[경도, 위도]`, Kakao LatLng는 `(위도, 경도)` |

`station-${역이름}` 같은 프론트 임시 ID와 `station_mukho` 같은 DB ID는 다릅니다. 백엔드에서 임시 문자열을 임의로 추정하지 않습니다. `/places` 응답을 기준으로 연결해야 장소 중복과 잘못된 저장을 막을 수 있습니다.
저장된 여행 목록 어댑터도 현재 legs·revision을 버리므로 실제 경로와 수정 기능을 붙일 때 보존해야 합니다.

## 프론트에는 있으나 백엔드의 실제 구현 범위를 넘는 기능

- 열차 탑승/관찰을 포함한 최적 일정, 여러 날 여행, 복수 비교안: 현재 도보 계산기의 지원 범위 밖입니다.
- 자유시간 활동 추천·변경: 프론트의 데모 제안에 대응하는 동적 추천 API는 없습니다.
- 동해시 외 역 선택: 프론트 이름 목록의 모든 역이 현재 DB 카탈로그에 들어 있지는 않습니다.
- 관광지·식당 방문 자동 생성: DB에 장소가 있어도 방문 가능성이 미확인인 장소는 계산기가 거절합니다. 현재 허용 3곳입니다.

따라서 **프론트 전체 연결 완료 상태는 아닙니다.** 위 데모 흐름을 정상처럼 보이게 하기 위해 백엔드에서 가짜 일정이나 열차를 생성하지 않습니다. 프론트 담당자가 지도/담기/열차 연결을 진행하는 동안 플래너 담당자는 열차 연계와 방문 가능 장소를 확장해야 합니다.

연결 순서는 공통 장소 ID → 담기 동기화 → 열차 조회 → 챗봇 action 보존 → 실제 generations 응답 표시 → 경로 표시가 적합합니다.
