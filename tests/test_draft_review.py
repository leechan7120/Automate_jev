from dataclasses import replace

import pytest

from automate_jev.draft_review import DraftReviewService
from automate_jev.integrated_demo import synthetic_fill_action
from automate_jev.models import ContractError
from automate_jev.video_extractor import FixtureVideoExtractor


@pytest.mark.asyncio
async def test_review_token_is_bound_to_exact_workflow(tmp_path):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    workflow = await FixtureVideoExtractor().extract(
        video,
        mime_type="video/mp4",
        workflow_id="reviewed-workflow",
        allowed_root="D:/demo",
        actions=(synthetic_fill_action(),),
    )
    reviews = DraftReviewService(b"review-secret")
    token = reviews.issue(workflow)
    reviews.verify(token, workflow)

    changed = replace(workflow, allowed_root="D:/other")
    with pytest.raises(ContractError, match="hash mismatch"):
        reviews.verify(token, changed)
