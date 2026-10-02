from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from .draft_review import DraftReviewService
from .env import load_gemini_settings
from .integrated_demo import synthetic_fill_action
from .models import ContractError
from .video_extractor import (
    ALLOWED_VIDEO_TYPES,
    MAX_VIDEO_BYTES,
    FixtureVideoExtractor,
    GeminiVideoExtractor,
    VideoExtractionError,
)
from .workflow import parse_workflow


INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Automate Jev</title><style>
body{font-family:system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;background:#f7f8fa;color:#17202a}
main{background:white;padding:28px;border-radius:16px;box-shadow:0 8px 30px #0001}label{display:block;margin:14px 0 6px}
input,select,textarea,button{font:inherit}input,select,textarea{box-sizing:border-box;width:100%;padding:10px;border:1px solid #ccd2da;border-radius:8px}
textarea{min-height:320px;font-family:ui-monospace,monospace}button{margin-top:14px;padding:10px 16px;border:0;border-radius:8px;background:#1665d8;color:white;cursor:pointer}
.muted{color:#68717d}.status{margin-top:16px;padding:12px;border-radius:8px;background:#eef4ff;white-space:pre-wrap}</style></head>
<body><main><h1>Automate Jev</h1><p class="muted">영상에서 Workflow 초안을 만들고 실행 전에 검토합니다.</p>
<form id="upload"><label>영상</label><input name="video" type="file" accept="video/mp4,video/webm,video/quicktime" required>
<label>Provider</label><select name="provider"><option value="fixture">FIXTURE</option><option value="live">LIVE Gemini</option></select>
<label>Workflow ID</label><input name="workflow_id" value="uploaded-workflow" required>
<label>허용 루트</label><input name="allowed_root" value="D:/compe/skku_AI_hack/demo-data" required>
<button>초안 추출</button></form><label>Workflow JSON</label><textarea id="draft" spellcheck="false"></textarea>
<button id="review" type="button">검토 완료</button><div id="status" class="status">대기 중</div></main>
<script>
const status=document.querySelector('#status'),draft=document.querySelector('#draft');
document.querySelector('#upload').onsubmit=async(e)=>{e.preventDefault();status.textContent='추출 중…';const r=await fetch('/v1/workflows/extract',{method:'POST',body:new FormData(e.target)});const b=await r.json();if(!r.ok){status.textContent=b.detail||'추출 실패';return}draft.value=JSON.stringify(b.workflow,null,2);status.textContent=`${b.provider} 초안 · 실행 권한 없음`;};
document.querySelector('#review').onclick=async()=>{let value;try{value=JSON.parse(draft.value)}catch{status.textContent='JSON 형식을 확인하세요';return}const r=await fetch('/v1/workflows/review',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(value)});const b=await r.json();status.textContent=r.ok?`검토 완료\n${b.review.workflow_hash}\n실행 권한: 없음`:b.detail||'검토 실패';};
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


def create_app(*, env_path: str | Path = ".env") -> FastAPI:
    application = FastAPI(title="Automate Jev", version="0.1.0")
    reviews = DraftReviewService()
    trusted_actions = (synthetic_fill_action(),)

    @application.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/workflows/extract")
    async def extract_workflow(
        video: UploadFile = File(...),
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
                return {
                    "provider": provider.upper(),
                    "video": {"bytes": size, "sha256": video_hash},
                    "workflow": workflow.payload(),
                    "execution_authorized": False,
                }
        except ContractError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except VideoExtractionError:
            raise HTTPException(status_code=502, detail="video extraction failed") from None
        finally:
            await video.close()

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

    return application


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("automate_jev.api:app", host="127.0.0.1", port=8000)
