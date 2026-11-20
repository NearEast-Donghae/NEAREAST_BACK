# 팀원 실행과 공개 데이터 배포

## 최초 실행

이 통합 버전 또는 이 PR이 병합된 main을 받아 `.env.example`을 `.env`로 복사하고 `NEAREAST_DB_PASSWORD`를 설정합니다. 기존 DB를 사용한다면 기존 DB 이름·사용자·비밀번호·Compose 프로젝트 이름을 유지합니다.

```bash
docker compose up --build -d
docker compose exec backend python scripts/seed_data.py
```

첫 명령은 DB 준비 후 migration을 완료하고 백엔드를 시작합니다. 둘째 명령은 이미지에 포함된 루트의 `neareast.seed.gz`를 PostgreSQL에 적재합니다. 새 파일을 받은 경우 반드시 `--build`로 이미지에도 반영합니다. 전체 공개 데이터 적재가 한 트랜잭션이며 다시 실행할 수 있습니다. seed는 만들어진 schema revision을 기록하고, DB가 그 revision이거나 그 뒤의 revision일 때만 적재합니다(그 전이거나 모르는 revision이면 한 행도 넣지 않고 멈춥니다).

Swagger는 http://localhost:8012/docs 입니다. 계정은 각자 가입합니다. seed에는 사용자·세션·채팅·저장 여행이 없습니다.

## 실제 기능 테스트에 필요한 설정

| 목적 | 설정 |
| --- | --- |
| 스키마·공개 데이터 복구 | DB 설정만 필요. 외부 API 호출·재임베딩 없음 |
| 실제 AI 챗봇 | `NEAREAST_CHAT_MOCK=false`, `NEAREAST_OPENAI_API_KEY`, 모델 `gpt-5.4-nano` |
| 벡터 검색 | `NEAREAST_CHAT_VECTOR_SEARCH=true`, `NEAREAST_EMBEDDING_MODEL=text-embedding-3-large`; seed 벡터는 1536차원 |
| 실제 도보 경로 | `NEAREAST_TMAP_API_KEY` |
| 프론트 연결 | 프론트의 `VITE_USE_MOCK_AUTH=false`, `VITE_USE_MOCK_CHAT=false`; Vite 프록시 기본 주소는 `127.0.0.1:8012` |
| 프론트 지도 | 프론트에 Kakao JavaScript 키·허용 도메인 설정 |

`.env.example`은 키 없이 기동할 수 있도록 챗봇 mock을 기본으로 둡니다. 복사만 하면 실제 LLM 모드와 같지는 않습니다. 두 명령으로 같은 코드·스키마·seed를 실행할 수 있지만 외부 API 응답과 사용자 기록까지 같아지는 것은 아닙니다. API 키와 실제 `.env`는 Git에 올리지 않습니다.

## 포함 데이터와 남은 범위

| 항목 | seed 행 수 |
| --- | ---: |
| 장소 | 135 (활성 장소 134, 중복 ID 연결 1) |
| 지식 문서·large 벡터 | 각 203 |
| 플래너 원본 장소 | 138 |
| 열차·정차 기록 | 63 / 958 |
| 플래너 열차 구간 | 1,145 |
| 출처·검토 참조 기록 | 21 |

좌표 미확보 8곳, 연결된 설명 미확보 10곳이 남아 있습니다. 현재 플래너의 방문 허용 장소는 동해역·묵호역·동해시종합버스터미널 3곳이며, 단일 날짜·도보 일정·방문 최대 5곳을 지원합니다. 열차 연계·여러 날 일정과 프론트 전체 연결은 완료 범위가 아닙니다.

시간표는 2026-11-21용 추정 스냅샷입니다. 최근 토요일 운행 기록 등 출처 날짜·제약은 API의 `basis`로 반환합니다. seed 적재는 실시간 갱신이 아니며 자동 수집 스케줄러도 포함하지 않습니다.

## PR #19와의 관계

[PR #19](https://github.com/NearEast-Donghae/NEAREAST_BACK/pull/19)의 head `f1d02aea4d4704102e69e9f219d5124df736c51d`를 기준으로 다음 내용을 선택 반영했습니다.

- 차종 6개 enum·API 검증·DB CHECK·`train_type_source`. 현재 migration head는 `0006_train_type_contract`입니다.
- 열차 63편 차종과 1814→1804 표기. 팀원이 2026-11-20 레일블루 화면을 수동 확인한 값이며 공식 API 차종이라고 표시하지 않습니다. 확인 대상은 2026-11-21입니다.
- 기존 1814 서비스 ID와 정차 기록을 보존하고 `original_no`, `number_correction`에 원번호·확인 날짜·출처를 저장합니다. 기존 번호 상세 조회와 저장 내역도 이어집니다.
- 사용자 발화로 확인되지 않은 출발지·시각·날짜를 계획 조건에서 제외하고 중복 장소 제안을 제거하는 챗봇 수정.

수집·검토 코드와 출처 원본은 로컬 `NearEast_Data`에 보관하고, 검토 결과는 이 저장소의 seed에 포함했습니다. Data 저장소가 없어도 팀원이 복원할 수 있습니다.

**정차 시각 115개 변경은 보류했습니다.** 지식 문서의 ‘무궁화→무궁화호’ 본문 표기 수정도 가져오지 않았습니다. 따라서 #19 전체와 동일한 변경은 아닙니다. 이번 통합 PR을 병합한 뒤 #19를 별도로 병합할 필요는 없으며, 보류 부분이 필요하면 현재 구조에 맞춘 후속 변경으로 가져옵니다. 기존 #19의 migration과 수집 경로를 그대로 병합하면 현재 분리 구조와 충돌합니다.

## 검증

빈 DB에서 자동 migration → seed 적재 → 같은 seed 재적재 → `alembic check`를 확인했습니다. 기존 DB 반영 전후 사용자 데이터 9개 테이블과 정차 958행의 해시가 동일했습니다. 1814/1804 번호 충돌 거절·적용 날짜 제한·기존 저장 참조 유지·재수집 보존을 검증했습니다. Playwright 검증은 사용자 요청으로 보류했습니다.
