from pathlib import Path
from types import SimpleNamespace

import pytest

from automate_jev.integrated_demo import synthetic_fill_action
from automate_jev.models import ContractError
from automate_jev.video_extractor import FixtureVideoExtractor, GeminiVideoExtractor


@pytest.mark.asyncio
async def test_fixture_extractor_returns_valid_unexecuted_workflow(tmp_path: Path):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"synthetic-video")
    workflow = await FixtureVideoExtractor().extract(
        video,
        mime_type="video/mp4",
        workflow_id="fixture-video",
        allowed_root="D:/compe/skku_AI_hack/demo-data",
        actions=(synthetic_fill_action(),),
    )

    assert workflow.steps[0].action_id == "smoke-target.fill-required-text"
    assert workflow.is_complete({"required_text_present": True}) is True


@pytest.mark.asyncio
async def test_fixture_extractor_rejects_bad_video_contract(tmp_path: Path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(ContractError, match="video must contain"):
        await FixtureVideoExtractor().extract(
            empty,
            mime_type="video/mp4",
            workflow_id="fixture-video",
            allowed_root="D:/demo",
            actions=(synthetic_fill_action(),),
        )


@pytest.mark.asyncio
async def test_gemini_extractor_validates_and_deletes_remote_file(tmp_path: Path):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"synthetic-video")
    deleted: list[str] = []
    remote = SimpleNamespace(
        name="files/test",
        uri="https://example.invalid/files/test",
        state=SimpleNamespace(name="ACTIVE"),
    )

    class Files:
        async def upload(self, **kwargs):
            return remote

        async def get(self, **kwargs):
            return remote

        async def delete(self, *, name):
            deleted.append(name)

    class Interactions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                output_text=(
                    '{"steps":[{"id":"step-1","domain":"desktop",'
                    '"action_id":"smoke-target.fill-required-text",'
                    '"success":{"required_text_present":true},"risk":"safe"}],'
                    '"completion":{"all":[{"required_text_present":true}]}}'
                )
            )

    class AsyncClient:
        files = Files()
        interactions = Interactions()

        async def aclose(self):
            return None

    client = SimpleNamespace(aio=AsyncClient())
    extractor = GeminiVideoExtractor(
        api_key="test-key",
        model="available-video-model",
        client_factory=lambda _: client,
    )

    workflow = await extractor.extract(
        video,
        mime_type="video/mp4",
        workflow_id="gemini-video",
        allowed_root="D:/demo",
        actions=(synthetic_fill_action(),),
    )

    assert workflow.steps[0].action_id == "smoke-target.fill-required-text"
    assert deleted == ["files/test"]
