# NearEast backend — initial scaffold

Python 3.12 / FastAPI / PostgreSQL 17 로컬 개발 시작점입니다.
Compose 명령 하나로 백엔드와 DB를 띄우고 Swagger에서 연결 상태를 확인합니다.
업무 테이블·마이그레이션 이력·업무 Endpoint는 없으며 상태 확인 API 두 개만 제공합니다.
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
├── alembic/                # 마이그레이션 환경과 비어 있는 versions/
├── alembic.ini             # 비밀번호 없는 Alembic 설정
├── tests/                  # 앱·Health·마이그레이션 설정 검증
├── docs/development.md      # 모듈 경계와 HTTP API 작성 기준
├── pyproject.toml           # 의존성, 패키지 메타데이터
├── .env.example
├── .dockerignore
├── compose.yaml            # 개발용 backend + db, 상태 확인, DB 볼륨
└── Dockerfile              # 이 FastAPI 앱의 실행 이미지
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
| `NEAREAST_DB_HOST` | `127.0.0.1` | Python 직접 실행 시 DB 주소. Compose 내부는 항상 `db` |
| `NEAREAST_DB_PORT` | `5432` | 호스트의 DB 포트 / Python 직접 실행 시 접속 포트. Compose 내부는 항상 `5432` |
| `NEAREAST_DB_NAME` | `.env.example`: `neareast` | 최초 생성 DB 이름 |
| `NEAREAST_DB_USER` | `.env.example`: `neareast` | 개발용 DB 계정 |
| `NEAREAST_DB_PASSWORD` | `.env.example`의 개발용 값 | Compose에서는 필수. 앱 설정에 없으면 readiness 실패 |

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
