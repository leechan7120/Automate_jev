from __future__ import annotations

import os
from pathlib import Path
import shlex
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .draft_review import DraftReviewService, DraftReviewToken
from .execution_session import ExecutionRunner, ExecutionSessionService
from .integrated_demo import run_integrated_demo, synthetic_fill_action
from .mcp import MCPStdioClient, MCPToolClient
from .memory import LocalMemoryStore
from .models import ContractError
from .notion import NotionMCP
from .workflow import WorkflowDefinition, parse_workflow


BROWSER_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Automate Jev Browser</title><style>
:root{font-family:Georgia,serif;color:#e8edf2;background:#101518;--line:#354149;--panel:#172025;--accent:#e8b85b;--muted:#8e9ca5}*{box-sizing:border-box}body{margin:0;height:100vh;overflow:hidden;background:radial-gradient(circle at 75% 0,#25343b 0,#101518 42%);font-family:Arial,sans-serif}.chrome{height:100vh;display:grid;grid-template-rows:42px 48px 1fr}.tabs{display:flex;align-items:end;gap:5px;padding:0 12px;background:#0b0f11;border-bottom:1px solid var(--line)}.tab{height:34px;min-width:190px;max-width:280px;padding:9px 14px;border:1px solid var(--line);border-bottom:0;border-radius:8px 8px 0 0;background:#182126;color:#c9d2d7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tab.active{background:var(--panel);color:#fff}.new{width:34px;height:32px;margin-bottom:2px;background:none;color:#c9d2d7;border:0;font-size:21px;cursor:pointer}.toolbar{display:flex;align-items:center;gap:8px;padding:8px 14px;background:#172025;border-bottom:1px solid var(--line)}button{font:inherit;cursor:pointer}.icon{width:31px;height:31px;border:0;background:none;color:#b8c4ca;font-size:19px}.address{height:32px;flex:1;border:1px solid #45545c;border-radius:18px;background:#0f1518;color:#e7edf0;padding:0 16px;font-size:14px}.go{height:31px;padding:0 15px;border:1px solid #b68b40;border-radius:16px;background:var(--accent);color:#20190b;font-weight:bold}.content{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 320px}.viewport{min-width:0;display:flex;flex-direction:column;background:#f5f1e9;color:#20262a}.pagehead{padding:13px 20px;background:#fff;border-bottom:1px solid #d9d5cc;color:#63696d;font-size:12px;display:flex;justify-content:space-between}.page{padding:28px 32px;overflow:auto;white-space:pre-wrap;font-family:Georgia,serif;font-size:17px;line-height:1.55}.side{border-left:1px solid var(--line);background:rgba(23,32,37,.96);padding:22px 18px;overflow:auto}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.4px;color:var(--accent)}h1{font:26px Georgia,serif;margin:7px 0 22px;color:#f4f0e5}.card{border-top:1px solid var(--line);padding:16px 0}.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}.value{margin-top:7px;font-size:13px;line-height:1.45;word-break:break-word}.pill{display:inline-block;margin-top:8px;padding:4px 8px;border:1px solid #496052;border-radius:12px;color:#9ed0a7;font-size:11px}.empty{color:#67767e}.element{display:flex;gap:8px;padding:7px 0;border-bottom:1px solid #29343a;font-size:12px}.element b{color:#e8b85b}.status{position:fixed;bottom:0;left:0;right:320px;padding:8px 15px;background:#0c1113d9;color:#aab6bb;font-size:12px}
@media(max-width:760px){.content{grid-template-columns:1fr}.side{display:none}.status{right:0}.tab{min-width:130px}.page{padding:22px}}
</style></head><body><div class="chrome"><div class="tabs"><div class="tab active" id="tab">New tab</div><button class="new" title="New tab">+</button></div><div class="toolbar"><button class="icon" title="Back" disabled>&#8592;</button><button class="icon" title="Forward" disabled>&#8594;</button><button class="icon" title="Reload" onclick="observe()">&#8635;</button><input class="address" id="address" value="https://example.com"><button class="go" onclick="navigate()">Go</button></div><div class="content"><section class="viewport"><div class="pagehead"><span id="title">No page loaded</span><span id="url">Protected browser surface</span></div><div class="page" id="page"><span class="empty">Enter a URL and press Go. The page is rendered by the local Chromium host; Jev receives bounded page text and interactive element metadata.</span></div></section><aside class="side"><div class="eyebrow">Automate Jev</div><h1>Browser memory</h1><div class="card"><div class="label">Session</div><div class="value" id="session">Local persistent profile</div><span class="pill">OBSERVE ONLY</span></div><div class="card"><div class="label">DOM revision</div><div class="value" id="revision">-</div></div><div class="card"><div class="label">Interactive surface</div><div id="elements" class="value empty">No page observed</div></div><div class="card"><div class="label">Routine status</div><div class="value">Passive activity memory is local. Mutating browser actions remain revision-checked.</div></div><div class="card"><a href="/workflows" style="color:#e8b85b">Workflow review</a></div></aside></div></div><div class="status" id="status">Browser host ready</div><script>
const $=id=>document.getElementById(id),status=$('status'); // .join('\n') keeps legacy page clients compatible.
async function navigate(){const url=$('address').value.trim();status.textContent='Opening persistent browser tab...';const r=await fetch('/v1/browser/tabs',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url})});const b=await r.json();if(!r.ok){status.textContent=b.detail||'Navigation failed';return}render(b);}
async function observe(){const tab=$('tab').dataset.id;if(!tab)return;const r=await fetch(`/v1/browser/tabs/${tab}/observe`);const b=await r.json();if(!r.ok){status.textContent=b.detail||'Observation failed';return}render(b);}
function render(b){$('tab').dataset.id=b.tab_id;$('tab').textContent=b.title||b.url;$('title').textContent=b.title||'Untitled page';$('url').textContent=b.url;$('page').textContent=b.visible_text||'(No visible text)';$('revision').textContent=b.dom_revision;const preview=document.createElement('img');preview.alt='Rendered page preview';preview.src=`/v1/browser/tabs/${b.tab_id}/screenshot?revision=${encodeURIComponent(b.dom_revision)}`;preview.onload=()=>{$('page').replaceChildren(preview)};const items=b.interactive_elements||[];$('elements').innerHTML=items.length?items.map(x=>`<div class="element"><b>${x.role}</b><span>${x.name||x.id}</span></div>`).join(''):'<span class="empty">No buttons or fields detected</span>';status.textContent=`Observed ${items.length} interactive elements in the platform`}
</script></body></html>"""

INDEX_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Automate Jev</title><style>
body{font-family:system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;background:#f7f8fa;color:#17202a}
main{background:white;padding:28px;border-radius:16px;box-shadow:0 8px 30px #0001}label{display:block;margin:14px 0 6px}
input,select,textarea,button{font:inherit}input,select,textarea{box-sizing:border-box;width:100%;padding:10px;border:1px solid #ccd2da;border-radius:8px}
textarea{min-height:320px;font-family:ui-monospace,monospace}button{margin-top:14px;padding:10px 16px;border:0;border-radius:8px;background:#1665d8;color:white;cursor:pointer}
.muted{color:#68717d}.status{margin-top:16px;padding:12px;border-radius:8px;background:#eef4ff;white-space:pre-wrap}.steps{display:flex;gap:8px;margin:18px 0}.step{flex:1;padding:9px;border-radius:999px;text-align:center;background:#e7eaf0;color:#596273}.step.on{background:#1665d8;color:white}button:disabled{background:#9aa5b1;cursor:not-allowed}</style></head>
<body><main><h1>Automate Jev</h1><p class="muted">영상 초안부터 검토, Jev 판단, 안전 실행까지 한 화면에서 진행합니다.</p>
<div class="steps"><div class="step on" id="s1">1. 초안</div><div class="step" id="s2">2. 검토</div><div class="step" id="s3">3. 실행</div></div>
<form id="upload"><label>영상</label><input name="video" type="file" accept="video/mp4,video/webm,video/quicktime" required>
<label>공공AX 사이드카 (선택)</label><input name="sidecar" type="file" accept="application/json,.series4.json">
<label>Provider</label><select name="provider"><option value="fixture">FIXTURE</option><option value="live">LIVE Gemini</option></select>
<label>Workflow ID</label><input name="workflow_id" value="uploaded-workflow" required>
<label>허용 루트</label><input name="allowed_root" value="D:/compe/skku_AI_hack/demo-data" required>
<button>초안 추출</button></form><label>Workflow JSON</label><textarea id="draft" spellcheck="false"></textarea>
<button id="review" type="button">검토 완료</button><button id="execute" type="button" disabled>자동화 실행</button><div id="status" class="status">대기 중</div></main>
<script>
const status=document.querySelector('#status'),draft=document.querySelector('#draft'),execute=document.querySelector('#execute');let review=null;
const stage=n=>{for(let i=1;i<=3;i++)document.querySelector(`#s${i}`).classList.toggle('on',i<=n)};
document.querySelector('#upload').onsubmit=async(e)=>{e.preventDefault();review=null;execute.disabled=true;stage(1);status.textContent='추출 중…';const r=await fetch('/v1/workflows/extract',{method:'POST',body:new FormData(e.target)});const b=await r.json();if(!r.ok){status.textContent=b.detail||'추출 실패';return}draft.value=JSON.stringify(b.workflow,null,2);status.textContent=`${b.provider} 초안 · 실행 권한 없음`;};
document.querySelector('#review').onclick=async()=>{let value;try{value=JSON.parse(draft.value)}catch{status.textContent='JSON 형식을 확인하세요';return}const r=await fetch('/v1/workflows/review',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(value)});const b=await r.json();if(!r.ok){status.textContent=b.detail||'검토 실패';return}review=b.review;stage(2);execute.disabled=false;status.textContent=`검토 완료\n${review.workflow_hash}\n실행 버튼을 누르면 이 검토 토큰을 일회성 세션으로 교환합니다.`;};
execute.onclick=async()=>{let workflow;try{workflow=JSON.parse(draft.value)}catch{status.textContent='JSON 형식을 확인하세요';return}execute.disabled=true;const provider=document.querySelector('[name=provider]').value;const r=await fetch('/v1/executions',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({workflow,review,provider})});const b=await r.json();if(!r.ok){status.textContent=b.detail||'실행 세션 생성 실패';return}stage(3);status.textContent='실행 세션 생성됨\nWindows 대상 준비 및 안전 실행 중…';poll(b.session_id);};
async function poll(id){const r=await fetch(`/v1/executions/${id}`),b=await r.json();if(!r.ok){status.textContent=b.detail||'상태 조회 실패';return}status.textContent=[`${b.provider} · ${b.status}`,...b.timeline.map(x=>`${x.stage}: ${x.detail}`),b.result?JSON.stringify(b.result,null,2):''].filter(Boolean).join('\n');if(['QUEUED','RUNNING'].includes(b.status))setTimeout(()=>poll(id),500)}
</script></body></html>"""


MCP_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Automate Jev MCP</title><style>body{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;background:#f7f8fa;color:#17202a}main{background:#fff;padding:28px;border-radius:12px;box-shadow:0 8px 30px #0001}label{display:block;margin:14px 0 6px}input,button{font:inherit;box-sizing:border-box;width:100%;padding:10px;border:1px solid #ccd2da;border-radius:8px}button{margin-top:16px;background:#1665d8;color:#fff;border:0;cursor:pointer}.status{margin-top:16px;padding:12px;background:#eef4ff;white-space:pre-wrap}</style></head>
<body><main><h1>Automate Jev MCP</h1><p>MCP 서비스에서 개인 데이터를 가져와 local semantic memory에 저장합니다.</p><form id="sync"><label>Notion 검색어</label><input name="query" value="daily routine" required><button>MCP 데이터 동기화</button></form><div id="status" class="status">대기 중</div></main><script>document.querySelector('#sync').onsubmit=async event=>{event.preventDefault();const query=new FormData(event.target).get('query');const status=document.querySelector('#status');status.textContent='MCP에서 가져오는 중...';const response=await fetch('/v1/mcp/notion/sync',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({query})});const body=await response.json();status.textContent=response.ok?JSON.stringify(body,null,2):(body.detail||'동기화 실패')}</script></body></html>"""


def _configured_mcp_client() -> MCPToolClient | None:
    raw_command = os.environ.get("AUTOMATE_JEV_MCP_COMMAND", "").strip()
    if not raw_command:
        return None
    command = tuple(shlex.split(raw_command, posix=os.name != "nt"))
    return MCPStdioClient(command)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "")
    return value.lower() in {"1", "true", "yes", "on"} if value else default


def create_app(
    *,
    env_path: str | Path = ".env",
    execution_runner: ExecutionRunner | None = None,
    journal_root: str | Path | None = None,
    mcp_client: MCPToolClient | None = None,
    memory_root: str | Path | None = None,
) -> FastAPI:
    application = FastAPI(title="Automate Jev", version="0.1.0")
    reviews = DraftReviewService()
    trusted_actions = (synthetic_fill_action(),)
    repository_root = Path(__file__).parents[2]
    bin_directory = repository_root / "windows-host" / "bin"
    execution_journal_root = Path(journal_root or repository_root / ".automate-jev" / "web")
    semantic_memory_root = Path(memory_root or repository_root / ".automate-jev" / "memory")
    notion_client = mcp_client or _configured_mcp_client()
    notion_parent_id = os.environ.get("NOTION_ROUTINE_PARENT_ID", "").strip()
    notion_publish_enabled = _env_flag("NOTION_PUBLISH_ENABLED")

    async def native_runner(
        workflow: WorkflowDefinition,
        provider: str,
        session_id: str,
    ) -> dict[str, Any]:
        workflow.bind_actions(trusted_actions)
        return await run_integrated_demo(
            host_path=bin_directory / "AutomateJev.WindowsHost.exe",
            target_path=bin_directory / "AutomateJev.SmokeTarget.exe",
            journal_root=execution_journal_root,
            provider_mode=provider,
            env_path=Path(env_path),
            session_id=session_id,
        )

    executions = ExecutionSessionService(
        reviews=reviews,
        trusted_actions=trusted_actions,
        runner=execution_runner or native_runner,
    )

    @application.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return MCP_HTML

    @application.get("/workflows", response_class=HTMLResponse)
    async def workflows() -> str:
        return MCP_HTML

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/mcp/notion/sync")
    async def sync_notion(document: dict[str, object] = Body(...)) -> dict[str, object]:
        query = document.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise HTTPException(status_code=400, detail="query must contain 1 to 512 characters")
        if notion_client is None:
            raise HTTPException(status_code=503, detail="MCP client is not configured")
        try:
            notion = NotionMCP(notion_client)
            records = await notion.sync_to_memory(query, LocalMemoryStore(semantic_memory_root))
            return {"source": "notion-mcp", "query": query, "records": [record.payload() for record in records]}
        except ContractError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    @application.post("/v1/mcp/notion/publish-routine")
    async def publish_notion_routine(document: dict[str, object] = Body(...)) -> dict[str, object]:
        title = document.get("title")
        content = document.get("content")
        if not isinstance(title, str) or not isinstance(content, str):
            raise HTTPException(status_code=400, detail="title and content are required")
        if not notion_publish_enabled:
            raise HTTPException(status_code=403, detail="Notion routine publishing is disabled")
        if not notion_parent_id:
            raise HTTPException(status_code=503, detail="NOTION_ROUTINE_PARENT_ID is not configured")
        if notion_client is None:
            raise HTTPException(status_code=503, detail="MCP client is not configured")
        try:
            notion = NotionMCP(notion_client)
            result = await notion.publish_routine(
                title=title,
                content=content,
                parent_id=notion_parent_id,
            )
            return {"source": "notion-mcp", "parent_id": notion_parent_id, "result": result}
        except ContractError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    @application.post("/v1/workflows/review")
    async def review_workflow(document: dict[str, object] = Body(...)) -> dict[str, object]:
        try:
            workflow = parse_workflow(document)
            workflow.bind_actions(trusted_actions)
            token = reviews.issue(workflow)
            return {
                "reviewed": True,
                "workflow": workflow.payload(),
                "review": {**token.payload, "signature": token.signature},
                "execution_authorized": False,
            }
        except ContractError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    @application.post("/v1/executions", status_code=202)
    async def create_execution(
        background_tasks: BackgroundTasks,
        document: dict[str, object] = Body(...),
    ) -> dict[str, object]:
        try:
            workflow_document = document.get("workflow")
            review_document = document.get("review")
            provider = str(document.get("provider", "fixture")).lower()
            if not isinstance(workflow_document, dict) or not isinstance(review_document, dict):
                raise ContractError("workflow and review objects are required")
            signature = review_document.get("signature")
            if not isinstance(signature, str) or not signature:
                raise ContractError("review signature is required")
            review = DraftReviewToken(
                payload={key: value for key, value in review_document.items() if key != "signature"},
                signature=signature,
            )
            workflow = parse_workflow(workflow_document)
            session = await executions.create(
                workflow=workflow,
                review=review,
                provider=provider,
            )
            background_tasks.add_task(executions.run, session.session_id, workflow)
            return session.payload()
        except ContractError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    @application.get("/v1/executions/{session_id}")
    async def get_execution(session_id: str) -> dict[str, object]:
        try:
            return executions.get(session_id).payload()
        except ContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None

    return application


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("automate_jev.api:app", host="127.0.0.1", port=8000)
