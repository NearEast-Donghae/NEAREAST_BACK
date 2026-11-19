# NearEast backend — initial scaffold

Python 3.12 / FastAPI / PostgreSQL 17 로컬 개발 시작점입니다.
Compose 명령 하나로 백엔드와 DB를 띄우고 Swagger에서 연결 상태를 확인합니다.
업무 테이블·마이그레이션 이력은 없으며, 상태 확인 API와 서비스 API(장소, 열차 시간표, 니어리 챗봇)를 제공합니다.
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
│       ├── places/          # 장소 목록·상세 (지도 마커 id의 기준)
│       ├── trains/          # 서비스 공용 시간표: 역별 열차·구간 검색·열차 상세
│       ├── plans/           # 하루 계획 계산(엮기): 계획·세 가지 안·바꾸면 비교. 저장 없음
│       └── chat/            # 니어리 챗봇. 열차 도구는 trains 모듈을 호출
├── knowledge/              # 챗봇 지식 문서 (파일명 = 문서 id = 지도 마커 id)
├── data/places.json        # 지도 마커·챗봇·시간표가 공유하는 장소 id 목록
├── data/timetable_*.json   # 토요일 시간표 스냅샷 (scripts/fetch_timetable.py 산출물)
├── data/train_types.json   # 열차 종류 표 (손으로 채움)
├── scripts/fetch_timetable.py  # 공공데이터 운행 기록 → 시간표 스냅샷 (원본은 data/raw/, 커밋 안 함)
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

## 장소 API

지도 마커와 역·장소 상세 화면용입니다. 여기의 `id`를 열차 API의 역 id, 챗봇의 `context.focused`·`pins`,
제안의 `target`이 그대로 씁니다.

### `GET /api/v1/places` — 목록 (`?kind=station|poi|heritage|spot`)

```jsonc
{
  "places": [
    {"id": "station_donghae", "name": "동해역", "kind": "station",
     "passenger_stop": true,          // 역: true 승하차 가능 / false 불가(관찰만) / null 미확인
     "aliases": ["동해"], "lat": null, "lng": null}   // 좌표는 아직 비어 있음
  ]
}
```

### `GET /api/v1/places/{id}` — 상세

```jsonc
// GET /api/v1/places/poi_cheongok_cave
{
  "id": "poi_cheongok_cave", "name": "천곡황금박쥐동굴", "kind": "poi", "passenger_stop": null,
  "aliases": ["천곡동굴", "황금박쥐동굴"], "lat": null, "lng": null,
  "summary": "천곡황금박쥐동굴은 도심에 있는 유일한 천연 석회석 동굴이에요. …",   // 소개 첫 문단
  "description": "… \n\n운영 시간은 09:00~18:00이고, …",   // 전문, 문단은 빈 줄로 구분
  "source": "poi_cheongok_cave",     // 근거 문서 id (없으면 summary·description과 함께 null)
  "has_trains": false                // true면 /stations/{id}/trains 로 열차를 볼 수 있음
}
```

없는 id는 **404** `{"detail": "place not found"}`, 장소 파일을 읽을 수 없으면 **503**입니다.

## 열차 API (서비스 공용 시간표)

화면과 니어리가 같은 시간표를 씁니다. **모든 응답에 `basis`(근거)가 있습니다.** 화면에 `basis.summary`를 보여 주세요.

```jsonc
"basis": {
  "certainty": "estimated",
  "summary": "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준",
  "target_date": "2026-11-21",
  "record_dates": ["2026-11-14", "2026-11-07", "2026-10-31"],
  "plan_date": "2026-12-19",
  "limits": ["2026-11-21 당일 운행 여부는 공공데이터로 확인할 수 없음(…)",
             "여객열차의 정차 기록만 있음: 화물열차와 통과 기록은 공식 데이터 없음",
             "열차 종류는 데이터에 없음: data/train_types.json에 손으로 채운 것만 표시"]
}
```

공통 열차 필드: `no`(열차번호), `type`(종류, 표에 없으면 `null` → 번호·노선·행선지로 표시), `line`(노선),
`direction`(`U` 상행 / `D` 하행), `origin`·`destination`(시발·종착역), `weekly_saturday`(3주 모두 운행),
`in_plan`(12/19 토요일 운행계획에도 있음), `spread_min`(주별 시각 차이, 분).

### `GET /api/v1/stations/{station_id}/trains` — 역별 열차와 리듬

쿼리: `after=HH:MM`(이후만), `direction=U|D`.

```jsonc
// GET /api/v1/stations/station_donghae/trains?after=16:00
{
  "station_id": "station_donghae", "station_name": "동해",
  "boarding": true,       // false면 승하차 불가 역(관찰만), 시각은 어림
  "note": null,           // 통과역·기록 없는 역이면 안내 문장
  "trains": [
    {"no": "1863", "type": null, "line": "동해선", "direction": "D",
     "origin": "동대구", "destination": "강릉",
     "weekly_saturday": true, "in_plan": true, "spread_min": 10,
     "time": "16:16",     // 정렬 기준: 출발, 없으면 도착, 통과역은 통과 시각
     "arr": "16:15", "dep": "16:16", "passing": false, "stop_type": "여객승하차", "time_basis": null}
    // … 시각 순
  ],
  "rhythm": {
    "hourly": {"16": 2, "17": 5, "18": 5, "19": 2, "20": 3, "21": 4, "22": 4, "23": 1},  // 시간대별 열차 수 (필터 적용 후)
    "busiest_hour": "17",
    "gaps": [{"start": "22:52", "end": "23:57", "minutes": 65}]   // 60분 이상 비는 구간
  },
  "basis": { … }
}
```

- 망상역(`station_mangsang`)은 `boarding: false`이고 열차마다 `passing: true`, `arr`/`dep`은 `null`,
  `time_basis`에 "묵호 10:37 출발과 정동진 10:57 도착 사이를 거리 비율(0.26)로 어림"처럼 근거가 담깁니다.
- 삼화·묵호항처럼 여객 기록이 없는 역은 `trains: []`와 `note`("… 화물열차 시각은 공식 데이터가 없어요.")를 돌려줍니다.
- 역이 아닌 id나 없는 id는 **404**, `after`·`direction` 형식 오류는 **422**, 스냅샷이 없으면 **503**입니다.

### `GET /api/v1/trains?from=&to=&after=` — 구간 열차 검색

출발역에서 타서 도착역에서 내릴 수 있는 열차만 출발 시각 순으로 돌려줍니다(통과역은 타고 내릴 수 없어 결과 없음).

```jsonc
// GET /api/v1/trains?from=station_donghae&to=station_mukho&after=16:00
{
  "from_id": "station_donghae", "to_id": "station_mukho", "after": "16:00",
  "trains": [
    {"no": "1863", "type": null, "line": "동해선", "direction": "D", "origin": "동대구",
     "destination": "강릉", "weekly_saturday": true, "in_plan": true, "spread_min": 10,
     "dep": "16:16", "arr": "16:24", "minutes": 8}
  ],
  "basis": { … }
}
```

### `GET /api/v1/trains/{train_no}` — 열차 상세

```jsonc
// GET /api/v1/trains/1861   (앞자리 0이 붙은 01861도 됨)
{
  "no": "1861", "type": null, "line": "동해선", "direction": "D",
  "origin": "동대구", "destination": "강릉",
  "weekly_saturday": true, "in_plan": true, "spread_min": 11,
  "stops": [
    {"name": "동해", "place_id": "station_donghae", "arr": "10:23", "dep": "10:27",
     "pass_time": null, "stop_type": "여객승하차", "time_basis": null},
    {"name": "망상", "place_id": "station_mangsang", "arr": null, "dep": null,
     "pass_time": "10:42", "stop_type": "통과(어림)",
     "time_basis": "묵호 10:37 출발과 정동진 10:57 도착 사이를 거리 비율(0.26)로 어림"}
    // … 시발역부터 종착역까지 전체. 장소 목록에 없는 역은 place_id가 null
  ],
  "basis": { … }
}
```

없는 열차번호는 **404** `{"detail": "train not found"}`입니다.

### 시간표 스냅샷 만들기

공공데이터 "한국철도공사_열차운행정보"는 대회 당일 운행계획, 통과 기록, 화물열차, 열차 종류를 주지 않습니다.
그래서 최근 토요일 3주의 실제 운행 기록으로 스냅샷을 만들고 모두 `estimated`로 표시합니다.

- 운행 기록은 지연이 섞인 실제 시각이라, 3주 가운데 출발은 가장 이른 시각, 도착은 가운데 값을 씁니다.
- 망상 통과 시각은 앞뒤 정차역(묵호~정동진) 사이를 역키로정 거리 비율로 어림합니다.
- 열차 종류는 `data/train_types.json`의 `type` 칸에 직접 채웁니다(비워 두면 `null`). 채운 값은 다시 만들어도 유지됩니다.

```bash
# .env에 NEAREAST_DATA_GO_KR_KEY(디코딩 키). 원본은 data/raw/에 저장되어 다시 실행해도 API를 부르지 않음
python scripts/fetch_timetable.py
python scripts/fetch_timetable.py --refresh   # 원본을 버리고 다시 받기
```

## 하루 계획 API (엮기)

사용자가 담은 열차·장소(핀)를 하루 타임라인으로 엮습니다. **저장하지 않습니다**: 요청에 담긴 것만으로 계산하고,
"나의 일정" 저장은 프론트(브라우저)가 합니다. LLM을 부르지 않습니다.

서버가 지키는 규칙:

- **핀 불가침**: `must=true` 항목은 빼거나 바꾸지 않습니다. 다 지킬 수 없으면 계획 대신 `feasible=false`와
  `conflicts`(이유 + 서버가 다시 계산해 되는 대안 최대 2개)만 돌려줍니다.
- **제안은 제안**: 빈 시간에 갈 만한 곳은 `suggestions`로만 줍니다(`needs_confirm`은 항상 `true`). 타임라인에 넣지 않습니다.
  수락하면 프론트가 `items`에 `{"kind":"visit","place":target,"stay_min":stay_min}`를 넣어 다시 요청합니다.
- **통과역은 관찰만**: 망상역처럼 서지 않는 역은 `observe`만 되고 `ride`의 출발·도착이 될 수 없습니다.
- **보수적 판정**: 화면에 보이는 시각은 대표 시각이고, 되는지는 최악의 주로 판정합니다. 타려면 출발 5분 전까지 승강장,
  내린 뒤에는 가장 늦게 도착한 경우부터, 관찰은 가장 일찍 온 경우의 2분 전까지. 그렇게 남는 여유가 `margin_min`입니다.
- 이동은 열차(시간표 스냅샷)와 도보뿐입니다. 도보는 좌표 직선거리 × 1.3 ÷ 4.5km/h(올림, 90분 초과는 쓰지 않음).
  **`data/places.json`에 좌표가 없는 장소는 걸어갈 수 없는 곳으로 취급**되어 `missed`/`conflicts`에 이유가 적힙니다.
  택시·버스는 계산하지 않고 `gap`의 `note`에 필요하다고만 적습니다.

### `POST /api/v1/plans` — 하루 계획

```jsonc
// 요청
{
  "date": "2026-11-21",
  "start": {"place": "station_donghae", "time": "09:00"},
  "end":   {"place": "station_donghae", "time": "19:00"},   // 같은 날, start보다 뒤
  "items": [                                                 // 최대 12개. must 기본 false("가능하면")
    {"kind": "ride",    "train": "1864", "from": "station_donghae", "to": "station_chuam", "must": true},
    {"kind": "ride",    "train": "1861", "from": "station_chuam", "to": "station_donghae", "must": true},
    {"kind": "station", "place": "station_mukho", "stay_min": 30},      // 역에 머물며 구경
    {"kind": "observe", "train": "753", "place": "station_donghae"},    // place를 빼면 서버가 편한 역을 고름
    {"kind": "visit",   "place": "poi_chotdaebawi", "stay_min": 40}     // 장소 방문 (stay_min 5~480, 기본 30)
  ],
  "mode": "balanced",            // train_first | visit_first | balanced(기본)
  "dismissed": ["poi_nongoldam"],// 거절한 제안. 다시 제안하지 않음
  "depth": "full"                // full(기본) | light: 되는지와 요약만(timeline=[]) | reference: 제안 없음
}
```

```jsonc
// 응답 (줄임. walk·missed·outside·suggestions 줄은 형태를 보이려고 다른 요청의 결과에서 가져온 예)
{
  "feasible": true,
  "summary": {"seen": 32, "ridden": 4, "visits": 1, "walk_min": 0, "longest_gap_min": 46, "end_arrival": "17:05"},
  "timeline": [
    {"type": "start", "at": "09:00", "place": "station_donghae"},
    {"type": "gap",  "from": "09:00", "to": "09:32", "place": "station_donghae",
     "note": "이 시간에는 동해역에 서는 열차가 없어요. 다른 곳으로 가려면 택시·버스가 필요해요",
     "passing": [{"train": "841", "at": "09:32"}]},                 // 이 공백을 끝내는 열차
    {"type": "wait", "from": "09:32", "to": "09:39", "place": "station_donghae",
     "passing": [{"train": "1801", "at": "09:34"}]},                // 기다리는 동안 덤으로 보이는 열차
    {"type": "ride", "item": 0, "train": "1864", "from": "station_donghae", "depart": "09:39",
     "to": "station_chuam", "arrive": "09:48", "certainty": "estimated", "margin_min": 39},
    {"type": "wait", "from": "09:48", "to": "10:15", "place": "station_chuam", "passing": []},
    {"type": "ride", "item": 1, "train": "1861", "from": "station_chuam", "depart": "10:15",
     "to": "station_donghae", "arrive": "10:23", "certainty": "estimated", "margin_min": 21},
    {"type": "ride", "item": null, "train": "751", "from": "station_donghae", "depart": "10:59",
     "to": "station_mukho", "arrive": "11:08", "certainty": "estimated", "margin_min": 28},  // item=null: 이동용으로 서버가 고른 열차
    {"type": "visit", "item": 2, "place": "station_mukho", "from": "11:08", "to": "11:38", "passing": []},
    {"type": "observe", "item": 3, "train": "753", "place": "station_donghae", "at": "17:05",
     "passing_through": false, "certainty": "estimated",
     "basis": "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준의 어림 시각", "margin_min": 305},
    {"type": "walk", "from": "station_chuam", "to": "poi_chotdaebawi", "depart": "09:48", "arrive": "09:56",
     "min": 8, "certainty": "estimated"},                           // 좌표가 있는 장소 사이에서만 나옴
    {"type": "end", "at": "19:00", "place": "station_donghae"}
  ],
  "conflicts": [],
  "missed":  [{"item": 4, "reason": "촛대바위까지 갈 열차가 없고, 좌표가 아직 없어 걸어가는 시간도 계산할 수 없어요"}],
  "outside": [{"item": 5, "train": "755", "at": "22:50", "hint": "하룻밤 묵으면 볼 수 있어요"}],
  "suggestions": [
    {"type": "add", "target": "poi_bukpyeong_market", "gap": ["12:30", "13:16"],
     "reason": "동해역 공백(12:30–13:16) 안에 다녀올 수 있어요(걸어서 편도 3분, 머무는 시간 30분)",
     "needs_confirm": true, "certainty": "estimated", "walk_min": 3, "stay_min": 30,
     "notes": ["11/21은 장날이 아니에요(다음 장날 11/23)"]}
  ],
  "basis": "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준의 어림 시각"
}
```

타임라인 스텝: `start` `end`(`at`) · `wait` `gap` `visit`(`from`~`to`는 시각) · `ride` `walk`(`from`·`to`는 장소 id,
시각은 `depart`·`arrive`) · `observe`(`at`). `item`은 `items`의 몇 번째인지입니다.

- `gap`: 그 자리에 30분 이상 열차가 오지 않는 구간. 빈 채로 둡니다. `wait`: 그보다 짧은 대기.
- `summary.seen`: 관찰한 열차 + 기다리거나 머무는 동안 지나간 열차(중복 제외).
- `suggestions`: 공백마다 최대 2곳. "왕복 도보 + 30분 머묾 + 복귀 여유 10분"이 공백에 들어가는 `poi`·`heritage`만,
  `dismissed`·이미 담은 곳·시작/종료 장소는 제외. 니어리의 공백 제안도 같은 함수(`plans.service.suggest`)를 씁니다.

must를 지킬 수 없을 때(`망상역`은 열차가 서지 않고 좌표도 아직 없어 갈 방법이 없음):

```jsonc
// items: [{"kind": "observe", "train": "1861", "place": "station_mangsang", "must": true}]
{
  "feasible": false, "summary": null, "timeline": [],
  "conflicts": [{
    "item": 0,
    "reason": "1861 열차는 망상역에 10:42에 오는데, 그 전에 망상역까지 갈 열차가 없고, 좌표가 아직 없어 걸어가는 시간도 계산할 수 없어요",
    "options": [
      {"label": "묵호역에서 보기",
       "change": {"item": 0, "replace": {"kind": "observe", "train": "1861", "place": "station_mukho", "must": true}}},
      {"label": "동해역에서 보기",
       "change": {"item": 0, "replace": {"kind": "observe", "train": "1861", "place": "station_donghae", "must": true}}}
    ]
  }],
  "missed": [], "outside": [], "suggestions": [], "basis": "…"
}
```

`options[].change`는 `items[item]`을 `replace`로 바꿔 다시 요청하라는 뜻입니다. 서버가 고쳐 놓지 않습니다.

상태 코드: 200 / 422(형식 오류, 모르는 필드, 13개 이상, 종료가 시작보다 이름, 모르는 시작·종료 장소) / 503(시간표·장소 파일 없음).
`items` 안의 모르는 장소·열차는 오류가 아니라 그 항목의 `missed`(must면 `conflicts`) 이유로 돌아옵니다.

### `POST /api/v1/plans/variants` — 일정 비교 (열차 우선 / 방문지 우선 / 균형)

요청은 `/plans`와 같습니다(`mode`는 무시). **세 안 모두 must 항목은 똑같이 지킵니다.** 다른 것은 must=false 항목 중
무엇을 먼저 넣는지와 제안 순서뿐입니다(`station`·`observe`·`ride`는 열차 쪽, `visit`는 방문 쪽).

```jsonc
{"variants": [
  {"mode": "train_first", "label": "열차 우선", "feature": "열차를 먼저 담았어요: 32편을 보고 4번 타요",
   "summary": {…}, "plan": {… /plans 응답과 같음 …}},
  {"mode": "visit_first", "label": "방문지 우선", "feature": "방문지를 먼저 담았어요: 1곳에 머물고 0분 걸어요", …},
  {"mode": "balanced",    "label": "균형",       "feature": "담은 4개 중 4개를 넣었어요: 가장 긴 빈 시간은 46분이에요", …}
]}
```

### `POST /api/v1/plans/compare` — 바꾸면 비교

"한 편 늦추기", "이 항목 빼기", "꼭 보기로 바꾸기" 모두 바꾼 요청을 `changed`에 넣어 보냅니다.

```jsonc
// 요청
{"base": { … /plans 요청 … }, "changed": { … 바꾼 요청 … }}
// 응답
{
  "base": { … 계획 … }, "changed": { … 계획 … },
  "diff": {
    "gained": [{"kind": "train", "id": "1863", "how": "ride"}],       // 값은 예. how: observe | ride | passing | visit
    "lost":   [{"kind": "train", "id": "754", "how": "passing"}, {"kind": "place", "id": "station_mukho", "how": "visit"}],
    "end_arrival": ["17:05", "16:15"],     // [base, changed]. 불가능한 쪽은 null
    "gap_change_min": -30                  // 가장 긴 공백의 변화. 한쪽이 불가능하면 null
  }
}
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
보통 응답은 2~3초, 도구를 쓰는 답은 4초 안팎입니다. 메시지 1건은 **전체 12초 안에** 끝납니다.
모델 호출이 그 안에 끝나지 않으면(호출 1회는 최대 8초, 일시적 실패는 남은 시간 안에서 한 번 재시도)
내장 응답기가 대신 답합니다(`mode: "fallback"`). 프론트 타임아웃은 15초로 잡아 주세요.

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
- 열차 시각은 아래 "열차 API"와 같은 시간표 스냅샷에서 나오며, 답 끝에 근거 문장("최근 토요일 3주 … 운행 기록 기준의
  어림 시각이에요.")이 붙고 제안의 `certainty`는 `estimated`입니다. 스냅샷 파일이 없을 때만 내장 예시 시각으로
  답하고 `(예시 데이터)`를 붙입니다. 도보 시간은 아직 예시 데이터입니다.

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
| `NEAREAST_LLM_TIMEOUT_SECONDS` | `8` | OpenAI 호출 1회 타임아웃. 일시적 실패는 한 번 재시도 |
| `NEAREAST_CHAT_DEADLINE_SECONDS` | `12` | 메시지 1건 전체의 시간 상한. 넘으면 내장 응답기가 대신 답함(`mode: "fallback"`) |
| `NEAREAST_CHAT_REASONING_EFFORT` | 없음 | 추론 모델(gpt-5 계열)만: `none`·`minimal`·`low`·`medium`·`high`. gpt-4.1 계열에는 두지 않음 |
| `NEAREAST_LLM_USAGE_LOG` | `llm_usage.jsonl` | 모델 호출마다 토큰 사용량(캐시 포함)·응답 시간, 요청마다 단계별 소요 시간(`kind: "request"`)을 한 줄씩 기록. Compose에서는 `/tmp/llm_usage.jsonl` |

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
