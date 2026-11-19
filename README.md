# NearEast backend — initial scaffold

Python 3.12 / FastAPI / PostgreSQL 17 로컬 개발 시작점입니다.
Compose 명령 하나로 백엔드와 DB를 띄우고 Swagger에서 연결 상태를 확인합니다.
업무 테이블·마이그레이션 이력은 없으며, 상태 확인 API 두 개와 챗봇 API(mock 모드)를 제공합니다.
SQLAlchemy 공통 Base와 Alembic 설정은 준비되어 있습니다. 실행 시 DB 구조를 자동 변경하지 않습니다.
앱 자체는 DB 연결 없이 시작할 수 있지만 DB가 응답해야 readiness가 성공합니다.
`/`의 404는 정상이며 Swagger 주소는 `/docs`입니다.

이 폴더만 독립 Git 저장소로 사용합니다. 상위 폴더의 기획서는 포함하지 않습니다.
구체적인 서비스 기능과 DB 모델은 아직 구현하지 않았습니다.

## Compose로 실행 — Git Bash

Docker Desktop의 Linux 컨테이너 엔진을 켜고 이 README가 있는 폴더에서 실행합니다.
현재 작업 폴더에는 개발용 `.env`도 준비했습니다. 다른 곳에 새로 clone했다면
최초 한 번 `cp .env.example .env`를 실행합니다. 기존 `.env`는 덮어쓰지 않습니다.

```bash
docker compose up --build -d
docker compose ps
```

첫 실행에는 이미지·패키지 다운로드 시간이 필요합니다. `-d`는 백그라운드 시작이며,
명령이 끝난 순간 모든 준비가 끝났다는 뜻은 아닙니다. `ps`에서 두 서비스가
`healthy`가 되는지 확인합니다. 준비 완료까지 명령에서 기다리려면 `--wait`를 추가합니다.

- Swagger: http://localhost:8012/docs
- API 생존 확인: http://localhost:8012/health/live → HTTP 200, `{"status":"ok"}`
- API + DB 연결 확인: http://localhost:8012/health/ready → HTTP 200, `{"status":"ok","database":"ok"}`

Swagger의 **Health → GET /health/ready → Try it out → Execute**로도 확인할 수 있습니다.
DB 연결 실패·인증 실패·쿼리 실패 시 readiness는 HTTP 503과
`{"status":"error","database":"unavailable"}`을 반환합니다. liveness는 DB 장애와 무관합니다.
나중에 Kubernetes에 올릴 때도 DB 장애 때문에 앱을 반복 재시작시키지 않도록 둘을 구분합니다.

```bash
curl -f http://localhost:8012/health/ready
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1 AS ok;"'
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt public.*"'
```

마지막 명령은 새 볼륨에서 사용자 테이블이 없음을 보여줍니다. PostgreSQL 자체의 시스템
카탈로그와 기본 스키마는 존재합니다. 앱은 `SELECT 1`만 실행하며 테이블을 자동 생성하지 않습니다.

로그 확인은 `docker compose logs --tail=100 backend db`, 종료는 `docker compose down`입니다.
DB는 named volume에 보관되므로 다시 실행해도 데이터가 유지됩니다. `.env`의 DB 이름·계정·비밀번호는
**빈 볼륨의 최초 초기화에만** 반영됩니다. 나중에 값을 바꾸더라도 기존 DB 계정이 자동 변경되지는 않습니다.
`down -v`는 DB 데이터를 삭제하므로 일반 종료 명령으로 사용하지 않습니다.

포트 충돌이면 `.env`의 `NEAREAST_HTTP_PORT` 또는 `NEAREAST_DB_PORT`를 변경한 후 다시 실행합니다.
이전 수동 실행 컨테이너가 같은 포트를 쓰는지도 확인합니다.

## 구조

```text
NearEast_Backend/
├── src/neareast_api/
│   ├── main.py              # 앱 생성, 설정 적용, 라우터 조립
│   ├── api/router.py        # /api/v1 아래 기능 라우터를 명시적으로 등록
│   ├── api/health.py        # 생존·DB 연결 확인
│   ├── core/config.py       # 실행 환경 설정과 검증
│   ├── core/database.py     # SELECT 1과 연결 종료만 수행
│   ├── db/base.py           # 모든 기능 모델이 공유할 ORM Base
│   ├── db/metadata.py       # 기능별 모델의 명시적 등록 위치
│   ├── db/url.py            # 공통 설정으로 DB 접속 URL 구성
│   └── modules/             # 합의된 기능을 Python 패키지로 추가할 위치
│       └── chat/            # 지도 맥락을 아는 챗봇 (router/schemas/service/retrieval/tools)
├── knowledge/              # 챗봇 지식 문서 (파일명 = 문서 id = 지도 마커 id)
├── data/places.json        # 지도 마커와 챗봇이 공유하는 장소 id 목록
├── scripts/ingest.py       # knowledge/ → data/embeddings.json 임베딩 캐시
├── alembic/                # 마이그레이션 환경과 비어 있는 versions/
├── alembic.ini             # 비밀번호 없는 Alembic 설정
├── tests/                  # 앱·Health·마이그레이션·챗봇 계약 검증
│   └── chat_scenarios/     # 챗봇 품질 시나리오 (프롬프트 수정 때마다 재실행)
├── docs/development.md      # 모듈 경계와 HTTP API 작성 기준
├── pyproject.toml           # 의존성, 패키지 메타데이터
├── .env.example
├── .dockerignore
├── compose.yaml            # 개발용 backend + db, 상태 확인, DB 볼륨
└── Dockerfile              # 이 FastAPI 앱의 실행 이미지
```

## 챗봇 API — 니어리

챗봇 이름은 **니어리**입니다. 해요체로 친근하게 말하지만, 말투가 규칙(문서 근거만, 시각은 도구로만,
계획 직접 수정 금지, 안전 대안)보다 앞서지 않습니다.

### `GET /api/v1/chat/persona`

채팅 화면이 쓸 이름·인사말·로딩 문구입니다. 첫 화면 인사와 응답 대기 중 문구에 그대로 씁니다.

```jsonc
{
  "name": "니어리",
  "greeting": "안녕하세요, 동해 철도 여행을 함께 짜는 니어리예요.",
  "loading": {
    "default": "니어리가 생각하고 있어요…",
    "train": "열차 시간표를 살펴보고 있어요…",
    "place": "{place} 주변을 둘러보고 있어요…",   // {place}에 장소 이름을 넣어 표시
    "knowledge": "자료를 찾아보고 있어요…",
    "safety": "안전한 방법을 확인하고 있어요.",
    "slow": "조금 오래 걸리고 있어요. 잠시만 기다려 주세요."
  }
}
```

**프론트 안내**: 요청을 보낸 뒤 바로 `loading`의 상황별 문구를 보여 주고, **5초가 넘으면 `slow` 문구로 바꿔** 주세요.
보통 응답은 2~3초, 도구를 쓰는 답은 4초 안팎입니다. 모델 호출 1회는 8초에 끊고 한 번 다시 시도합니다.
한 메시지에 호출이 최대 2회라서 **최악의 경우 약 32초**까지 걸릴 수 있고, 그래도 실패하면 내장 응답기가
대신 답합니다(`mode: "fallback"`). 프론트 타임아웃은 35초 이상으로 잡아 주세요.

### `POST /api/v1/chat/messages`

화면 맥락(열어 둔 마커, 내 핀, 날짜)을 함께 보내면 답변·다음 행동 칩·제안을 돌려줍니다.
기본은 **mock 모드**(`NEAREAST_CHAT_MOCK=true`)라 API 키 없이 내장 예시 데이터로 답합니다.
열차 시각이 담긴 답에는 `(예시 데이터)`가 붙습니다. `false`면 OpenAI로 답하며, 키가 없거나 호출이 실패하면
내장 응답기로 대신 답합니다(`mode: "fallback"`). 503은 장소·문서 파일을 읽을 수 없을 때만 납니다.

```jsonc
// 요청
{
  "message": "언제가 좋아?",
  "context": {
    "date": "2026-11-21",           // null 가능
    "pins": ["station_donghae"],    // 사용자 확정 핀 id
    "focused": "station_donghae",   // 지금 열어 둔 마커 id, null 가능
    "depth": "light",               // reference|light|full (기본 light)
    "dismissed": ["poi_nongoldam"]  // 사용자가 거절한 제안 target id (기본 [])
  },
  "history": [{"role": "user", "content": "..."}]  // 전체 대화를 보내도 됨. 서버는 최근 메시지 10개만 사용
}
// 응답
{
  "reply": "...",
  "intent": "train_lookup",          // knowledge|train_lookup|safety|proposal|smalltalk
  "chips": ["동해역 열차 목록", "동해역 근처"],  // 2~3개, 그대로 다음 message로 보내면 대화가 이어짐
  "proposals": [{"type": "add", "target": "poi_bukpyeong_market", "reason": "...",
                 "needs_confirm": true, "certainty": "timetable"}],
  "sources": [],                     // 답의 근거 문서 id
  "tool_used": "station_rhythm",     // null 가능
  "mode": "llm"                      // llm | fallback | mock
}
```

- `mode`: `llm`은 모델이 답한 것, `fallback`은 모델 호출이 실패해 내장 응답기가 대신 답한 것,
  `mock`은 `NEAREAST_CHAT_MOCK=true`입니다. 모델이 실패해도 챗봇은 503 대신 `fallback`으로 답합니다.

- `context`, `history`는 생략할 수 있습니다. 정의되지 않은 필드는 오타로 보고 **422**로 거부합니다.
- `history`는 전체 대화를 보내도 됩니다(200개까지). 서버는 최근 10개, 합계 6000자까지만 씁니다.
- 모르는 `focused`/`pins` id는 오류가 아니라 **200**입니다. 그 id는 무시하고, 한 대화에 한 번
  "지금 열어 둔 장소는 아직 모르는 곳이라 빼고 답할게요."처럼 알립니다(id 자체는 서버 로그에만 남음).
- 같은 주소에서 1분에 60번을 넘게 보내면 **429**와 `Retry-After`(초)를 돌려줍니다.
- 상태 코드: 200 정상 / 422 입력 오류 / 429 요청 과다 / 503 장소·문서 파일을 읽을 수 없음.

#### 서버가 강제하는 규칙

모델(또는 내장 응답기)이 무엇을 내놓든 응답은 아래를 지킵니다.

| 규칙 | 동작 |
| --- | --- |
| 제안 | 항상 `needs_confirm: true`. `dismissed`·이미 핀인 장소·목록에 없는 장소는 제거. `depth: "reference"`면 제안 없음. `certainty`(`timetable`\|`estimated`\|`planned`\|`null`)는 도구 결과에서 옮김 |
| 열차 시각·편수 | 답에 나온 시각("10:20", "오후 4시", "10시대")과 편수는 도구 결과나 인용한 문서에 실제로 있어야 함. 없으면 안내 문장으로 바꿈. "도보 7분", "1시간 거리" 같은 소요 시간은 그대로 둠 |
| 근거 없는 답 | 근거 문서도 도구도 없는 지식 답변은 "아직 확인된 정보가 없어요."로 바꿈. "빈 시간은 그대로"처럼 비워 두겠다는 선택은 해당 없음 |
| 안전 | 질문에 선로·건널목·차단기·철길이 있을 때만: 답에 "권하지 않아요"가 없으면 표준 문장으로 바꾸고, 공개 장소 대안이 없으면 덧붙임 |
| 통과역 | 답이 승하차 불가 역에 관한 것일 때만 "관찰만" 안내와 `safety_passing_station` 근거를 추가 |
| 예시 데이터 | 예시(mock) 도구 결과를 쓴 답에는 `(예시 데이터)` 표시 |
| 도구 대상 | 화면(`focused`·`pins`), 이번 질문, 직전 대화에서 사용자가 말한 장소만. 줄임말("묵호")은 `places.json`의 `aliases`로 알아들음 |
| 칩 | 2~3개. 근처 데이터가 없는 장소의 "근처" 칩은 제거하고, 모자라면 기본 칩으로 채움 |
| 답 형식 | 400자 안에서 문장 단위로 자름. 마크다운·목록 기호 제거. 이어지는 대화에서는 인사말 반복 제거 |
| 근거 문서 | 프롬프트에 실제로 넣은 문서 id만 `sources`에 남김 |

규칙이 답을 바꾸면 서버 로그에 어떤 규칙이 무엇을 바꿨는지 원문과 함께 남습니다.

- 장소 id는 **`data/places.json`** 이 기준이며 지도 마커 id와 같아야 합니다. 좌표는 아직 비어 있습니다.
  `aliases`는 질문에서 그 장소로 알아듣는 다른 표현입니다.
- 열차 시각과 도보 시간은 아직 예시 데이터(`modules/chat/tools.py`)이며 시간표 스냅샷으로 교체됩니다.

### LLM 모드와 평가

메시지 1건에 LLM 호출은 1회이며, 모델이 도구(열차 리듬·열차 목록·주변 장소)를 요청한 경우에만 2회입니다.
고정 시스템 프롬프트를 맨 앞에 두어 OpenAI 자동 프롬프트 캐싱이 적용되게 했습니다.
모델 출력은 위 "서버가 강제하는 규칙"으로 다시 검사합니다. 모델 호출이 실패하면(키 없음, 타임아웃, 429,
출력 잘림, 예기치 못한 오류) 내장 응답기가 같은 규칙으로 대신 답하고 `mode`가 `fallback`이 됩니다.
실패 이유는 로그에 상태 코드·오류 코드·요청 id·모델명으로 남습니다.

`tests/chat_scenarios/scenarios.json`의 품질 시나리오를 실제 모델로 돌립니다(**토큰을 씁니다**).

```bash
python scripts/eval_chat.py --quiet                      # 전체, 그룹별 통과율·토큰·추정 비용·캐시 비율
python scripts/eval_chat.py --model gpt-5.4-nano --reasoning-effort low --quiet  # 다른 설정과 비교
python scripts/eval_chat.py --rerun-failed               # 그 모델·설정에서 지난번 실패한 것만
```

결과에는 그룹별 통과율, API 호출 수, 토큰(캐시 비율), 추정 비용, 응답 시간(평균·최대)이 나옵니다.
시나리오의 `_note`는 기준을 완화한 이유이며, `llm_only`는 mock 모드 단위 테스트에서 제외됩니다.

### 지식 문서 — `knowledge/<id>.md`

챗봇의 지식 답변은 이 문서들만 근거로 하며, 응답 `sources`에 문서 id가 담깁니다.
장소 문서의 파일명·`id`는 `places.json`의 id와 같아야 합니다(테스트가 검사).
확인된 사실만 적고, 확인이 필요한 내용은 본문 대신 `todo:`에 남깁니다.

```markdown
---
id: station_donghae
title: 동해역
kind: station                 # station | poi | heritage | safety | rail
places: [station_donghae]     # 이 문서가 연결되는 장소 id (화면 맥락 검색용)
keywords: [동해역, 북평역]      # 질문에 이 단어가 있으면 찾음 (고유명사 위주)
---
첫 문단은 짧은 답변에 그대로 인용됩니다.

둘째 문단부터는 보충 설명입니다.
```

검색 순서: ① 화면 맥락 id(`focused`, `pins`)로 직접 조회 → ② 키워드 → ③ 임베딩 유사도.
③은 LLM 연결 후, `data/embeddings.json`이 있고 모델명이 같을 때만 쓰입니다.
문서를 고친 뒤 임베딩 캐시를 갱신합니다(바뀐 문서만 다시 임베딩):

```bash
python scripts/ingest.py --dry-run   # 키 없이 바뀔 문서만 확인
NEAREAST_OPENAI_API_KEY=... python scripts/ingest.py
```

## Docker 밖에서 Python 직접 실행 — 선택 사항

아래 명령은 모두 **이 README가 있는 프로젝트 루트**에서 실행합니다.
Python 3.12가 필요합니다. 다른 Python 버전은 이번 초기 검증 범위에 포함하지 않았습니다.
패키지 최초 설치에는 인터넷 또는 별도 패키지 저장소가 필요합니다.
이 방식에서는 DB만 `docker compose up -d db`로 먼저 실행합니다.
Compose 백엔드도 이미 실행 중이라면 동일한 HTTP 포트를 동시에 사용할 수 없습니다.

### Windows PowerShell

가상환경 활성화나 PowerShell 실행 정책 변경 없이 실행할 수 있습니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m uvicorn neareast_api.main:create_app --factory --env-file .env --reload --host 127.0.0.1 --port 8012
```

### macOS / Linux

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
[ -f .env ] || cp .env.example .env
.venv/bin/python -m uvicorn neareast_api.main:create_app --factory --env-file .env --reload --host 127.0.0.1 --port 8012
```

`http://127.0.0.1:8012/docs`에서 Health API 두 개를 확인할 수 있습니다.
종료는 실행 중인 터미널에서 Ctrl+C입니다. `--reload`는 로컬 개발에만 사용합니다.

## 설정

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `NEAREAST_DOCS_ENABLED` | 로컬 `true`, Dockerfile `false` | 자동 문서와 OpenAPI 노출 여부 |
| `NEAREAST_HTTP_PORT` | `8012` | Compose가 호스트에 공개할 백엔드 포트 |
| `NEAREAST_CORS_ALLOW_ORIGINS` | 없음 | 브라우저에서 API를 부를 프론트 주소(쉼표로 구분, 예: `http://localhost:5173`). 비우면 다른 주소의 브라우저 호출은 막힘 |
| `NEAREAST_DB_HOST` | `127.0.0.1` | Python 직접 실행 시 DB 주소. Compose 내부는 항상 `db` |
| `NEAREAST_DB_PORT` | `5432` | 호스트의 DB 포트 / Python 직접 실행 시 접속 포트. Compose 내부는 항상 `5432` |
| `NEAREAST_DB_NAME` | `.env.example`: `neareast` | 최초 생성 DB 이름 |
| `NEAREAST_DB_USER` | `.env.example`: `neareast` | 개발용 DB 계정 |
| `NEAREAST_DB_PASSWORD` | `.env.example`의 개발용 값 | Compose에서는 필수. 앱 설정에 없으면 readiness 실패 |
| `NEAREAST_CHAT_MOCK` | `true` | 챗봇을 키 없이 내장 예시 데이터로 응답. `false`는 LLM 연결 전까지 503 |
| `NEAREAST_CHAT_RATE_LIMIT_PER_MINUTE` | `60` | 클라이언트 주소 하나가 1분에 보낼 수 있는 챗봇 요청 수. 넘으면 429와 `Retry-After`. `0`은 제한 없음 |
| `NEAREAST_CONTENT_DIR` | `.` | `knowledge/`·`data/`가 있는 폴더(실행 위치 기준). 이미지에서는 `/app` |
| `NEAREAST_OPENAI_API_KEY` | 없음 | LLM 모드에서 필수. **`.env`에만** 두고 커밋·공유하지 않음 |
| `NEAREAST_CHAT_MODEL` | `gpt-5.4-nano` | 답변 생성 모델 (평가 비교 후 선택) |
| `NEAREAST_EMBEDDING_MODEL` | `text-embedding-3-small` | 문서·질의 임베딩 모델 (`scripts/ingest.py`와 같아야 함) |
| `NEAREAST_CHAT_MAX_TOKENS` | `1000` | 호출 1회의 출력 토큰 상한. 추론 토큰도 포함되므로 넉넉히 둠(답 길이는 프롬프트와 서버의 400자 제한으로 유지) |
| `NEAREAST_LLM_TIMEOUT_SECONDS` | `8` | OpenAI 호출 1회 타임아웃. 1회 재시도하고, 실패하면 내장 응답기로 대신 답함. 메시지 1건의 최악 대기는 이 값의 약 4배 |
| `NEAREAST_CHAT_REASONING_EFFORT` | 없음 | 추론 모델(gpt-5 계열)만: `none`·`minimal`·`low`·`medium`·`high`. gpt-4.1 계열에는 두지 않음 |
| `NEAREAST_LLM_USAGE_LOG` | `llm_usage.jsonl` | 호출마다 토큰 사용량(캐시 포함)과 응답 시간을 한 줄씩 기록. Compose에서는 `/tmp/llm_usage.jsonl` |

앱은 생성 시 프로세스 환경변수를 읽고 검증합니다. 잘못된 bool 값은 시작 오류로 처리합니다.
앱 자체가 `.env`를 탐색하거나 로딩하지 않습니다. 로컬에서는 명령의 `--env-file .env`,
Compose에서는 YAML의 `environment` 항목으로 전달합니다. `.env`가 자동으로 컨테이너에 복사되는 것은 아닙니다.
Uvicorn의 `.env` 로딩은 이미 설정된 프로세스 환경변수를 덮어쓰지 않습니다.
Python 직접 실행의 HTTP 포트는 Uvicorn 명령의 `--port`로 지정합니다.
Alembic CLI도 같은 `NEAREAST_DB_*` 설정을 사용하며 프로젝트 `.env`를 명시적으로 읽습니다.
Compose는 백엔드와 DB 포트를 호스트의 `127.0.0.1`에만 공개합니다.
이 계정과 비밀번호는 로컬 개발용이며 운영 설정이 아닙니다. `.env`는 Git과 이미지에서 제외합니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

macOS/Linux에서는 실행 파일 부분을 `.venv/bin/python`으로 바꿉니다.
테스트는 실제 DB나 외부 서비스 연결 없이 실행합니다.
현재는 앱 시작·설정·라우터 조립·DB 성공/실패 응답과 연결 종료를 검증하며,
Alembic은 임시 폴더에서 설정과 SQL 초안 생성을 검증합니다.
DB 테스트의 연결 객체는 대역입니다. 향후 업무 API의 REST 동작이나 실제 DB 배포 통합을
검증한 것으로 간주하지 않습니다.

의존성은 `pyproject.toml`의 버전 범위로 선언했습니다. 잠금 파일은 아직 없으므로
설치 시점에 선택되는 정확한 버전이 달라질 수 있습니다.
공유 배포 환경을 정할 때 의존성을 잠그고 해당 버전으로 테스트를 다시 실행합니다.


## Docker와 배포 범위

Compose는 프로젝트 루트를 백엔드 빌드 컨텍스트로 사용하고, DB는 공식 PostgreSQL 이미지를 사용합니다.
PostgreSQL은 17 계열과 `/var/lib/postgresql/data` 볼륨 경로를 함께 지정했습니다.
Compose는 DB가 준비된 뒤 백엔드를 시작합니다. 실행 중 DB 장애는 backend healthcheck가 감지하지만,
`unhealthy` 표시 자체가 컨테이너를 자동 재시작시키지는 않습니다.
백엔드 컨테이너는 비루트 사용자로 Uvicorn 프로세스 하나를 실행하고 종료 신호를 직접 받습니다.
프록시 헤더 신뢰는 기본적으로 꺼 두었습니다. 실제 프록시가 정해지면 신뢰 대상을 명시해 설정합니다.

Dockerfile 한 개는 **이 앱의 이미지**를 뜻합니다. 전체 NearEast의 컨테이너 수를 정하지 않습니다.
현재 Compose의 두 서비스는 로컬 개발 편의를 위한 구성입니다.
AWS, Kubernetes, 복제 수, GPU, 운영 저장소는 배포 구성이 확정되면 별도로 설정합니다.

## 개발 시작

[개발 기준](docs/development.md)에 따라 `modules/<기능>/`을 추가하고
`api/router.py`에 라우터를 등록합니다. 빈 기능별 폴더나 DB 클래스를 미리 여러 개 생성할 필요는 없습니다.

DB 모델을 만들 때는 `db/base.py`의 `Base`를 상속하고 `db/metadata.py`에 등록합니다.
Alembic의 초기 환경은 이미 생성했으므로 `alembic init`을 다시 실행하지 않습니다.
모델 추가 후의 revision 생성·검토·적용 명령은 [개발 기준](docs/development.md#alembic과-모델-추가)을 참고합니다.
현재는 revision이 없어 `alembic heads`와 `history`의 출력이 비어 있는 것이 정상입니다.
Docker 이미지에도 Alembic 설정과 이력 파일이 포함되지만 자동으로 실행하지는 않습니다.

참고: [FastAPI 여러 파일 구성](https://fastapi.tiangolo.com/tutorial/bigger-applications/),
[FastAPI Docker 배포](https://fastapi.tiangolo.com/deployment/docker/),
[Python 프로젝트 설정](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/).
