# NearEast Backend

Python 3.12 · FastAPI · PostgreSQL 17 · pgvector(1536) · Alembic.
이메일/닉네임/비밀번호 가입, 쿠키 로그인, 사용자별 대화방과 메시지, 여행 조건·여행 계획 이력을 제공합니다.
장소·지식·열차 데이터는 PostgreSQL에서 읽습니다. 서비스 실행에 JSON/Markdown 데이터 파일은 필요하지 않습니다.

## Docker로 실행

Docker Desktop의 Linux 엔진을 실행합니다. 새 clone에서는 `.env.example`을 `.env`로 복사합니다.
기존 `.env`는 덮어쓰지 않습니다. 로컬 Uvicorn이 8012 포트를 쓰고 있으면 먼저 종료합니다.

```bash
docker compose -p neareast-dev up -d db
docker compose -p neareast-dev build backend
docker compose -p neareast-dev run --rm backend alembic upgrade head
docker compose -p neareast-dev up -d backend
```

실행 중인 backend 컨테이너에서 마이그레이션을 적용하려면 다음을 사용합니다.

```bash
docker compose -p neareast-dev exec backend alembic upgrade head
```

- Swagger: http://localhost:8012/docs
- Liveness: http://localhost:8012/health/live
- DB readiness: http://localhost:8012/health/ready
- 신규 환경의 호스트 DB 포트 기본값은 5432이며 `.env`에서 변경할 수 있습니다. 컨테이너 내부에서는 db:5432를 사용합니다.
- API는 시작할 때 테이블을 자동 생성하지 않습니다. 먼저 Alembic을 실행해야 합니다.
- named volume이 DB 데이터를 보관합니다. 일반 종료는 `docker compose -p neareast-dev down`입니다.

## 선택: 백엔드를 로컬 Python으로 실행

테스트·디버깅 시 DB만 Docker로 실행하고 백엔드는 로컬에서 실행할 수도 있습니다.
이 경우 `.env`의 DB 호스트/포트는 Docker가 호스트에 공개한 주소와 일치해야 합니다.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m uvicorn neareast_api.main:create_app --factory --env-file .env --host 127.0.0.1 --port 8012
```

## Swagger에서 로그인 후 확인

1. `POST /api/v1/auth/signup`: `email`, `nickname`, `password`를 보내면 가입 후 자동 로그인합니다.
2. 재접속은 `POST /api/v1/auth/login`: `email`, `password`를 보냅니다.
3. 응답의 `csrfToken`을 복사합니다. 인증 쿠키는 브라우저가 자동 저장합니다.
4. 조회 API는 그대로 실행합니다. POST/PATCH/PUT/DELETE는 `X-CSRF-Token` 입력란에 토큰을 넣습니다.
5. `GET /api/v1/users/me`로 로그인 사용자와 CSRF 토큰을 다시 받을 수 있습니다.

비밀번호는 Argon2id로 해싱하고 세션 토큰도 해시만 DB에 저장합니다.
이메일 인증은 없으며 형식·중복·비밀번호 길이는 검증합니다.
장소·지도·열차 조회는 비로그인으로 사용할 수 있습니다. 챗봇·대화 기록·여행 계획의 모든 기능은 로그인이 필요합니다.
Health와 개발용 API 문서는 공개입니다.
브라우저에서 `localhost`와 `127.0.0.1`을 섞으면 쿠키가 공유되지 않으므로 하나로 통일합니다.

## API

기본 경로 `/api/v1`. 새 DTO는 camelCase이며 기존 PR의 DTO 키는 호환성을 위해 유지했습니다.

| 리소스 | 메서드 | 용도 |
| --- | --- | --- |
| `/auth/signup`, `/auth/login`, `/auth/logout` | POST | 가입·로그인·로그아웃 |
| `/users/me` | GET | 현재 사용자·CSRF 토큰 |
| `/conversations` | GET, POST | 사용자 대화방 목록·생성 |
| `/conversations/{conversationId}` | GET, PATCH, DELETE | 조회·제목 변경·삭제 |
| `/conversations/{conversationId}/messages` | GET, POST | 대화 기록·메시지 전송 |
| `/planning-requests` | GET, POST | 여행 조건 목록·생성 |
| `/planning-requests/{requestId}` | GET, PATCH, DELETE | 여행 조건 조회·교체·삭제 |
| `/planning-requests/{requestId}/generations` | POST | 루트 생성기 연결 지점; 미연결 시 503 |
| `/itineraries` | GET, POST | 저장된 여행 목록·저장 |
| `/itineraries/{itineraryId}` | GET, PUT, DELETE | 여행 조회·교체·삭제 |
| `/itineraries/{itineraryId}/revisions` | GET | 여행 수정 이력 |
| `/places`, `/places/{place_id}` | GET | 장소 목록·상세 |
| `/stations/{station_id}/trains` | GET | 역 정차·통과 열차 |
| `/trains`, `/trains/{train_no}` | GET | 구간 검색·열차 상세 |
| `/saved`, `/saved/places`, `/saved/trains` | GET, POST, PATCH, DELETE | 담은 장소·열차 |
| `/chat/persona`, `/chat/messages` | GET, POST | 기존 PR 챗봇 계약 유지 |

대화 저장에는 `/conversations/{id}/messages`를 사용합니다. 기존 `/chat/messages`는 요청에 담긴
history를 사용하는 호환 API이며 자동 저장하지 않습니다. 다른 사용자의 리소스는 404를 반환합니다.
목록에는 `limit`, `offset`을 지원합니다. 메시지는 시간순, 여행·대화방 목록은 최신순입니다.
여행·여행 조건 수정에는 `expectedRevision`을 보내며 이전 버전이면 409를 반환합니다.
여행 조건의 `selectedTrains: [{trainNo, action: "ride"|"observe", stationId}]`는 선택 칸입니다. 보내지 않으면 `selectedTrainIds`만으로 동작하고,
보내면 두 칸의 열차가 같아야 합니다(한쪽에만 있는 열차가 있으면 422).

## 담기 API — `/saved`

로그인한 사용자가 담은 장소와 열차입니다. 다른 보호 API와 같이 쿠키 인증이고, 변경 요청에는 `X-CSRF-Token`이 필요합니다.

| 경로 | 메서드 | 용도 |
| --- | --- | --- |
| `/saved` | GET | 담은 장소·열차 전체. 장소 이름, 열차 종류·그 역 시각·확실성 포함 |
| `/saved/places` | POST | 장소 담기 `{placeId, priority}` |
| `/saved/places/{savedId}` | PATCH, DELETE | 우선순위 변경 `{priority}` · 빼기 |
| `/saved/trains` | POST | 열차 담기 `{trainNo, action, stationId, priority}` |
| `/saved/trains/{savedId}` | PATCH, DELETE | `{priority?, action?}` 변경 · 빼기 |

`priority`는 `"must"`(꼭) 또는 `"interested"`(관심, 기본값), `action`은 `"ride"`(탑승) 또는 `"observe"`(관찰)입니다.

```jsonc
// POST /api/v1/saved/trains  {"trainNo": "1861", "action": "observe", "stationId": "station_mangsang"}
// GET /api/v1/saved
{
  "places": [
    {"id": "…", "placeId": "station_mukho", "name": "묵호역", "kind": "station", "priority": "must",
     "createdAt": "…", "updatedAt": "…"}
  ],
  "trains": [
    {"id": "…", "trainNo": "1861", "action": "observe", "stationId": "station_mangsang", "stationName": "망상역",
     "priority": "interested", "available": true,       // false: 지금 시간표에 없는 열차(아래 칸은 null)
     "trainType": null, "line": "동해선", "direction": "D", "origin": "동대구", "destination": "강릉",
     "time": "10:42", "arr": null, "dep": null, "passing": true,
     "timeBasis": "묵호 10:37 출발과 정동진 10:57 도착 사이를 거리 비율(0.26)로 어림",
     "certainty": "estimated", "createdAt": "…", "updatedAt": "…"}
  ],
  "basis": "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준"
}
```

서버가 지키는 규칙:

| 상황 | 응답 |
| --- | --- |
| 통과역에서 `ride`(그 역에 서지 않는 열차 포함) | 422 `"이 역은 열차가 서지 않아 관찰만 할 수 있어요"` |
| 열차가 그 역을 지나지 않음, 없는 열차번호 | 422 `"이 열차는 이 역을 지나지 않아요"` |
| 없는 장소 id, 역이 아닌 `stationId` | 422 |
| 같은 항목을 다시 담음 | 200, 기존 항목을 그대로 돌려줌(새로 만들지 않고 우선순위도 바꾸지 않음) |
| 장소 50개 또는 열차 50개를 넘김 | 409 |
| 남의 항목 수정·삭제 | 404 |
| 열차 `action`을 바꿨더니 이미 담은 항목과 같아짐 | 409 |

열차는 열차번호·행동·역이 모두 같아야 같은 항목입니다. 같은 열차를 묵호에서 관찰하고 동해에서 타는 것은 두 항목입니다.

니어리와의 연결: 로그인 사용자의 담은 장소는 요청의 `context.pins`와 합쳐져 니어리의 핀이 됩니다. 그래서 이미 담은 장소는 제안에 다시 나오지 않습니다.

## 챗봇 응답의 `action` — "일정 짜줘"

`/chat/messages`와 `/conversations/{id}/messages`의 챗봇 응답에 `action`이 추가됐습니다(기본 `null`).
사용자가 "일정 짜줘", "계획 세워줘", "이대로 하루 만들어줘"처럼 하루 일정을 요청하면 `intent`가 `plan_request`가 되고,
니어리는 일정을 직접 짜지 않고 일정 화면으로 넘긴다는 안내만 합니다(이 답은 서버가 씁니다).

`action.payload`는 `POST /planning-requests`의 `conditions`와 같은 형식이라 그대로 넘길 수 있습니다.

```jsonc
// 요청: {"message": "일정 짜줘", "context": {"date": "2026-11-21"}}  (묵호역을 must로, 열차 1861을 동해역 탑승으로 담아 둔 사용자)
{
  "reply": "11월 21일 하루 일정은 일정 화면에서 짜요. 담아 둔 곳(묵호역)과 열차 1편을 그대로 일정 화면으로 넘겨 드릴게요. 순서와 시간은 거기서 직접 고를 수 있어요.",
  "intent": "plan_request",
  "action": {
    "type": "plan_request",
    "payload": {
      "travelDate": "2026-11-21", "endDate": null,
      "startTime": null, "endTime": null,               // 대화에서 말했을 때만
      "departurePlaceId": null, "arrivalPlaceId": null,  // 대화에서 말했을 때만
      "requiredPlaceIds": ["station_mukho"],             // must로 담은 장소 + 요청의 context.pins
      "selectedTrainIds": ["1861"],                      // 담은 열차의 열차번호
      "selectedTrains": [{"trainNo": "1861", "action": "ride", "stationId": "station_donghae"}],
      "preferredTrainTypes": [], "interests": [], "maxWalkingMinutes": null
    }
  },
  "chips": ["묵호역 열차 리듬", "묵호역 근처"],
  "proposals": [], "sources": [], "tool_used": null, "mode": "mock"
}
```

- `action`이 있으면 프론트는 `payload`를 들고 일정 화면을 엽니다. 열차의 탑승/관찰 표현은 협의 중이라 `selectedTrainIds`와 `selectedTrains`를 둘 다 채웁니다.
- `interested`로 담은 장소는 `requiredPlaceIds`에 넣지 않습니다(일정 화면이 `/saved`에서 읽습니다).
- 조건이 모자라면 `action`은 `null`이고 니어리가 **하나만** 묻습니다. 담은 장소·열차와 핀이 하나도 없으면 먼저 담아 달라고 하고,
  날짜가 없으면 날짜를 묻고 칩("오늘로 일정 짜줘" 등)을 줍니다. 칩을 누르면 `action`이 담겨 돌아옵니다.
- 메시지에 날짜가 있으면(`2026-11-21`, `11월 21일`, 오늘·내일·모레) 화면의 날짜보다 우선합니다.
- 일정에 대한 질문("일정 화면은 뭐 하는 곳이야?")이나 장소 추천 요청은 `plan_request`가 아닙니다.

## 저장 구조

13개 테이블: `users`, `auth_sessions`, `places`, `knowledge_documents`, `knowledge_chunks`,
`timetable_snapshots`, `train_services`, `train_stops`, `conversations`, `messages`,
`planning_requests`, `itineraries`, `itinerary_revisions`.

사용자·소유권·외래키·검색 기준은 컬럼으로 관리합니다. 생성 결과와 수정 이력의 중첩 DTO,
원천 시간표 세부 필드는 JSONB로 저장합니다. JSONB는 PostgreSQL 컬럼이며 Git의 JSON 파일이 아닙니다.
지식 원문은 `knowledge_documents.body`, 임베딩은 `knowledge_chunks.embedding vector(1536)`에 저장합니다.

`0001_service_storage`는 테이블과 vector 확장, `0002_reference_content`는 기존 PR의 최초 자료를 적재합니다.
`market_aliases`는 북평민속시장 문서의 별칭(재래시장, 장터, 전통시장)을 더합니다. 적용 뒤 `scripts/ingest.py`를 실행하면 그 문서만 다시 임베딩됩니다.
`train_types`는 열차 종류를 여섯 가지(`KTX-이음`, `ITX-마음`, `ITX-새마을`, `새마을호`, `누리로`, `무궁화호`)로 제한하는 DB 제약과, 종류를 어디서 확인했는지 적는 `train_type_source` 칸을 추가하고, 확인한 63편의 종류를 넣습니다(출처: 레일블루 화면에서 수동 확인, 2026-11-20). 종류는 수집 데이터에 없어 사람이 확인한 것만 넣고, 확인하지 않은 열차는 비워 둡니다. 여행 조건의 `preferredTrainTypes`도 같은 여섯 값만 받습니다(그 밖은 422).
시간표를 다시 수집·적재해도 열차번호 기준으로 종류와 출처가 유지됩니다. 수집 기록의 1814(동해→부전 17:20)는 대회일 표기에 맞춰 1804로 저장하며, 이 번호 바꿈도 적재할 때마다 적용됩니다(`ingestion/service.py`의 `NUMBER_OVERRIDES`).
`timetable_times`는 역 시각을 시간표에 더 가깝게 고칩니다. 수집한 운행 기록은 지연이 섞인 실제 시각이라, 열차의 시발역·종착역 시각은 12/19 운행계획의 계획 시각을 쓰고(`time_basis`에 표시), 그 밖에는 최근 토요일 3주 기록 중 가장 이른 시각을 씁니다(도착·출발 모두). 값은 `scripts/fetch_timetable.py`가 원본 응답에서 만들고, 시드와 `alembic/seed/timetable_times_after.sql`에 같은 값이 들어 있습니다. 지식 문서 "열차 종류 구분"의 표기도 "무궁화호"로 맞춥니다("무궁화"로도 검색됨).
`knowledge_enrichment`는 촛대바위·논골담길·망상해수욕장·도째비골 스카이밸리 문서를 보강하고 출처 URL을 `source_url`에 적습니다(한 줄에 하나). 적용 뒤 `scripts/ingest.py`를 실행하면 이 네 문서만 다시 임베딩됩니다.
`saved_items`는 담기 테이블 두 개(`saved_places`, `saved_trains`)를 만듭니다.
초기 seed SQL은 새 DB 재현용 이력입니다. 서버 요청 중에 읽지 않으며 새 데이터를 넣을 때마다 Git에 갱신하지 않습니다.
모델 변경 후 `alembic revision --autogenerate -m "..."`로 새 이력을 만들고 검토 후 적용합니다.
이미 적용한 마이그레이션은 수정하지 않습니다.

## 데이터 수집·임베딩

```bash
# 공공데이터 운행 기록 수집 후 PostgreSQL 적재; 원본 응답은 data/raw/에 임시 캐시
.venv/Scripts/python.exe scripts/fetch_timetable.py
# 원본 캐시를 재사용하지 않을 때
.venv/Scripts/python.exe scripts/fetch_timetable.py --refresh
# PostgreSQL 원문의 변경 여부만 확인(유료 API 호출 없음)
docker compose -p neareast-dev exec backend python scripts/ingest.py --dry-run
# 변경된 문서만 OpenAI 임베딩 후 DB에 적재
docker compose -p neareast-dev exec backend python scripts/ingest.py
```

**배포할 때 한 번**: `alembic upgrade head` 다음에 `docker compose -p neareast-dev exec backend python scripts/ingest.py`를 실행해야 벡터 검색이 켜집니다.
실행하지 않아도 서버는 동작하며, 그동안은 화면 맥락과 키워드 검색만으로 답합니다.
이 명령은 **바뀐 문서만** 다시 임베딩합니다. 문서의 제목·별칭(키워드)·본문, 관련 장소의 이름·별칭 가운데 하나라도 바뀌었거나 임베딩 모델을 바꾼 문서가 대상이고,
나머지는 건너뜁니다. 그래서 문서나 장소를 고친 뒤에는 같은 명령을 다시 실행하면 됩니다. 끝나면 몇 개를 몇 차원으로 넣었는지와 사용한 토큰 수를 출력합니다.

철도 수집은 현재 로컬 Python 스크립트 기준이며 DB는 Docker의 PostgreSQL에 적재합니다.
컨테이너에서 수집하려면 해당 작업에 키와 쓰기 가능한 원본 캐시 경로를 별도로 제공해야 합니다.
`NEAREAST_DATA_GO_KR_KEY`는 한국철도공사 개발계정 디코딩 키입니다.
OpenAI는 `NEAREAST_OPENAI_API_KEY` 또는 `OPENAI_API_KEY`를 사용하며, 앞의 값이 비어 있지 않으면 우선합니다.
실제 LLM 응답은 `NEAREAST_CHAT_MOCK=false`, 기본 테스트 모드는 `true`입니다.
`text-embedding-3-large`를 1536차원으로 줄여 사용합니다(문서와 질문 모두). 원문/모델/벡터 유무를 비교해 필요한 문서만 재임베딩합니다.
임베딩을 바꾼 문서는 재적재 전까지 오래된 벡터 검색에서 제외됩니다.
현재 작은 문서 20개를 문서 단위로 임베딩합니다. 임베딩하는 글은 제목, 별칭(문서 키워드와 관련 장소의 별칭), 관련 장소 이름, 본문입니다.

두 장소 사이 도보 시간("동해역에서 북평민속시장까지 걸어가면 얼마나 걸려?")은 서버가 계산해서 답합니다(응답의 `tool_used`가 `walk_time`).
두 장소 모두 좌표가 있으면 직선거리 × 1.3, 시속 4.5km로 어림해 "약 N분"으로 답하고 어림이라고 밝힙니다. 좌표가 없으면 "아직 위치 정보가 없어 도보 시간을 계산할 수 없어요"라고 답합니다.
장소 좌표가 `places` 테이블에 들어오면 코드 수정 없이 켜집니다.

벡터 검색은 검색의 세 번째 단계입니다(① 화면 맥락의 id → ② 키워드 → ③ 벡터). 앞 단계에서 문서를 3개 못 채웠을 때만 질문을 임베딩해 채웁니다.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NEAREAST_CHAT_VECTOR_SEARCH` | `true` | 벡터 검색 사용 여부. `false`면 ①②만 씁니다 |
| `NEAREAST_VECTOR_MIN_SIMILARITY` | `0.27` | 질문과의 유사도가 이보다 낮은 문서는 근거로 쓰지 않습니다(0~1). 임베딩 모델을 바꾸면 다시 정해야 합니다 |
| `NEAREAST_EMBEDDING_TIMEOUT_SECONDS` | `2` | 질문 임베딩을 기다리는 시간. 넘기면 벡터 검색만 건너뜁니다 |

벡터 검색은 언제든 빠질 수 있고, 그래도 답은 나갑니다(오류가 되지 않습니다): 질문 임베딩이 실패하거나 늦을 때,
저장된 벡터의 모델이 `NEAREAST_EMBEDDING_MODEL`과 다를 때(경고 로그), 벡터가 아직 없을 때입니다. 같은 질문은 프로세스 안에서 한 번만 임베딩합니다.
대규모 문서 분할·DB 내부 유사도 검색·자연어 여행 조건 수집은 별도 고도화 대상입니다.

## 검증

```bash
.venv/Scripts/python.exe -m unittest discover -s tests
.venv/Scripts/python.exe -m alembic check
# 실제 OpenAI 호출: 테스트 계정 생성 후 정리하며 API 토큰을 사용합니다.
.venv/Scripts/python.exe scripts/verify_live.py
```

실제 PostgreSQL 통합 테스트는 PowerShell에서 다음과 같이 실행합니다. 임의 이름의 전용 테스트 스키마만 생성·제거합니다.

```powershell
$env:NEAREAST_RUN_POSTGRES_TESTS='1'
.venv/Scripts/python.exe -m unittest discover -s tests -p test_postgres_storage.py -v
Remove-Item Env:NEAREAST_RUN_POSTGRES_TESTS
```

현재 데이터 한계·검증 결과는 [검증 기록](docs/verification.md),
프론트 연동과 기존 PR 영향은 [백엔드 연동 계약](docs/backend-integration.md),
코드 기준은 [개발 기준](docs/development.md)을 참고합니다.

## 배포 설정

HTTPS에서는 `NEAREAST_COOKIE_SECURE=true`. 프론트는 `credentials: "include"`를 사용하고
상태 변경 요청에 `X-CSRF-Token`을 보냅니다. CORS에는 정확한 프론트 origin만 등록합니다.
동일 사이트 배포 또는 프론트 도메인의 `/api` reverse proxy를 사용합니다.
서로 무관한 도메인에 배포하면 현재 SameSite=Lax 쿠키 계약을 별도로 조정해야 합니다.
운영에서는 `NEAREAST_DOCS_ENABLED=false` 및 비밀관리 환경변수 사용을 권장합니다.
개발 Compose는 127.0.0.1에만 포트를 노출하므로 외부 공개는 배포 환경에서 별도로 구성합니다.
