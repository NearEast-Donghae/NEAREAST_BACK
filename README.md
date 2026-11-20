# NearEast Backend

FastAPI + PostgreSQL/pgvector 기반 서비스 API입니다. 데이터 수집·정제·문서 임베딩·실험은 형제 프로젝트 `NearEast_Data`에서 수행합니다. 백엔드는 그 프로젝트를 import하지 않습니다.

## 실행

`.env.example`을 `.env`로 복사하고 DB 비밀번호를 설정합니다. 이미 실행 중인 DB가 있으면 기존 Compose 프로젝트 이름과 포트를 유지합니다.

```bash
docker compose up --build -d
docker compose exec backend python scripts/seed_data.py
```

첫 명령은 DB 준비 → Alembic 자동 적용 → API 시작 순서입니다. migration이 실패하면 API를 시작하지 않습니다. 둘째 명령은 루트의 `neareast.seed.gz` 한 파일을 트랜잭션으로 적재합니다. 사용자·대화·여행은 seed에 포함하지 않습니다. 복구에 OpenAI·공공데이터 키나 재임베딩은 필요 없습니다. 실제 챗봇·도보 경로 호출에는 해당 키가 별도로 필요합니다.

개발 Swagger: http://localhost:8012/docs. `.env`의 `COMPOSE_PROJECT_NAME`을 팀에서 정한 값으로 유지합니다. 이전에 `-p neareast-dev`를 사용했으면 같은 이름을 설정해야 포트 충돌·별도 볼륨 생성을 피할 수 있습니다. 과거 migration SQL은 이미 적용된 DB와 새 DB의 스키마 재현을 위해 유지합니다.

```bash
# 선택: 적재 전 파일 형식·체크섬 확인 (실제 DB 제약 검사는 적재 시 수행)
docker compose exec backend python scripts/seed_data.py --dry-run
```

## 실행 코드

- `src/neareast_api/core`: 환경설정·공통 검증.
- `db`: Entity·SQL 조회·공개 seed import/export. 데이터 원본 파일을 서비스 요청 중에 읽지 않습니다.
- `modules`: 인증·사용자·장소·열차·대화·담기·여행·플래너.
- `alembic`: 스키마 및 이미 배포된 migration 이력. 현재 head `0006_train_type_contract`.
- `scripts/migrate.py`, `scripts/seed_data.py`: 자동 migration과 공개 데이터 복구 명령.
- `neareast.seed.gz`: 장소·지식·벡터·플래너 원본·출처·열차 스냅샷을 담은 단일 배포 파일. 시간표는 실시간 자료가 아닙니다.
- `tests`: API·인증·저장·검색·플래너 및 PostgreSQL 검증.

질문 임베딩과 LLM 호출은 RAG 실행에 필요하므로 남아 있습니다. 문서 일괄 임베딩과 수집 도구는 `NearEast_Data`에 있습니다. 기본적으로 파일 로그를 만들지 않고 표준 logging을 사용합니다. `NEAREAST_LLM_USAGE_LOG`는 오프라인 평가에서 파일 기록이 필요할 때만 명시합니다.

## API 연결

모든 서비스 경로 앞에는 `/api/v1`이 붙습니다. 지도·장소·열차 조회는 공개이고 대화·담기·여행·본인 정보는 로그인이 필요합니다.
로그인은 HttpOnly 쿠키이며, 변경 요청에는 로그인 또는 `GET /users/me`가 반환한 `csrfToken`을 `X-CSRF-Token`으로 보냅니다.

| 기능 | 주요 경로 |
| --- | --- |
| 가입·로그인·로그아웃 | `POST /auth/signup`, `/auth/login`, `/auth/logout` |
| 본인 조회·닉네임 변경·탈퇴 | `GET/PATCH/DELETE /users/me` |
| 비밀번호 변경 | `PUT /users/me/password` |
| 장소 목록·상세 | `GET /places`, `/places/{place_id}` |
| 역 열차·구간 검색·열차 상세 | `GET /stations/{station_id}/trains`, `/trains?from=...&to=...`, `/trains/{train_no}` |
| 장소·열차 담기 | `/saved`, `/saved/places`, `/saved/trains` |
| 세션별 대화 | `/conversations`, `/conversations/{conversationId}/messages` |
| 여행 저장·수정·조회·삭제·이력 | `/itineraries`, `/itineraries/{itineraryId}`, `/itineraries/{itineraryId}/revisions` |
| 여행 조건·생성 | `/planning-requests`, `/planning-requests/{requestId}/generations` |
| 플래너 온보딩(도착역·출발역·동행 선택지) | `GET /onboarding/options`, `/onboarding/origins?destination=...` |

Vite가 proxy의 Host를 바꿔도 브라우저 Origin은 남습니다. 개발 프론트 주소를 `NEAREAST_CORS_ALLOW_ORIGINS`에 명시합니다. 배포에서는 실제 HTTPS 주소만 허용하고 `NEAREAST_COOKIE_SECURE=true`를 사용합니다.

장소 응답의 `category`는 관광공사 자료의 분류를 그대로 옮긴 값입니다(`attraction`, `culture`, `leisure`, `shopping`, `restaurant`, `lodging`, `festival`, `course`, `market`). 자료에 분류가 없는 장소는 `null`이며 추측으로 채우지 않습니다. 챗봇의 "열차 공백에 갈 만한 곳"은 `attraction`·`culture`·`market`만 대상으로 하고, 식당은 공백이 점심(11~14시)·저녁(17~20시)과 30분 이상 겹칠 때만 식사 제안으로 따로 나옵니다. 근처 장소와 두 장소 사이 도보 시간은 같은 함수(직선거리 × 1.3, 시속 4.5km)로 계산하므로 같은 구간에 다른 숫자가 나오지 않습니다. 좌표가 없는 장소에서만 예시 데이터를 쓰고 답에 그렇게 밝힙니다.

플래너는 팀원 구현 기준 단일 날짜·도보 일정·방문지 최대 5곳입니다. 열차 연계나 여러 날 일정은 아직 지원하지 않습니다. 좌표만 있다고 방문 가능으로 추정하지 않습니다.

[최신 프론트 연결 점검](docs/frontend-contract.md), [seed·팀원 실행·PR #19 반영 범위](docs/data-release.md)를 참고합니다. `NearEast_Data`는 현재 로컬 별도 프로젝트이며 아직 원격에 게시하지 않았습니다. 백엔드 실행에는 필요하지 않습니다.

## 검증

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m ruff check src scripts tests
```

실제 PostgreSQL 테스트는 `NEAREAST_RUN_POSTGRES_TESTS=1`로 실행합니다. 별도 테스트 스키마를 만들고 정리합니다. 프론트나 데이터 수집 프로젝트가 없어도 백엔드 기본 테스트가 실행됩니다.

챗봇 평가는 설정된 DB(실제 seed)와 실제 모델로 시나리오 전체를 돌립니다. OpenAI 키가 필요하고 비용이 듭니다(nano 기준 1회 약 0.07달러).

```bash
python scripts/eval_chat.py --quiet --out eval.json          # 전체
python scripts/eval_chat.py --group V --vector-search off     # 묶음만, 벡터 검색 끄고
```

`NEAREAST_EMBEDDING_MODEL`이 seed의 벡터를 만든 모델(`text-embedding-3-large`)과 다르면 벡터 검색은 조용히 건너뜁니다. 평가 출력의 "임베딩 호출 수"가 0이면 이 값을 확인합니다.
