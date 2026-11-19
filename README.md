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
| `/plans`, `/plans/variants`, `/plans/compare` | POST | 하루 계획 계산·세 가지 안·바꾸면 비교 (저장 없음, 공개) |
| `/chat/persona`, `/chat/messages` | GET, POST | 기존 PR 챗봇 계약 유지 |

대화 저장에는 `/conversations/{id}/messages`를 사용합니다. 기존 `/chat/messages`는 요청에 담긴
history를 사용하는 호환 API이며 자동 저장하지 않습니다. 다른 사용자의 리소스는 404를 반환합니다.
목록에는 `limit`, `offset`을 지원합니다. 메시지는 시간순, 여행·대화방 목록은 최신순입니다.
여행·여행 조건 수정에는 `expectedRevision`을 보내며 이전 버전이면 409를 반환합니다.

## 요청·응답 예시

장소·열차·하루 계획은 로그인 없이 호출합니다. 챗봇은 로그인 후 쿠키와 함께 호출합니다(위 "Swagger에서 로그인 후 확인" 참고).

### 장소 API

지도 마커와 역·장소 상세 화면용입니다. 여기의 `id`를 열차 API의 역 id, 챗봇의 `context.focused`·`pins`,
제안의 `target`이 그대로 씁니다.

#### `GET /api/v1/places` — 목록 (`?kind=station|poi|heritage|spot`)

```jsonc
{
  "places": [
    {"id": "station_donghae", "name": "동해역", "kind": "station",
     "passenger_stop": true,          // 역: true 승하차 가능 / false 불가(관찰만) / null 미확인
     "aliases": ["동해"], "lat": null, "lng": null}   // 좌표는 아직 비어 있음
  ]
}
```

#### `GET /api/v1/places/{id}` — 상세

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

### 열차 API (서비스 공용 시간표)

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

#### `GET /api/v1/stations/{station_id}/trains` — 역별 열차와 리듬

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

#### `GET /api/v1/trains?from=&to=&after=` — 구간 열차 검색

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

#### `GET /api/v1/trains/{train_no}` — 열차 상세

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

### 하루 계획 API (엮기)

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
  **장소 테이블에 좌표가 없는 장소는 걸어갈 수 없는 곳으로 취급**되어 `missed`/`conflicts`에 이유가 적힙니다.
  택시·버스는 계산하지 않고 `gap`의 `note`에 필요하다고만 적습니다.

#### `POST /api/v1/plans` — 하루 계획

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

#### `POST /api/v1/plans/variants` — 일정 비교 (열차 우선 / 방문지 우선 / 균형)

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

#### `POST /api/v1/plans/compare` — 바꾸면 비교

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

### 챗봇 API — 니어리

챗봇 이름은 **니어리**입니다. 해요체로 친근하게 말하지만, 말투가 규칙(문서 근거만, 시각은 도구로만,
계획 직접 수정 금지, 안전 대안)보다 앞서지 않습니다.

#### `GET /api/v1/chat/persona`

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

#### `POST /api/v1/chat/messages`

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
| 도구 대상 | 화면(`focused`·`pins`), 이번 질문, 직전 대화에서 사용자가 말한 장소만. 줄임말("묵호")은 장소 테이블의 `aliases`로 알아들음 |
| 칩 | 2~3개. 근처 데이터가 없는 장소의 "근처" 칩은 제거하고, 모자라면 기본 칩으로 채움 |
| 답 형식 | 400자 안에서 문장 단위로 자름. 마크다운·목록 기호 제거. 이어지는 대화에서는 인사말 반복 제거 |
| 근거 문서 | 프롬프트에 실제로 넣은 문서 id만 `sources`에 남김 |

규칙이 답을 바꾸면 서버 로그에 어떤 규칙이 무엇을 바꿨는지 원문과 함께 남습니다.

- 장소 id는 **장소 테이블(`/places`)** 이 기준이며 지도 마커 id와 같아야 합니다. 좌표는 아직 비어 있습니다.
  `aliases`는 질문에서 그 장소로 알아듣는 다른 표현입니다.
- 열차 시각은 아래 "열차 API"와 같은 시간표 스냅샷에서 나오며, 답 끝에 근거 문장("최근 토요일 3주 … 운행 기록 기준의
  어림 시각이에요.")이 붙고 제안의 `certainty`는 `estimated`입니다. 스냅샷 파일이 없을 때만 내장 예시 시각으로
  답하고 `(예시 데이터)`를 붙입니다. 도보 시간은 아직 예시 데이터입니다.

## 저장 구조

13개 테이블: `users`, `auth_sessions`, `places`, `knowledge_documents`, `knowledge_chunks`,
`timetable_snapshots`, `train_services`, `train_stops`, `conversations`, `messages`,
`planning_requests`, `itineraries`, `itinerary_revisions`.

사용자·소유권·외래키·검색 기준은 컬럼으로 관리합니다. 생성 결과와 수정 이력의 중첩 DTO,
원천 시간표 세부 필드는 JSONB로 저장합니다. JSONB는 PostgreSQL 컬럼이며 Git의 JSON 파일이 아닙니다.
지식 원문은 `knowledge_documents.body`, 임베딩은 `knowledge_chunks.embedding vector(1536)`에 저장합니다.

`0001_service_storage`는 테이블과 vector 확장, `0002_reference_content`는 기존 PR의 최초 자료를 적재합니다.
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
