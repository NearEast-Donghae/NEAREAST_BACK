# NEAREAST 팀 컨벤션

## Git 커밋과 PR

새 커밋과 PR 제목에는 `접두사 : 작업 내용` 형식을 사용합니다. 콜론 양쪽에 공백을 한 칸씩 둡니다.

| 커밋 접두사 | PR Type | 적용 범위 |
|---|---|---|
| `Feat` | `FEAT` | 새로운 기능 구현 |
| `Add` | `ADD` | 에셋 파일 추가 |
| `Fix` | `FIX` | 버그 수정 |
| `Docs` | `DOCS` | 문서 추가 및 수정 |
| `Style` | `STYLE` | 스타일링 및 포맷팅 |
| `Refactor` | `REFACTOR` | 동작을 유지하는 리팩토링 |
| `Test` | `TEST` | 테스트 추가 및 수정 |
| `Deploy` | `DEPLOY` | 배포 |
| `Conf` | `CONF` | 빌드와 환경 설정 |
| `Chore` | `CHORE` | 기타 작업 |

예시 제목은 `Feat : 장소 데이터 구축`입니다. PR 본문에 실제 작업에 맞는 PR Type과 검증 결과를 기록합니다.
팀 규칙 적용 이전에 작성된 커밋 메시지는 이력에 보존합니다.

## Python 코드

- 클래스와 데이터 객체는 PascalCase, 내부 함수·메서드·변수·필드는 camelCase, 고정 설정값은 UPPER_SNAKE_CASE를 사용합니다.
- Python 특수 이름과 pytest의 `test_*`, `tmp_path`처럼 외부 도구가 찾는 이름은 해당 도구의 규격을 따릅니다.
- 파일·모듈 이름과 기존 CLI 옵션은 실행 계약입니다. 공공 API 필드, 환경변수, JSON 키, CSV 헤더, SQLite 컬럼 및 의존성의 인터페이스는 각 규격을 따릅니다.
- 4칸 들여쓰기와 일관된 줄바꿈을 사용합니다. 이름에 역할을 담고, 복잡한 조건에는 의미 있는 중간 변수를 쓰며, 진위 값과 `None`은 의도에 맞게 검사합니다.
- 예외 처리는 실패가 발생하는 범위에 한정합니다. 설명이 필요한 결정과 제약을 주석으로 남깁니다.

| 역할 | STEP 1 위치 |
|---|---|
| API 연결, 요청·할당량 처리, 응답 데이터 | `backend/app/services/public_data.py`의 `PublicData`, `ApiPage` |
| CSV·첨부·API 수집 | `scripts/import_csv.py`, `scripts/import_public_data.py`, `scripts/fetch_*.py` |
| 정제와 병합, 저장 조정 | `scripts/normalize_data.py`, `scripts/build_dataset.py` |
| 검증과 FastAPI 진입점 | `scripts/validate_data.py`, `backend/app/main.py` |

## 팀 저장소 통합

개인 개발 중에는 개인 GitHub를 `origin`으로 사용합니다. 비밀정보와 원본 데이터는 추적 대상에서 제외하고,
CSV 재배포가 필요한 경우 각 자료의 이용 조건을 검토한 뒤 포함할 파일을 결정합니다.

통합이 승인되면 팀 백엔드의 최신 기본 브랜치에서 별도 작업 브랜치를 만듭니다.
팀의 FastAPI 진입점, 장소 카탈로그, 열차 시간표가 사용하는 데이터 경로와 ID를 확인하고,
STEP 1 소스와 데이터 필드를 필요한 범위로 매핑합니다. 개인 프로젝트 전체를 팀 루트에 복사하면
`README.md`, `pyproject.toml`, `scripts/`, `tests/`와 데이터 로딩 규칙이 충돌할 수 있습니다.
날짜별 TAGO 운행 구간과 팀 서비스의 시간표 데이터가 각각 나타내는 기간과 범위도 확인합니다.
팀 기준으로 테스트를 통과한 뒤 작업 브랜치에서 PR을 작성합니다.
