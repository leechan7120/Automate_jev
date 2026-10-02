from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from .draft_review import DraftReviewService, DraftReviewToken
from .env import load_gemini_settings
from .execution_session import ExecutionRunner, ExecutionSessionService
from .integrated_demo import run_integrated_demo, synthetic_fill_action
from .models import ContractError
from .sidecar import MAX_SIDECAR_BYTES, validate_sidecar
from .video_extractor import (
    ALLOWED_VIDEO_TYPES,
    MAX_VIDEO_BYTES,
    FixtureVideoExtractor,
    GeminiVideoExtractor,
    VideoExtractionError,
)
from .workflow import WorkflowDefinition, parse_workflow


INDEX_HTML = """<!doctype html>
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


async def _save_bounded(upload: UploadFile, destination: Path) -> tuple[int, str]:
    total = 0
    digest = hashlib.sha256()
    with destination.open("wb") as stream:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                raise ContractError(f"video must not exceed {MAX_VIDEO_BYTES} bytes")
            digest.update(chunk)
            stream.write(chunk)
    if total == 0:
        raise ContractError("video must not be empty")
    return total, "sha256:" + digest.hexdigest()


def create_app(
    *,
    env_path: str | Path = ".env",
    execution_runner: ExecutionRunner | None = None,
    journal_root: str | Path | None = None,
) -> FastAPI:
    application = FastAPI(title="Automate Jev", version="0.1.0")
    reviews = DraftReviewService()
    trusted_actions = (synthetic_fill_action(),)
    repository_root = Path(__file__).parents[2]
    bin_directory = repository_root / "windows-host" / "bin"
    execution_journal_root = Path(journal_root or repository_root / ".automate-jev" / "web")

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
        return INDEX_HTML

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/workflows/extract")
    async def extract_workflow(
        video: UploadFile = File(...),
        sidecar: UploadFile | None = File(None),
        provider: str = Form("fixture"),
        workflow_id: str = Form("uploaded-workflow"),
        allowed_root: str = Form(...),
    ) -> dict[str, object]:
        mime_type = (video.content_type or "").lower()
        if mime_type not in ALLOWED_VIDEO_TYPES:
            raise HTTPException(status_code=415, detail="unsupported video MIME type")
        if provider not in {"fixture", "live"}:
            raise HTTPException(status_code=400, detail="provider must be fixture or live")
        try:
            with tempfile.TemporaryDirectory(prefix="automate-jev-upload-") as directory:
                video_path = Path(directory) / "upload.bin"
                size, video_hash = await _save_bounded(video, video_path)
                sidecar_content = None
                sidecar_evidence = None
                if sidecar is not None:
                    if not (sidecar.filename or "").lower().endswith(".series4.json"):
                        raise ContractError("sidecar filename must end with .series4.json")
                    sidecar_content = await sidecar.read(MAX_SIDECAR_BYTES + 1)
                    sidecar_evidence = validate_sidecar(
                        sidecar_content,
                        actions=trusted_actions,
                    )
                if provider == "live":
                    api_key, model = load_gemini_settings(env_path)
                    extractor = GeminiVideoExtractor(api_key=api_key, model=model)
                else:
                    extractor = FixtureVideoExtractor()
                workflow = await extractor.extract(
                    video_path,
                    mime_type=mime_type,
                    workflow_id=workflow_id,
                    allowed_root=allowed_root,
                    actions=trusted_actions,
                )
                if sidecar_content is not None:
                    sidecar_evidence = validate_sidecar(
                        sidecar_content,
                        actions=trusted_actions,
                        workflow=workflow,
                    )
                return {
                    "provider": provider.upper(),
                    "video": {"bytes": size, "sha256": video_hash},
                    "workflow": workflow.payload(),
                    "sidecar": sidecar_evidence.payload() if sidecar_evidence else None,
                    "execution_authorized": False,
                }
        except ContractError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except VideoExtractionError:
            raise HTTPException(status_code=502, detail="video extraction failed") from None
        finally:
            await video.close()
            if sidecar is not None:
                await sidecar.close()

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
