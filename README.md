# Automate Jev

Jev를 제한된 후보 중 다음 행동을 고르는 decision router로 사용하고, 실제 실행 권한은 로컬 정책과 실행기에만 두는 Windows 자동화 MVP입니다.

현재 구현은 안전 코어, 실 Jev provider, 최소 권한 네이티브 Windows Host까지 포함합니다.

- 불변 `Action`과 `Observation`
- schema version과 state revision 분리
- session/version 기반 action registry
- action·revision·arguments에 결속된 HMAC 승인 증표
- checksum chain, flush/fsync, atomic index를 사용하는 실행 journal
- 의미 기반 위험 정책
- bounded verifier와 `SUCCESS / FAILURE / UNKNOWN`
- provider/runtime protocol과 fail-closed orchestrator
- deterministic simulation demo
- `.env`를 전역 환경에 주입하지 않는 Jev live provider
- 비밀 환경 변수를 전달하지 않는 Windows Host JSON-lines client

## 실행

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m automate_jev.demo
```

실 Jev 연결만 점검하려면 `.env.example`을 참고해 `.env`를 만든 뒤 다음을 실행합니다. 키 값은 출력되지 않으며 `.env`는 Git에서 제외됩니다.

```powershell
uv run python -m automate_jev.live_smoke --env-file .env
```

Windows Host 계약은 [docs/BRIDGE_PROTOCOL.md](docs/BRIDGE_PROTOCOL.md)에 정의되어 있습니다. Python의 `mock_bridge`와 C# Host가 같은 JSON-lines 계약을 구현하며, CI에서 네이티브 Host 빌드와 계약 테스트를 함께 수행합니다.

외부 패키지 없이 Windows 기본 .NET Framework 컴파일러로 C# Host를 빌드할 수 있습니다.

```powershell
.\windows-host\build-framework.ps1
```

Host는 현재 `notepad.fill-required-text`와 격리 테스트용 `smoke-target.fill-required-text`만 허용합니다. foreground 프로세스와 UIA text control, session·증가하는 registry version·action hash·expiry·idempotency·state revision을 다시 확인한 뒤 합성 문자열 `DEMO_APPROVED`만 입력합니다.

격리된 합성 TextBox 앱을 직접 띄워 네이티브 입력까지 확인하려면 다음 smoke를 실행합니다. 스크립트가 직접 시작한 PID와 foreground 창 PID가 다르면 입력하지 않습니다. Store Notepad가 기존 프로세스의 탭을 재사용하는 경우 사용자 창과의 혼동을 피하기 위해 자동 smoke 대상으로 사용하지 않습니다.

```powershell
uv run python -m automate_jev.windows_ui_smoke
```

Jev 판단부터 정책, journal, Windows Host, 결과 검증까지 하나의 경로로 실행하려면 통합 데모를 사용합니다. 기본값은 재현 가능한 `FIXTURE`이며 실 API는 명시적으로 선택해야 합니다. 두 모드 모두 confidence `0.8` 미만이면 입력하지 않습니다.

```powershell
uv run python -m automate_jev.integrated_demo --provider fixture
uv run python -m automate_jev.integrated_demo --provider live --env-file .env
```

`schemas/workflow.schema.json` 기반 Workflow는 `automate_jev.workflow.load_workflow`로 읽습니다. 로더는 크기와 JSON 구조를 제한하고, Workflow의 action ID와 위험도가 로컬의 신뢰된 action catalog와 일치할 때만 결속합니다.

영상에서 실행되지 않은 Workflow 초안을 만드는 로컬 API도 실행할 수 있습니다.

```powershell
uv run automate-jev-api
```

`POST /v1/workflows/extract`는 `video`, `provider`, `workflow_id`, `allowed_root` multipart field를 받습니다. 기본 `fixture` 모드는 자격 증명 없이 업로드 계약을 검증합니다. `live` 모드는 `.env`의 `gemini_api_key`(호환 별칭: `jev_PJ_Gemini_Key`)와 `gemini_model`을 사용합니다. 업로드는 허용된 video MIME과 50MB 이하로 제한됩니다. 모델은 등록된 action ID와 단계 순서만 제안하며, domain·risk·성공 조건·완료 조건은 신뢰된 로컬 action catalog에서 다시 구성됩니다. 이 API는 Workflow 초안만 반환하고 실행을 승인하지 않습니다.

브라우저에서 `http://127.0.0.1:8000`을 열면 영상 업로드부터 Workflow 검토와 실제 안전 실행까지 한 화면에서 사용할 수 있습니다. 공공AX `.series4.json`을 함께 올리면 크기·JSON·버전을 검증하고, 허용된 `TextEntry`가 로컬 action catalog 및 추출 Workflow와 일치하는지 확인합니다. 사이드카가 임의 action이나 입력값을 만들 수는 없습니다.

`POST /v1/workflows/review`는 수정된 Workflow를 다시 검증하고 정확한 초안 해시에 결속된 단기 검토 토큰을 발급합니다. `POST /v1/executions`는 이 토큰을 일회성 실행 세션으로 교환하며, 같은 토큰의 재사용이나 검토 후 Workflow 변경을 거부합니다. `GET /v1/executions/{session_id}`에서 `QUEUED / RUNNING / SUCCESS / BLOCKED / UNKNOWN` 상태와 단계별 원인을 조회할 수 있습니다. 실제 실행은 기존 Jev provider, 정책 gate, Windows Host, bounded verifier, journal 경로를 그대로 사용합니다.

## 안전 불변식

1. 모델은 등록된 action ID 외에 target이나 arguments를 만들 수 없습니다.
2. `EXECUTING` journal record가 내구성 있게 기록되기 전에 실행하지 않습니다.
3. state revision, action hash, registry version, approval binding이 하나라도 다르면 실행하지 않습니다.
4. 결과가 불명확한 변경성 action은 `UNKNOWN`으로 남기며 자동 재실행하지 않습니다.
5. `confirm` action은 올바른 승인 증표 없이는 실행하지 않습니다.

전체 10시간 계획은 [docs/HACKATHON_10H_PLAN.md](docs/HACKATHON_10H_PLAN.md)를 참고하세요.

공공AX Series 4의 실제 화면 녹화 기능으로 통합 데모를 녹화하고 Gemini live 추출까지 통과한 재현 기록은 [docs/ACTUAL_RECORDING_TEST.md](docs/ACTUAL_RECORDING_TEST.md)에 있습니다.
