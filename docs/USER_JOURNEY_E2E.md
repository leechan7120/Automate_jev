# 사용자 관점 실제 E2E 검증

검증일: 2026-10-02

이 검증은 import나 mock 호출만 확인하지 않고, 실제 공공AX 녹화 파일이 Gemini와 Jev를 거쳐 Windows UI 입력으로 이어지는 사용 흐름을 대상으로 했다.

## 검증한 사용자 여정

1. 공공AX가 실제로 만든 MP4와 `.series4.json`을 준비한다.
2. 영상을 재생해 빈 Synthetic Target에 `DEMO_APPROVED`가 입력되는 장면을 눈으로 확인한다.
3. 같은 MP4를 Gemini live 영상 분석에 전달한다.
4. Gemini가 만든 Workflow와 공공AX 사이드카의 `TextEntry` 증거를 비교한다.
5. 사용자가 Workflow를 검토하고 hash 결속 토큰을 발급한다.
6. 웹 화면의 `자동화 실행` 버튼을 누른다.
7. Live Jev가 현재 Windows 상태와 등록된 precondition을 보고 action을 고른다.
8. Windows Host가 target과 revision을 재검증하고 입력한다.
9. verifier와 journal이 실제 효과와 `COMMITTED`를 확인한다.

## 사용한 실제 녹화

- 영상 크기: 2,925,072 bytes
- SHA-256: `23d9e07c0f67081c053d484857959be67db76d0c89709d0786a223fac3d077ea`
- 실제 재생 확인: 빈 Synthetic Target에서 `DEMO_APPROVED`가 나타남
- 사이드카: version 2, 이벤트 2개
- 허용된 증거: `TextEntry / DEMO_APPROVED` 1개

이 파일은 합성 동영상이 아니라 공공AX 네이티브 녹화로 이미 생성된 실제 MP4다. 이번 검증에서는 저장된 영상을 다시 재생해 픽셀 결과까지 확인했다. 개발용 공공AX 앱에서 새 녹화를 만들려 했으나 현재 머신에서 .NET SDK가 PATH에 없어 새 bridge 빌드는 수행하지 못했다.

## Live 결과

### Gemini

`gemini-3.5-flash`가 다음 action을 추출했다.

```json
{
  "id": "fill-approved-marker",
  "domain": "desktop",
  "action_id": "smoke-target.fill-required-text",
  "success": {"required_text_present": true},
  "risk": "safe"
}
```

사이드카의 허용 event와 추출된 action ID가 정확히 일치했다. 기존 `gemini-2.5-flash`는 신규 사용자에게 종료됐고, `gemini-3.8-flash`는 이 계정에서 60초 deadline을 넘었다. 모델은 코드에 하드코딩하지 않고 실행 환경에서 검증된 `gemini-3.5-flash`를 선택했다.

### Jev와 실제 실행

최종 웹 UI 실행 결과:

- provider: `LIVE`
- status: `SUCCESS`
- action: `smoke-target.fill-required-text`
- Jev confidence: `0.77`
- effect observed: `true`
- journal: `COMMITTED`

화면 캡처 도구나 Chrome이 실행 도중 foreground를 가져간 시험에서는 `ABSTAINED` 또는 `STALE_ACTION`으로 입력 없이 중단했다. 간섭을 멈춘 뒤 Live Jev + Windows Host 반복 시험은 5/5회 `SUCCESS / COMMITTED`였다.

## 사용자 테스트에서 발견하고 수정한 문제

1. 웹 UI JavaScript가 newline escape 오류로 파싱되지 않아 모든 버튼이 무반응이었다.
   - HTML 문자열을 raw string으로 바꾸고 회귀 테스트를 추가했다.
2. Jev에 action 설명만 전달해 명확한 상태에서도 confidence가 자주 0.8 미만이었다.
   - 등록된 precondition과 expected effect를 arguments 없이 구조화해 전달한다.
3. 모든 action에 동일한 confidence 기준을 적용해 안전한 합성 입력도 대부분 중단됐다.
   - `safe` action은 0.5, `confirm` action은 기존 0.8을 유지한다.
   - 실제 실행 권한은 여전히 로컬 policy, revision, target, journal 검증에만 있다.
4. 결과 화면에 Jev confidence와 마지막 관찰 facts가 없어 중단 이유를 알기 어려웠다.
   - 실행 결과에 confidence, detail, final observation을 표시한다.

## 남은 사용자 경험 제약

- Chrome 테스트 확장의 `Allow access to file URLs`가 꺼져 있어 자동화 도구로 파일 선택 버튼을 끝까지 재현하지는 못했다. 일반 사용자의 브라우저 파일 선택에는 해당하지 않는 테스트 환경 제약이다. 실제 multipart 업로드는 동일 API에서 성공했다.
- Gemini model은 `.env` 또는 실행 환경에 명시해야 한다. 모델 누락·종료·timeout을 시작 화면에서 사전 진단하는 provider readiness UI가 추가로 필요하다.
- 현재 실제 실행은 Synthetic Target의 단일 safe action이다. Notepad 저장과 브라우저 업로드를 포함한 5-action 골든 패스는 아직 구현되지 않았다.
