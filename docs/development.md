# 개발 기준

클래스는 PascalCase, 새 변수·메서드는 camelCase, 상수는 UPPER_SNAKE_CASE로 작성합니다.
DB 테이블·컬럼은 snake_case입니다. SQLAlchemy/Pydantic의 예약 훅과 기존 PR의 공개 함수·응답 필드는
라이브러리 규약과 호환성 때문에 유지합니다. 새 외부 DTO는 camelCase입니다.

- `modules/*/controller.py`: HTTP 입력/응답, 인증·소유권 적용, CRUD 조립.
- `modules/*/dto.py`: 요청/응답 모델과 값 검증.
- `modules/*/service.py`: 인증, 대화 저장, 여행 계획 버전 관리의 업무 처리.
- `db/entities.py`: 공유 Entity와 FK. `db/metadata.py`가 Alembic에 등록합니다.
- `db/session.py`: 요청별 세션. 기존 챗봇 호출 계약 유지를 위해 ContextVar로 같은 세션을 전달합니다.
- `db/content_repository.py`: PostgreSQL 자료를 기존 챗봇·시간표의 객체로 변환합니다.
- `ingestion/service.py`: 원문·시간표 적재와 변경 문서 임베딩.
- `modules/chat/*`: 기존 PR의 답변·프롬프트·도구 로직. 저장소 변경과 분리합니다.

들여쓰기 4칸, 권장 줄 길이 100자, Ruff formatter를 사용합니다. 불필요한 import·디버깅 출력은 제거합니다.
함수는 한 가지 목적을 맡고, 공통 검증·소유권 확인은 서비스 함수로 재사용합니다.
주석은 로직의 반복 설명보다 경계·호환성·동시성 처리의 이유를 설명합니다.
인증 실패, 입력 오류, 소유권 없음, 버전 충돌, DB/생성기 장애를 구분합니다.

각 변경은 필요한 트랜잭션에서 commit합니다. LLM 응답을 기다리는 동안 대화방 행 잠금을 잡고 있지 않습니다.
DB의 요청 lease로 같은 방의 동시 생성을 막고, `requestId`로 재전송 결과를 재사용합니다.
lease가 만료돼 새 작업이 시작되면 이전 작업은 완료·실패 상태를 덮어쓰지 못합니다.
여행 갱신은 행 잠금과 expectedRevision을 함께 확인합니다.

마이그레이션 실행은 배포 준비 단계에서 명시적으로 수행하며 앱 startup에서 자동 생성하지 않습니다.
공개 장소·열차 조회는 DB 세션만 필요합니다. 사용자 대화나 계획에 접근하는 새 엔드포인트는 requireUser와 소유권 검증을 적용합니다.
예외 메시지·로그에 API 키·쿠키·비밀번호를 남기지 않습니다.
