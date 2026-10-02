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

## 안전 불변식

1. 모델은 등록된 action ID 외에 target이나 arguments를 만들 수 없습니다.
2. `EXECUTING` journal record가 내구성 있게 기록되기 전에 실행하지 않습니다.
3. state revision, action hash, registry version, approval binding이 하나라도 다르면 실행하지 않습니다.
4. 결과가 불명확한 변경성 action은 `UNKNOWN`으로 남기며 자동 재실행하지 않습니다.
5. `confirm` action은 올바른 승인 증표 없이는 실행하지 않습니다.

전체 10시간 계획은 [docs/HACKATHON_10H_PLAN.md](docs/HACKATHON_10H_PLAN.md)를 참고하세요.
