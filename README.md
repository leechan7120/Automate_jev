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

Jev 판단부터 정책, journal, Windows Host, 결과 검증까지 하나의 경로로 실행하려면 통합 데모를 사용합니다. 기본값은 재현 가능한 `FIXTURE`이며 실 API는 명시적으로 선택해야 합니다. 로컬 정책으로 다시 검증되는 `safe` action은 confidence `0.5`, 승인이나 외부 부작용이 필요한 action은 `0.8`을 최소값으로 사용합니다. confidence가 위험도를 낮추거나 승인 절차를 우회할 수는 없습니다.

```powershell
uv run python -m automate_jev.integrated_demo --provider fixture
uv run python -m automate_jev.integrated_demo --provider live --env-file .env
```

`schemas/workflow.schema.json` 기반 Workflow는 `automate_jev.workflow.load_workflow`로 읽습니다. 로더는 크기와 JSON 구조를 제한하고, Workflow의 action ID와 위험도가 로컬의 신뢰된 action catalog와 일치할 때만 결속합니다.

MCP 서비스 데이터를 local semantic memory로 가져오는 API도 실행할 수 있습니다.

```powershell
uv run automate-jev-api
```

`POST /v1/mcp/notion/sync`는 `{"query":"daily routine"}`를 받아 공식 Notion MCP의 검색 결과를 fetch하고 `.automate-jev/memory/semantic`에 저장합니다. MCP client는 `AUTOMATE_JEV_MCP_COMMAND` 환경 변수의 stdio command로 구성합니다. `POST /v1/mcp/notion/publish-routine`은 `NOTION_ROUTINE_PARENT_ID`로 지정한 `Routines` 페이지 아래에 새 routine 페이지를 만들며, `NOTION_PUBLISH_ENABLED=true`일 때만 동작합니다.

Slack MCP는 `POST /v1/mcp/slack/daily-progress`로 특정 날짜의 Slack 메시지를 검색합니다. `automate-jev-daily-progress` worker가 배포 후 24시간마다 실행되어 Slack 메시지를 `data/episodic/slack-daily-YYYY-MM-DD.md`에 저장하고, `NOTION_DAILY_PROGRESS_PARENT_ID` 아래에 `Daily Progress - YYYY-MM-DD` 페이지를 생성 또는 갱신합니다. Notion에 발행할 때는 Gemini가 Slack 근거를 반드시 정리하며, LLM이 없거나 실패하면 raw Slack 내용을 발행하지 않고 작업이 실패합니다. Daily Progress에는 Gemini가 정리한 진행, 논의, 결정, 프로젝트별 next action이 들어갑니다. 각 프로젝트 페이지는 `NOTION_PROJECT_ROOT_PAGE_ID` 아래에 자동 생성되고 이후 날짜에는 같은 페이지에 갱신됩니다. Project Root는 날짜별 원문 로그가 아니라 Gemini가 갱신한 현재 프로젝트 전체 개요, 구현 방식, 전체 진행 상태, 프로젝트별 상태를 담습니다.

브라우저 UI 없이도 `http://127.0.0.1:8000`에서 기존 Workflow 검토와 실행 API를 사용할 수 있습니다. 개인 사용 기록과 외부 서비스 데이터는 MCP adapter를 통해 수집하고 local memory와 routine learner에서 처리합니다.

### Notion MCP 연결

MCP 서버 명령을 준비한 뒤 Notion 페이지를 local semantic memory로 가져옵니다. `--` 뒤에는 실제 MCP stdio 서버 실행 명령을 넣습니다. 서버마다 tool 이름이 다를 수 있으므로 `--search-tool`로 조정할 수 있습니다.

```powershell
py -m pip install -e "."
node --version
npx --version
automate-jev-notion-sync --query "daily routine" --memory-root .automate-jev/memory -- npx -y mcp-remote https://mcp.notion.com/mcp
```

첫 실행 시 `mcp-remote`가 OAuth 인증 URL을 출력하거나 브라우저를 엽니다. Notion workspace를 승인하면 이후 `notion-get-tool-access`로 접근 가능한 tool을 확인하고 `notion-search`와 `notion-fetch`를 호출합니다. 결과는 `.automate-jev/memory/semantic` 아래에 저장됩니다. MCP tool 호출은 [mcp.py](src/automate_jev/mcp.py)의 JSON-RPC stdio client를 사용하고, Notion 변환과 routine 문서 발행 계약은 [notion.py](src/automate_jev/notion.py)에 있습니다.

Notion MCP는 공식적으로 `https://mcp.notion.com/mcp` Streamable HTTP와 OAuth를 사용합니다. 이 프로젝트는 `mcp-remote`를 stdio bridge로 사용하므로 프로젝트에 Notion token을 저장할 필요가 없습니다. OAuth가 끝난 뒤 다음 파일이 생성되는지 확인합니다.

```powershell
Get-ChildItem .automate-jev\memory\semantic
```

`POST /v1/workflows/review`는 수정된 Workflow를 다시 검증하고 정확한 초안 해시에 결속된 단기 검토 토큰을 발급합니다. `POST /v1/executions`는 이 토큰을 일회성 실행 세션으로 교환하며, 같은 토큰의 재사용이나 검토 후 Workflow 변경을 거부합니다. `GET /v1/executions/{session_id}`에서 `QUEUED / RUNNING / SUCCESS / BLOCKED / UNKNOWN` 상태와 단계별 원인을 조회할 수 있습니다. 실제 실행은 기존 Jev provider, 정책 gate, Windows Host, bounded verifier, journal 경로를 그대로 사용합니다.

### AWS EC2 배포

이 저장소에는 GHCR publish와 EC2 Docker Compose 배포를 위한 [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml), [CD workflow](.github/workflows/cd.yml), Nginx 설정이 포함되어 있습니다. `main`에 push하면 이미지를 만들고 EC2에서 새 컨테이너를 실행합니다. 실행 기록과 local memory는 EC2의 `data/`에 마운트되어 재배포해도 유지됩니다. 현재 앱은 SQLite를 사용하지 않으므로 별도 DB 서버는 필요하지 않습니다.

GitHub Actions Secrets로 다음 값을 등록해야 합니다.

```text
DEPLOY_HOST       EC2 공인 주소
DEPLOY_USER       SSH 사용자
DEPLOY_SSH_KEY    EC2 접속용 private key 전체 내용
DEPLOY_PATH       EC2 배포 디렉터리 (예: /opt/automate-jev)
GHCR_USERNAME     GHCR 로그인 사용자명
GHCR_READ_TOKEN   GHCR private image pull 권한 token
JEV_API_KEY       Jev provider key
AUTOMATE_JEV_MCP_COMMAND  MCP stdio command (예: npx -y mcp-remote https://mcp.notion.com/mcp)
AUTOMATE_JEV_SLACK_MCP_COMMAND  Slack token bridge (기본: `python -m automate_jev.slack_mcp_bridge`)
SLACK_OAUTH_TOKEN                Slack User OAuth token (`xoxp-...`)
AUTOMATE_JEV_SLACK_SERVER       Slack MCP server name (기본 slack)
AUTOMATE_JEV_SLACK_SEARCH_TOOL  Slack 검색 tool 이름 (기본 slack-search-messages)
AUTOMATE_JEV_SLACK_QUERY        날짜 필터를 대체할 Slack 검색어 (보통 비워둠)
AUTOMATE_JEV_TIMEZONE           일일 집계 timezone (예: Asia/Seoul)
AUTOMATE_JEV_LLM_ENABLED        Gemini Slack 요약 활성화 (Notion 발행 시 true 필요)
GEMINI_API_KEY                  Gemini API key (LLM 활성화 시 필요)
GEMINI_MODEL                    Gemini 모델 (예: gemini-3.8-flash)
NOTION_ROUTINE_PARENT_ID   Notion의 Automate Jev/Routines 부모 페이지 ID
NOTION_DAILY_PROGRESS_PARENT_ID  Notion의 Automate Jev/Daily Progress 부모 페이지 ID
NOTION_PROJECT_ROOT_PAGE_ID     자동 갱신할 프로젝트 root 문서의 페이지 ID
NOTION_PUBLISH_ENABLED     routine 쓰기 허용 여부 (기본 false, 명시적으로 true 필요)
```

EC2에는 Docker Engine, Docker Compose plugin, Nginx가 설치되어 있어야 하며, `DEPLOY_USER`가 Docker를 sudo 없이 실행할 수 있어야 합니다. Nginx 설정은 기본적으로 `server_name _`과 HTTP 80을 사용하므로 실제 도메인과 HTTPS는 EC2의 Certbot 또는 기존 TLS 설정에 맞춰 변경해야 합니다.

이 Docker 배포는 API, MCP memory sync, Slack daily-progress worker, 명시적 Notion routine publication을 제공하는 Linux backend 배포입니다. `/v1/executions`의 native Windows 실행은 컨테이너 안에 `AutomateJev.WindowsHost.exe`가 없기 때문에 AWS Linux에서 완료되지 않습니다. 실제 Windows UI 자동화까지 필요하면 Windows worker를 별도 운영하고 API에서 worker로 전달하는 구조가 필요합니다. Notion과 Slack MCP OAuth는 AWS 실행 환경에서 각각 한 번 인증하고, mcp-remote 인증 캐시를 영속 volume에 보존해야 합니다. CI/CD는 페이지를 자동 생성하지 않고 컨테이너만 배포하므로, Daily Progress worker가 정해진 주기로만 페이지를 생성합니다.

## 안전 불변식

1. 모델은 등록된 action ID 외에 target이나 arguments를 만들 수 없습니다.
2. `EXECUTING` journal record가 내구성 있게 기록되기 전에 실행하지 않습니다.
3. state revision, action hash, registry version, approval binding이 하나라도 다르면 실행하지 않습니다.
4. 결과가 불명확한 변경성 action은 `UNKNOWN`으로 남기며 자동 재실행하지 않습니다.
5. `confirm` action은 올바른 승인 증표 없이는 실행하지 않습니다.

전체 10시간 계획은 [docs/HACKATHON_10H_PLAN.md](docs/HACKATHON_10H_PLAN.md)를 참고하세요.

## 개인 루틴 자동화 기반

반복 사용 기록은 사용자가 별도로 주입하는 `action_id` 이벤트가 아니라, `ObservedActivity`로 수집하는 현재 foreground·서비스·주제·시간대 관찰값입니다. `PassiveActivityMonitor`는 같은 화면이 계속 유지되는 동안 중복 기록하지 않고 로컬 activity memory에만 저장합니다. `PassiveRoutineLearner`는 이 자연스러운 사용 기록에서 일간·주간·월간 패턴을 찾은 뒤 등록된 capability와 맥락을 매칭합니다.

```python
from automate_jev.activity import PassiveRoutineLearner
from automate_jev.routine import AutonomousRoutineRunner

patterns = PassiveRoutineLearner().learn(activity_memory.read(), registered_actions)
results = await AutonomousRoutineRunner(execute=run_registered_action).run_due(
	patterns,
	now=current_time,
)
```

`run_registered_action`은 기존 `AgentOrchestrator.run_next`를 호출하도록 연결해야 합니다. 따라서 자동화도 action registry, state revision, journal, verifier를 그대로 통과하며, `confirm`과 `blocked` action은 학습되더라도 사용자 승인 없이 실행되지 않습니다. 사용자의 반복 습관이 곧바로 외부 발송 권한으로 바뀌지 않도록 하는 경계입니다. 현재 activity source는 Windows foreground와 MCP sync가 제공하며, 수동으로 routine action을 주입하는 방식은 주 경로가 아닙니다.

Aside의 공개된 memory 흐름처럼 활동은 `ActivityMemoryExtractor`와 `NotionMCP`를 통해 local markdown memory로 정리되고, [memory.py](src/automate_jev/memory.py)의 파일에서 사람이 직접 확인·수정할 수 있습니다. 현재 구현은 `episodic / semantic / site / routine` memory 종류와 검색 계약을 제공합니다.

MCP 플랫폼 연결은 `MCPActionExecutor`와 `MCPToolClient` 계약을 사용합니다. Notion MCP 서버는 `target={"mcp_server": "notion", "mcp_tool": "..."}`, Slack MCP 서버는 `target={"mcp_server": "slack", "mcp_tool": "..."}`처럼 등록된 action으로 표현하고, 실제 MCP transport client만 주입하면 같은 루틴 학습·정책·journal 경로를 공유합니다.

공공AX Series 4의 실제 화면 녹화 기능으로 통합 데모를 녹화하고 Gemini live 추출까지 통과한 재현 기록은 [docs/ACTUAL_RECORDING_TEST.md](docs/ACTUAL_RECORDING_TEST.md)에 있습니다.

실제 영상을 눈으로 재생 확인하고, Gemini live 추출 → 사이드카 비교 → 웹 검토 → Live Jev 판단 → Windows Host 입력 → verifier/journal 확정까지 사용자 화면에서 실행한 결과는 [docs/USER_JOURNEY_E2E.md](docs/USER_JOURNEY_E2E.md)에 있습니다.
