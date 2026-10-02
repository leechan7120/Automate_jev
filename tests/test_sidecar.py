import json

import pytest

from automate_jev.integrated_demo import synthetic_fill_action
from automate_jev.models import ContractError
from automate_jev.sidecar import validate_sidecar
from automate_jev.video_extractor import FixtureVideoExtractor


def sidecar_bytes(text="DEMO_APPROVED", *, kind="TextEntry"):
    return json.dumps(
        {
            "version": 2,
            "currentVideoPath": "recording.mp4",
            "events": [
                {
                    "actionKind": kind,
                    "actionText": text,
                    "isQuarantined": False,
                }
            ],
        }
    ).encode()


@pytest.mark.asyncio
async def test_sidecar_evidence_is_bound_to_registered_workflow(tmp_path):
    action = synthetic_fill_action()
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    workflow = await FixtureVideoExtractor().extract(
        video,
        mime_type="video/mp4",
        workflow_id="sidecar-test",
        allowed_root="D:/demo",
        actions=(action,),
    )

    evidence = validate_sidecar(sidecar_bytes(), actions=(action,), workflow=workflow)

    assert evidence.accepted_event_count == 1
    assert evidence.action_ids == (action.id,)


def test_sidecar_cannot_invent_text_or_action():
    with pytest.raises(ContractError, match="not bound"):
        validate_sidecar(sidecar_bytes("UPLOAD_SECRET"), actions=(synthetic_fill_action(),))


def test_sidecar_rejects_missing_supported_evidence():
    with pytest.raises(ContractError, match="no accepted"):
        validate_sidecar(sidecar_bytes(kind="MouseLeftClick"), actions=(synthetic_fill_action(),))
