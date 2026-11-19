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

공공데이터포털에서 서비스별 활용 신청을 승인받은 뒤 `.env.example`을 `.env`로 복사하고 필요한 키만 입력합니다.
URL과 요청 파라미터는 공식 명세에 맞게 클라이언트에 있습니다. 키는 출력이나 스냅샷에 저장되지 않습니다.
환경변수가 우선 적용됩니다. `.env`와 원본 및 생성 결과는 Git에서 제외됩니다.

```powershell
Copy-Item -LiteralPath .env.example -Destination .env -NoClobber
git check-ignore -v .env
.\.venv\Scripts\python -m scripts.fetch_tourism
.\.venv\Scripts\python -m scripts.fetch_transport --dong-name 삼화동
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

팀 저장소가 생기면 개인 `origin`을 보존하면서 팀 URL을 별도 원격으로 연결합니다.
팀 저장소가 비어 있으면 개인 `main`의 커밋으로 `neareast/step1` 브랜치를 푸시합니다.
팀 저장소에 이력이 있다면 팀 기본 브랜치에서 작업 브랜치를 만든 후 개인 커밋을 순서대로 가져옵니다.

```text
git remote add team TEAM_REPOSITORY_URL
git fetch team
git log --reverse --oneline main
git switch -c neareast/step1 team/TEAM_DEFAULT_BRANCH
git cherry-pick FIRST_COMMIT^..LAST_COMMIT
git push -u team neareast/step1
```

위 예시에서 첫 커밋이 루트 커밋이면 부모가 없으므로 `git cherry-pick` 뒤에 가져올 SHA들을 오래된 순서로 붙여 실행합니다.
팀 브랜치에서 테스트 후 Pull Request를 열고, 팀 저장소가 비어 있으면 팀의 기본 브랜치가 준비된 후 PR을 엽니다.
