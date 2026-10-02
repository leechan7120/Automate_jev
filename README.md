# Automate Jev

Jev를 제한된 후보 중 다음 행동을 고르는 decision router로 사용하고, 실제 실행 권한은 로컬 정책과 실행기에만 두는 Windows 자동화 MVP입니다.

현재 첫 구현은 외부 UI를 조작하지 않는 안전 코어입니다.

- 불변 `Action`과 `Observation`
- schema version과 state revision 분리
- session/version 기반 action registry
- action·revision·arguments에 결속된 HMAC 승인 증표
- checksum chain, flush/fsync, atomic index를 사용하는 실행 journal
- 의미 기반 위험 정책
- bounded verifier와 `SUCCESS / FAILURE / UNKNOWN`
- provider/runtime protocol과 fail-closed orchestrator
- deterministic simulation demo

## 실행

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m automate_jev.demo
```

## 안전 불변식

1. 모델은 등록된 action ID 외에 target이나 arguments를 만들 수 없습니다.
2. `EXECUTING` journal record가 내구성 있게 기록되기 전에 실행하지 않습니다.
3. state revision, action hash, registry version, approval binding이 하나라도 다르면 실행하지 않습니다.
4. 결과가 불명확한 변경성 action은 `UNKNOWN`으로 남기며 자동 재실행하지 않습니다.
5. `confirm` action은 올바른 승인 증표 없이는 실행하지 않습니다.

전체 10시간 계획은 [docs/HACKATHON_10H_PLAN.md](docs/HACKATHON_10H_PLAN.md)를 참고하세요.
