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
| `/chat/persona`, `/chat/messages` | GET, POST | 기존 PR 챗봇 계약 유지 |

대화 저장에는 `/conversations/{id}/messages`를 사용합니다. 기존 `/chat/messages`는 요청에 담긴
history를 사용하는 호환 API이며 자동 저장하지 않습니다. 다른 사용자의 리소스는 404를 반환합니다.
목록에는 `limit`, `offset`을 지원합니다. 메시지는 시간순, 여행·대화방 목록은 최신순입니다.
여행·여행 조건 수정에는 `expectedRevision`을 보내며 이전 버전이면 409를 반환합니다.

## 저장 구조

13개 테이블: `users`, `auth_sessions`, `places`, `knowledge_documents`, `knowledge_chunks`,
`timetable_snapshots`, `train_services`, `train_stops`, `conversations`, `messages`,
`planning_requests`, `itineraries`, `itinerary_revisions`.

사용자·소유권·외래키·검색 기준은 컬럼으로 관리합니다. 생성 결과와 수정 이력의 중첩 DTO,
원천 시간표 세부 필드는 JSONB로 저장합니다. JSONB는 PostgreSQL 컬럼이며 Git의 JSON 파일이 아닙니다.
지식 원문은 `knowledge_documents.body`, 임베딩은 `knowledge_chunks.embedding vector(1536)`에 저장합니다.

`0001_service_storage`는 테이블과 vector 확장, `0002_reference_content`는 기존 PR의 최초 자료를 적재합니다.
`0003_place_coordinates`는 장소 좌표를 채웁니다(목록에 없는 장소는 NULL 그대로). 이미 적용한 뒤에 좌표를 더하려면 새 마이그레이션을 만듭니다.
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

철도 수집은 현재 로컬 Python 스크립트 기준이며 DB는 Docker의 PostgreSQL에 적재합니다.
컨테이너에서 수집하려면 해당 작업에 키와 쓰기 가능한 원본 캐시 경로를 별도로 제공해야 합니다.
`NEAREAST_DATA_GO_KR_KEY`는 한국철도공사 개발계정 디코딩 키입니다.
OpenAI는 `NEAREAST_OPENAI_API_KEY` 또는 `OPENAI_API_KEY`를 사용하며, 앞의 값이 비어 있지 않으면 우선합니다.
실제 LLM 응답은 `NEAREAST_CHAT_MOCK=false`, 기본 테스트 모드는 `true`입니다.
`text-embedding-3-small` 1536차원을 사용합니다. 원문/모델/벡터 유무를 비교해 필요한 문서만 재임베딩합니다.
임베딩을 바꾼 문서는 재적재 전까지 오래된 벡터 검색에서 제외됩니다.
현재 작은 문서 20개를 제목+본문 단위로 임베딩하며, 기존 챗봇의 검색 순서와 점수 계산은 유지합니다.
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
