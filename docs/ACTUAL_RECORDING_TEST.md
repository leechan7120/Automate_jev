# 실제 녹화 기반 통합 검증

검증일: 2026-10-02

합성 동영상 파일을 만들지 않고, 공공AX Series 4 v4.2.0의 네이티브 `record`/`stop` 브리지를 사용해 `AutomateJev.SmokeTarget`에서 통합 데모가 `DEMO_APPROVED`를 입력하는 장면을 실제로 녹화했다.

## 녹화 증거

- 비디오: H.264 MP4, 1920×1080, 30fps, 4.90초, 2,925,072 bytes
- SHA-256: `23d9e07c0f67081c053d484857959be67db76d0c89709d0786a223fac3d077ea`
- 사이드카: 공공AX가 저장한 `.series4.json`
- 사이드카 핵심 이벤트: `TextEntry` / `DEMO_APPROVED`
- 화면 범위: 프로젝트의 격리된 SmokeTarget만 사용하고 사용자 문서나 브라우저는 열지 않음

## Gemini live 추출 결과

같은 MP4를 `POST /v1/workflows/extract`에 `provider=live`로 전송했다. 응답은 HTTP 200이며 다음 안전 계약을 만족했다.

```json
{
  "provider": "LIVE",
  "video": {
    "bytes": 2925072,
    "sha256": "sha256:23d9e07c0f67081c053d484857959be67db76d0c89709d0786a223fac3d077ea"
  },
  "workflow": {
    "workflow_id": "actual-recording-demo",
    "steps": [
      {
        "id": "fill-approved-marker",
        "domain": "desktop",
        "action_id": "smoke-target.fill-required-text",
        "success": {"required_text_present": true},
        "risk": "safe"
      }
    ],
    "completion": {
      "all": [{"required_text_present": true}]
    }
  },
  "execution_authorized": false
}
```

Gemini가 결정하는 범위는 단계 ID와 등록된 action ID로 제한된다. 실행 domain, risk, success, completion은 로컬 action catalog에서 파생되므로 영상이나 모델 응답이 실행 계약을 덮어쓸 수 없다. 추출 응답은 실행 권한을 부여하지 않는다.

## 재현 순서

1. `windows-host/build-framework.ps1`로 Windows Host와 SmokeTarget을 빌드한다.
2. 공공AX Series 4 브리지에서 녹화를 시작한다.
3. `uv run python -m automate_jev.integrated_demo --provider fixture --hold-seconds 2`를 실행한다.
4. 녹화를 중지하고 저장된 MP4와 `.series4.json`을 확인한다.
5. MP4를 `/v1/workflows/extract`의 `live` provider로 전송한다.
6. 반환된 action ID가 catalog에 존재하고, 로컬에서 재구성된 계약과 `execution_authorized=false`를 확인한다.

API 키, 녹화 원본, 사이드카는 저장소에 커밋하지 않는다.
