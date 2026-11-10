# 개발 기준

## 모듈 경계

기능 책임이 정해지면 `src/neareast_api/modules/<기능>/`에 패키지를 만듭니다.
아래는 배치 예시이며 실제 파일은 필요한 시점에만 추가합니다.

```text
modules/<feature>/
├── __init__.py
├── router.py       # HTTP 입력, 인증 의존성, 응답 변환
├── schemas.py      # 요청/응답 데이터 형식
├── models.py       # 공통 Base를 상속하는 해당 기능의 ORM 모델
├── service.py      # 해당 기능의 업무 규칙
└── repository.py   # DB 접근이 실제 필요해질 때 추가
```

- `main.py`는 앱을 조립합니다. 업무 규칙이나 SQL을 넣지 않습니다.
- `api/router.py`는 기능별 라우터를 명시적으로 `include_router` 합니다.
- 기능 라우터는 `/api/v1`을 반복하지 않고 `/resources`처럼 리소스 경로만 가집니다.
- `core`는 작은 공통 설정·DB 연결 확인용입니다. 기능 모듈을 역으로 import하거나 모든 업무 규칙을 모으지 않습니다.
- 업무 서비스는 가능하면 FastAPI의 `Request`, `Response`, `HTTPException`에 의존하지 않습니다.
  HTTP 변환은 라우터/경계에서 합니다. 단순 함수로 충분하면 불필요한 클래스·추상 계층을 만들지 않습니다.
- 다른 기능의 repository/테이블 내부를 직접 사용하기보다 그 기능이 공개한 서비스 함수를 호출합니다.
  상호 import가 생기면 공통 조정 책임을 분리합니다. 현재는 범용 이벤트 버스나 플러그인 로더를 만들지 않습니다.
- 장기 재사용하는 DB pool·외부 클라이언트가 필요해지면 앱 lifespan에서 열고 닫습니다.
  import 시 연결하거나 프로세스 전역에 요청별 상태를 저장하지 않습니다.
  현재 상태 확인은 요청마다 연결을 열고 `SELECT 1` 후 즉시 닫습니다.
  ORM의 공통 Base와 마이그레이션 기반만 준비했으며 업무 세션과 repository는 기능 구현 시 추가합니다.
- `async def` 안에서 동기 I/O나 CPU/GPU 추론을 직접 실행하지 않습니다.
  실제 라이브러리와 실행 단위가 정해졌을 때 비동기 클라이언트 또는 별도 실행 경계를 선택합니다.

현재 `modules`는 비어 있고 구체적인 기능 분할을 강제하지 않습니다.
앱 생성 함수와 라우터 조립 위치를 분리했으므로 필요한 모듈을 선택해 별도 앱을 구성할 수 있습니다.
단, **서비스 분리가 폴더 이동만으로 끝나지는 않습니다.** 별도 서비스로 실행하면
통신 계약, 데이터 소유권, 타임아웃·재시도, 여러 서비스에 걸친 상태 변경을 추가로 설계해야 합니다.
Kubernetes는 이 설계를 자동으로 대신하지 않습니다.

## Alembic과 모델 추가

`db/base.py`의 공통 `Base`를 모든 기능 모델이 상속합니다. ID나 timestamp 컬럼을
공통 Base에서 자동으로 추가하지 않습니다. 모델 파일이 커지면 기능 안에서 `models/` 패키지로 나눠도 됩니다.
새 모델은 `db/metadata.py`에 명시적으로 import해야 Alembic의 자동 비교 대상에 포함됩니다.

```text
modules/<feature>/models.py → 공통 Base의 테이블 정의
db/metadata.py             → 모델들을 명시적으로 등록
alembic/env.py             → 등록된 테이블 정의와 실제 DB 비교
alembic/versions/           → 검토한 구조 변경 이력
```

현재 등록된 모델과 revision 파일은 없습니다. 앱 시작이나 `docker compose up`은
`create_all()`이나 `alembic upgrade`를 실행하지 않습니다. 빈 DB 상태를 유지합니다.
모델이 전혀 등록되지 않은 초기 상태에서 CLI의 `revision --autogenerate`는 연결 전에 거부합니다.
다른 모델 등록 누락까지 자동 검증하는 기능은 아니므로 생성된 변경 파일을 반드시 확인합니다.

Alembic은 기존 `Settings`와 `NEAREAST_DB_*` 설정을 사용합니다. 로컬 CLI는 `alembic.ini`가 있는
폴더의 `.env`를 읽으며 이미 설정된 프로세스 환경변수가 우선합니다.
컨테이너에서는 Compose가 전달한 `db:5432`를 사용합니다. 비밀번호를 ini 파일에 적지 않습니다.

### 모델을 구현한 뒤의 작업 순서 — Git Bash

먼저 로컬 개발 가상환경에 변경된 의존성을 설치합니다. Windows Git Bash 기준이며
macOS/Linux에서는 `.venv/Scripts/python.exe`를 `.venv/bin/python`으로 바꿉니다.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
docker compose up -d db
```

모델을 작성하고 `db/metadata.py`에 등록한 **뒤에만** 초안을 생성합니다.

```bash
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "describe_schema_change"
```

`alembic/versions/`에 생성된 파일의 생성·변경·삭제 내용을 검토하고 소스와 함께 Git에 보관합니다.
로컬에 설치한 CLI에서 생성해야 파일이 호스트에 남습니다. 실행 중인 컨테이너 안에만 revision을
생성하면 다음 빌드에 반영되지 않고 컨테이너 재생성 때 사라질 수 있으므로 그 방식은 사용하지 않습니다.

검토를 마친 변경을 적용할 때는 다음 중 하나를 사용합니다. 지금은 적용할 revision이 없습니다.

```bash
# 로컬 CLI로 적용
.venv/Scripts/python.exe -m alembic upgrade head

# 또는 변경된 모델·revision이 담긴 이미지를 다시 빌드한 뒤 컨테이너에서 적용
docker compose up --build -d
docker compose exec backend python -m alembic upgrade head
```

실제 적용하면 Alembic의 이력 관리 테이블 `alembic_version`이 생길 수 있습니다.
DB 접속 없이 설정된 revision 목록만 확인하려면 `python -m alembic heads` 또는
`python -m alembic history`를 사용합니다. 초기에는 출력이 없는 것이 정상입니다.
SQL 초안만 출력하는 `upgrade head --sql`은 실제 DB에 적용하지 않습니다.

참고: [Alembic 초기 설정](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[자동 생성과 수동 검토](https://alembic.sqlalchemy.org/en/latest/autogenerate.html).

## REST/HTTP API 추가 시 기준

이 문서는 개발 원칙이며 NearEast의 업무 Endpoint 명세가 아닙니다.
URL에 명사를 쓰는 것만으로 REST 설계가 완성되지는 않습니다.
리소스·표현·HTTP 의미와 상태 전이를 함께 검증합니다.

| 항목 | 기준 |
| --- | --- |
| 리소스 | 복수 명사와 ID를 기본으로 사용. 코드 모듈명이나 컨테이너 이름을 외부 경로에 노출하지 않음 |
| 조회 | GET/HEAD로 업무 상태를 변경하지 않음 |
| 생성 | POST 생성 성공 시 보통 201과 Location. 비동기 접수만 완료했다면 202와 결과 확인 방법을 제공 |
| 변경 | PATCH는 부분 변경, PUT은 대상 표현의 교체 의미를 구분. 누락 필드와 null을 구분 |
| 삭제 | DELETE의 반복 호출은 의도한 최종 효과가 같아야 함. 삭제와 리소스 상태 전이를 혼동하지 않음 |
| 멱등성 | PUT/DELETE의 의미를 유지. POST 재시도는 중복 생성 위험이 있는 기능에서 별도로 계약을 정함 |
| 상태 전이 | 승인·종료 등은 허용 출발 상태와 권한을 검증. 클라이언트가 임의의 내부 상태를 덮어쓰지 않도록 함 |
| 오류 | 미인증 401, 권한 부족 403, 미존재 404, 현재 상태 충돌 409, 입력 검증 실패 422 등의 의미를 구분 |
| 응답 | 204에는 본문을 넣지 않음. 응답 모델로 반환 필드를 제한하고 입력에 없는 오류·상태도 문서화 |
| 권한 | 리소스마다 현재 사용자의 접근 권한을 확인. 요청 본문의 사용자/역할 주장을 신뢰하지 않음 |
| 변경 검증 | 정상 사례와 함께 권한 실패, 잘못된 입력, 상태 충돌, 필요한 중복·경합 사례를 테스트 |

명령 성격의 동작을 억지로 DELETE나 자유로운 `PATCH state`로 바꾸지 않습니다.
실제 업무 의미에 맞는 리소스 또는 전이 요청을 정합니다.

기준: [HTTP Semantics, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html),
[PATCH, RFC 5789](https://www.rfc-editor.org/rfc/rfc5789.html),
[FastAPI APIRouter](https://fastapi.tiangolo.com/tutorial/bigger-applications/).

## 이번 초기 구조에서 선택한 사항

| 선택 | 이유 |
| --- | --- |
| Python 3.12, `src` 레이아웃 | 실행 버전과 import 경계를 명시하고 패키지 설치를 통해 실행 |
| `create_app()` factory | import 시 외부 연결 없이 앱을 생성하고 테스트별 설정/라우터를 분리 |
| 빈 `/api/v1` 라우터 | 업무 API를 만들기 전에도 등록 위치를 하나로 유지 |
| Pydantic 기반 설정 | 문서 노출과 DB 접속값 검증. 비밀번호는 SecretStr로 표시를 제한 |
| unittest + HTTPX | 별도 테스트 실행기 없이 ASGI 요청과 앱 시작을 검증 |
| 단일 앱 Dockerfile | 로컬 코드의 실행 방법만 정의. 최종 서비스 개수는 미확정 |
| 개발용 Compose | 백엔드와 빈 PostgreSQL, 준비 순서와 named volume. 운영 배포 구성과 별개 |
| `/health/live`, `/health/ready` | 업무 API와 분리한 운영 상태 확인. 후자는 DB `SELECT 1` 성공이 필요 |
| 공통 Base + Alembic | 기능별 모델은 한 metadata에 등록. revision은 명시적으로 생성·검토·적용 |

서비스 기능과 업무 정책은 이번 초기 커밋에 포함하지 않습니다.
기능별 API와 DB 모델은 요구사항이 정해진 뒤 추가합니다.
