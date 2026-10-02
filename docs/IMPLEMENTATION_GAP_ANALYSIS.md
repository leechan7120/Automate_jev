# MVP 차이 분석 및 구현 결과

기준 문서는 `HACKATHON_10H_PLAN.md`와 2026-10-02 현재 상태 보고서다.

## 우선순위

| 우선순위 | 차이 | 이번 구현 |
|---|---|---|
| P0 | 영상 검토와 안전 실행기가 분리됨 | 검토 토큰을 일회성 실행 세션으로 교환하는 API 추가 |
| P0 | 웹 UI가 검토에서 종료됨 | 실행 버튼, 단계 표시, 상태 polling, 실패 사유 표시 추가 |
| P0 | 공공AX 사이드카가 실행 흐름에 미연결 | 선택 업로드, 크기/UTF-8 JSON/버전 검증, catalog 및 Workflow 결속 추가 |
| P0 | 실제 경로 반복 검증 부족 | 자동 테스트와 Windows fixture 통합 실행으로 검증 |
| P1 | 승인 UI, emergency stop, crash recovery 정량 시험 부족 | 후속 작업 |
| P2 | latency·비용 대시보드, reset UI, 발표 자료 부족 | 후속 작업 |
| P3 | 다운로드·Notepad 저장·브라우저 업로드의 5-action 골든 패스 부족 | 현재 단일 안전 action 안정화 후 확장 |

## 새 실행 계약

1. 추출 API는 실행 권한이 없는 Workflow만 반환한다.
2. 검토 API는 정확한 Workflow hash에 결속된 단기 토큰을 발급한다.
3. 실행 API는 토큰 서명·만료·workflow ID·hash·catalog 결속을 다시 확인한다.
4. 검토 토큰은 한 번만 실행 세션으로 교환할 수 있다.
5. 실행 세션은 Jev/fixture 판단, 로컬 정책, Windows Host, verifier, journal을 호출한다.
6. 화면은 실행 상태와 journal 결과를 표시한다.

현재 제품형 골든 패스는 검증된 합성 SmokeTarget의 단일 action이다. 계획서의 브라우저 다운로드, Notepad 저장, 업로드는 아직 별도의 browser runtime과 두 confirm 승인 UI가 필요하므로 완료로 주장하지 않는다.
