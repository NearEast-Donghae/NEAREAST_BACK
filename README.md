# NEAREAST

강원특별자치도 동해시의 철도 명소를 중심으로 한 여행 플래너입니다.
STEP 1은 공식 CSV와 근거가 확인된 장소를 통합하고, 인증된 공공데이터 스냅샷을 추가로 처리합니다.

## Windows 개발 환경

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements.txt
.\.venv\Scripts\python -m pip install "pytest>=8,<10"
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m uvicorn backend.app.main:app
```

서버가 실행되면 [상태 확인](http://127.0.0.1:8000/health)을 엽니다.

## 데이터 생성

1. `data/raw/`에 원본 CSV를 둡니다. 파일명과 위치는 [원본 입력 안내](data/raw/README.md)를 참고합니다.
2. 프로젝트 루트에서 아래 명령을 실행합니다. API 인증이나 인터넷 없이 CSV부터 구축할 수 있습니다.

```powershell
.\.venv\Scripts\python -m scripts.import_csv
.\.venv\Scripts\python -m scripts.build_dataset
.\.venv\Scripts\python -m scripts.validate_data
```

`data/processed/`에는 `locations.json`, `locations.csv`, `stations.json`,
`railway_lines.json`, `trains.json`, `transport_hubs.json`을 생성합니다.
`data/reports/`에는 `data_quality_report.md`, `data_quality.json`, `missing_data.json`을 생성합니다.
소스별 매핑은 [데이터 출처 및 활용 내역](docs/data_sources.md)에 있습니다.

- `locations.id`를 장소 참조에 사용합니다. 역과 교통시설은 같은 ID로 상세 테이블에 연결됩니다.
- 주소와 좌표는 원본 근거를 보존하며 좌표계는 EPSG:4326입니다. `coordinate_ready`는 좌표 검사 통과를 뜻합니다. `routing_ready`는 좌표 검사와 공개 방문 근거가 있는 장소를 표시합니다. 실제 길찾기 호출에는 각 장소의 진입점과 이동수단별 결과를 확인합니다.
- `observed_stay_minutes`는 수집값이며, `default_stay_minutes`는 서비스가 정한 분 단위 기본값입니다. `null`은 미확인입니다.
- `railway_lines.json`에는 출처가 제공한 노선·서비스명과 역 참조가 있습니다. 역 순서와 선로 지오메트리는 자료가 확보될 때까지 비워 둡니다.
- `trains.json`의 `departure`, `arrival`, `stops`는 TAGO에서 직접 조회한 구간의 두 역 시각입니다. 전체 노선과 중간역은 별도 근거가 필요합니다.

## API 인증 및 수집

공공데이터포털에서 대상 API별 활용 신청을 승인받은 뒤 프로젝트의 `.env`에 `DATA_GO_KR_API_KEY`를 설정합니다.
별도 키가 있으면 `TAGO_SERVICE_KEY`, `TOURAPI_SERVICE_KEY`, `DONGHAE_SERVICE_KEY`로 지정합니다.
API별 키가 우선하고, 같은 변수의 값은 프로세스 환경변수가 파일보다 우선합니다.
`TAGO_API_URL`, `TOUR_API_URL`, `DONGHAE_API_URL`을 지정하면 공식 HTTPS 기본 주소와 대조합니다.
`.env`와 원본 및 생성 결과는 Git에서 제외됩니다.

세 API의 JSON·XML 응답 검사는 아래 명령으로 실행합니다. 요약은 `data/reports/api_connectivity.json`에 저장합니다.
검사용 응답의 행은 수집 데이터에 포함되지 않습니다.

### Windows VS Code에서 TAGO만 검사

VS Code에서 `NEAREAST` 폴더를 열고 PowerShell 터미널에서 실행합니다.
가상환경에 HTTPX가 없다면 `.\.venv\Scripts\python.exe -m pip install "httpx>=0.28,<1"`로 설치합니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.check_public_apis --service tago
```

`data/reports/tago_connectivity.json`에 형식별 HTTP 상태, API 결과 코드, 실제 응답 형식, 파싱한 행 수를 기록합니다.
JSON과 XML 각각의 `status`가 `parsed`이면 해당 형식의 실제 데이터 행까지 확인한 것입니다.
서버 응답 전에 연결이 실패하면 HTTP 상태와 API 코드는 `null`입니다.

동해시 교통시설정보에서 응답 대기 시간이 초과되면 `--read-timeout`으로 읽기 제한을 늘려 검사할 수 있습니다.
연결 수립 제한은 12초이며, 보고서의 `reason`에서 연결 수립과 응답 대기의 시간 초과를 구분합니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.check_public_apis --service donghae --read-timeout 45
```

### STEP 1 첨부 수집본 가져오기

`donghae_public_data_20261119.zip`에서 TAGO와 TourAPI 결과를 가져온 뒤 기존 CSV와 함께 빌드합니다.
ZIP 경로는 실제 다운로드 위치에 맞춥니다. 이 작업에는 인터넷 연결이나 새 API 요청이 필요하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.import_public_data (Join-Path $env:USERPROFILE 'Downloads\donghae_public_data_20261119.zip')
.\.venv\Scripts\python.exe -m scripts.build_dataset
.\.venv\Scripts\python.exe -m scripts.validate_data
```

첨부에 포함된 시간표 날짜는 2026-11-19입니다. 시각표는 출발·도착역 쌍별 조회 구간으로 저장합니다.
동해시 교통시설정보 API는 이번 STEP 1 범위에서 제외했습니다. 기존 CSV의 버스·여객선 터미널은 포함합니다.

### 실시간 수집 명령

```powershell
git check-ignore -v .env
.\.venv\Scripts\python -m scripts.fetch_tourism
.\.venv\Scripts\python -m scripts.fetch_train_data cities
```

도시 조회 결과 `data/raw/tago_cities.json`에서 해당 시·도 코드를 확인한 뒤
`python -m scripts.fetch_train_data stations CITY_CODE`로 역 ID를 확보합니다.
`python -m scripts.fetch_train_data schedule YYYY-MM-DD DEPARTURE_ID ARRIVAL_ID`로 날짜별 구간을 조회합니다.
반대 방향은 출발·도착 ID를 바꿔 조회합니다. 최신 스냅샷 반영을 위해 데이터 생성 명령을 다시 실행합니다.
`--max-calls`는 기본 10회이며 TAGO에서는 하위 명령보다 앞에 씁니다.

## GitHub

[개인 NEAREAST 저장소](https://github.com/hazel0138/NEAREAST)는 비공개입니다. 이 작업 폴더의 `origin`에 등록되어 있습니다.
연결과 인증이 가능한 환경에서 `git push -u origin main`을 실행하면 로컬 커밋 이력이 함께 올라갑니다.

새 커밋과 향후 팀 PR에는 [팀 컨벤션](docs/team_conventions.md)을 적용합니다.
팀 백엔드 통합은 팀 기본 브랜치의 장소·열차 모듈과 데이터 계약을 비교한 뒤 별도 브랜치에서 진행합니다.
