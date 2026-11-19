# NEAREAST STEP 1 데이터 출처 및 활용 내역

검토일은 2026-11-19입니다. Work에서 CSV 6개를 확인했습니다. 국가철도공단 첨부 2개는 전체 215행을 서로 비교했고, 로컬 CSV와도 모두 일치했습니다. 나머지 4개는 첨부의 이름과 바이트 수가 로컬 사본과 일치합니다. 첨부 원본 전송이 실패해 해당 4개의 실제 처리에는 로컬 사본을 사용했습니다.

## 실제 반영한 자료

| 출처 · 제공기관 · 공식 링크 | 사용한 입력 필드 | 활용 목적 | 처리 과정 | 결과 필드 | 확인 결과 |
|---|---|---|---|---|---|
| `강원특별자치도 동해시_관광명소현황_20251111.csv` · 동해시 · [공공데이터포털](https://www.data.go.kr/data/15029894/fileData.do) | 관광명소명, 도로명주소, 지번주소, 위도, 경도, 기준일자 | 여행지 후보 | CP949로 읽고 동해시 주소를 선별했습니다. 강원도 표기를 통일하고 좌표 범위를 점검했습니다. | `locations.json/.csv`의 `name,address,alternate_address,latitude,longitude,sources[].record_date` | 22행, 기준일 2025-11-11. 추암촛대바위의 원본 좌표는 공식 기본계획과 불일치해 보류했습니다. 전화번호는 사용 범위에서 제외했습니다. |
| `강원특별자치도 동해시_여객터미널정보_20261102.csv` · 동해시 · [공공데이터포털](https://www.data.go.kr/data/15040144/fileData.do) | 시군구명, 구분, 시설명, 운영주체, 소재지도로명주소, 소재지지번주소, 위도, 경도, 데이터기준일자 | 출발지·경유지 후보 | 동해시 3행을 선별했습니다. 버스터미널 운영기관 안내와 주소를 결합했습니다. | `locations.json/.csv`, `transport_hubs.json`의 공통 위치 필드 및 `operator,facility_category,sources[].record_date` | 3행, 기준일 2026-11-02. 주요노선·운행노선 수·홈페이지·연락처는 경로와 시간표의 별도 입력이므로 제외했습니다. |
| `국가철도공단_철도역 정보_20250711.csv` · 국가철도공단 · [공공데이터포털](https://www.data.go.kr/data/15067652/fileData.do) | 역이름, 주소, 위도좌표, 경도좌표, 관련노선, 취급업무, 입력일, 역소개 중 기념비 위치 근거 | 역 위치·여객/화물 취급 정보·철도 유산 | 전국 215행을 주소로 필터링했습니다. 관련노선 원문을 목록화하고 확인된 취급 정보만 반영했습니다. 취급업무에 항목이 없으면 값을 비웠습니다. | `locations.json/.csv`, `stations.json`의 위치와 `passenger_handling,freight_handling,railway_lines`, `railway_lines.json`의 역 참조, 기념비 후보 | 동해역·묵호역 2행. 파일 날짜는 2025-07-11이고 두 역의 입력일은 2021-09-29입니다. 역소개에는 과거 운행 설명이 있어 전체 텍스트를 제외했습니다. 열차정차횟수·역등급·지사·역연혁·다국어명은 미사용입니다. |
| `verified_places.csv` · [동해소방서](https://js119.gwd.go.kr/dh119/partici/partici_notice?articleSeq=11070643), [한국철도공사 물류](https://info.korail.com/info/contents.do?key=870), [한국철도공사 KTX 운행 안내](https://info.korail.com/info/selectBbsNttView.do?bbsNo=199&key=911&nttNo=25904), [동해시시설관리공단](https://www.dhsisul.org/pages/sub.htm?nav_code=new1701855784), [건설기술정보시스템](https://www.codil.or.kr/filebank/original/RK/OTKCRK180993/OTKCRK180993.pdf) | 역·시설명, 주소, 코레일의 정차 및 화물 취급 안내, 역사 자료의 노선명 | 동해·묵호역과 버스터미널의 공개 이용 근거, 묵호항·삼화역의 추가 근거 | 공식 페이지의 사실을 기록했습니다. 기존 장소와 이름·주소로 결합했습니다. 화물 취급과 공개 접근을 별도 필드로 처리했습니다. | `stations.json`의 `operation_status,passenger_handling,freight_handling,railway_lines`, `locations.json/.csv`의 `visit_allowed,description,sources` 및 `transport_hubs.json` | 동해역·묵호역·동해시종합버스터미널은 방문 근거가 있습니다. 묵호항역·삼화역은 정확한 좌표와 일반인 접근 여부가 미확보입니다. |
| `coordinate_exclusions.csv` · 동해시 · [동해시 탄소중립·녹색성장 기본계획](https://www.pcccr.go.kr/storage/board/base/2025/07/02/BOARD_ATTACH_1751436298948.pdf), [한국관광공사 장소 안내](https://access.visitkorea.or.kr/ms/detail_print.do?cotId=3d80ed0f-e004-4032-b0a6-312f218c3d4b) | 추암동 극점의 공식 좌표, 추암동 산 69 주소 | 잘못된 지도 핀·경로 후보 차단 | 원본의 정확한 좌표쌍에 제외 규칙을 적용했습니다. 원본 CSV의 값은 보존했습니다. | `locations.json/.csv`의 `latitude,longitude`를 null로 생성, `data_quality.json`에 원본 좌표와 근거 기록 | 원본 핀이 망상해변의 다른 명소 좌표와 근접합니다. 방문 출입구 좌표 확보가 필요합니다. |

## 미사용 CSV와 수집 대기 API

| 출처 · 제공기관 · 공식 링크 | 확인 필드 또는 API 명세 | 선별 및 반영 계획 | 확인 결과 |
|---|---|---|---|
| `강원도 동해시_패밀리사이트_20230327.csv` · 동해시 · [공공데이터포털](https://www.data.go.kr/data/15099922/fileData.do) | 명칭, 주소, 분류, 데이터기준일자 | 원본 검사만 수행했습니다. `주소`는 웹 URL이고 위치 좌표가 없어서 장소 출력에서 제외했습니다. | 25행, 2023-03-27. |
| `한국철도공사_동해선_역 위치정보_20240901.csv` · 한국철도공사 · [부산·울산 구간의 별도 공식 자료](https://www.data.go.kr/data/15041329/fileData.do) | 역번호, 역사명, 노선명, 환승역구분, 환승노선번호, 환승노선명, 역위도, 역경도, 역사도로명주소 | 역사도로명주소로 지역을 조사했습니다. 정확한 해당 첨부의 배포 URL과 이용 조건은 미확인입니다. 링크는 구간 확인용입니다. | 23행, 파일 날짜 2024-09-01. 부산·울산 23행이며 동해시 0행입니다. |
| `국가철도공단_철도역 정보_20250711(1).csv` · 국가철도공단 · [공공데이터포털](https://www.data.go.kr/data/15067652/fileData.do) | 원본과 동일한 14개 컬럼과 215행 | 전체 파싱 행을 원본 첨부와 대조했습니다. 같은 데이터는 한 번만 반영했습니다. | 모든 행과 필드가 일치합니다. |
| TAGO 열차정보 · 국토교통부 · [공공데이터포털](https://www.data.go.kr/data/15098552/openapi.do) | `GetCtyCodeList`, `GetCtyAcctoTrainSttnList`의 `nodeid,nodename`, `GetStrtpntAlocFndTrainInfo`의 `trainno,traingradename,depplacename,arrplacename,depplandtime,arrplandtime` | 날짜·출발역·도착역을 지정해 구간을 수집합니다. 날짜를 KST ISO 8601로 변환하고 조회 시각과 출처를 `trains.json`에 기록합니다. 역 목록을 함께 받으면 일치하는 로컬 역 ID를 연결합니다. | 공식 명세 확인, 모의 응답 테스트 수행. 인증키 미설정으로 실조회 0건. 구간 사이 역, 통과 시각, 화물 운행은 미확보입니다. |
| 국문 관광정보 서비스 GW · 한국관광공사 · [공공데이터포털](https://www.data.go.kr/data/15101578/openapi.do) | `areaBasedList2`의 `contentid,title,addr1,addr2,mapx,mapy,modifiedtime,contenttypeid` 및 지역 필드 | 법정동 코드 51/170 지역에서 관광지·문화시설을 수집합니다. 동일 이름과 주소 또는 근접 좌표가 확인되면 CSV와 병합해 `locations.json/.csv`로 보냅니다. | 공식 명세 확인, 모의 응답 테스트 수행. 인증키 미설정으로 실조회 0건. 이미지 필드는 저장 대상에서 제외했습니다. |
| 교통시설정보 조회 · 동해시 · [공공데이터포털](https://www.data.go.kr/data/15095898/openapi.do) | `getDonghaeTrafficFacilityInfoList`의 `GRID_ID,GRID_X_AXIS,GRID_Y_AXIS,CRT_YMD,DONG_NM,DONG_CD` 및 격자별 교통 지수 | KOTI-KATEC 100m 격자는 원래 좌표계로 `data/raw/donghae_traffic_grid.json`에 별도 보관합니다. 개별 정류장과 환승시설은 별도 원본에서 수집합니다. | 공식 명세 확인, 모의 응답 테스트 수행. 인증키 미설정으로 실조회 0건. 장소 출력에 반영한 행은 0건입니다. |

## 출처 식별과 재사용

- `source_ref`는 데이터셋·파일·레코드를 식별합니다. `source_id`는 제공기관의 ID이며, 원본에 없으면 null입니다. `field_sources`와 `railway_line_sources`로 병합 후 각 필드의 근거를 찾을 수 있습니다.
- `record_date`는 제공 데이터의 날짜, `snapshot_date`는 파일 또는 응답의 날짜, `checked_at`은 수동으로 근거를 확인한 날짜입니다. `fetched_at`은 API의 실제 조회 시점입니다.
- 편의시설은 모장소의 `amenities`에 기록합니다. [동해시시설관리공단](https://www.dhsisul.org/pages/sub.htm?nav_code=new1701855784)은 버스터미널 주차장을, [한국관광공사 열린관광](https://access.visitkorea.or.kr/ms/detail_print.do?cotId=3d80ed0f-e004-4032-b0a6-312f218c3d4b)은 추암촛대바위의 장애인 주차장과 화장실을 안내합니다. 각각 독립 위치와 운영 시간은 미확보입니다.
- 공공데이터포털에서 동해시 패밀리사이트·관광명소·여객터미널·국가철도공단 철도역의 이용허락범위 제한 없음 표기를 확인했습니다. 이 프로젝트에서는 원본 CSV와 API 응답을 로컬에 보관하고 공개 근거의 사실 및 링크만 Git에 포함합니다. TourAPI 사진은 개별 공공누리 조건이 있어 수집 대상에서 제외했습니다.
- 개발계정 포털 표기 한도는 TAGO 10,000건, TourAPI 1,000건, 동해시 교통시설 5,000건입니다. 클라이언트는 실행당 기본 10회 및 작업 폴더 기준 KST 일일 상한을 적용합니다. 발급 계정에 더 낮은 한도가 있거나 같은 키를 다른 환경에서도 사용하면 해당 잔여 한도에 맞춰 `--max-calls`를 지정합니다.
