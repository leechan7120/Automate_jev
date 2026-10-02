import pytest

from automate_jev.progress_summarizer import GeminiProgressSummarizer
from automate_jev.slack import SlackMessage


class FakeResponse:
    text = '{"progress":["API integration shipped"],"discussions":["Reviewed rollout"],"decisions":["Agreed on daily sync"],"project_overview":"Slack-driven progress capture"}'


class FakeAsyncAPI:
    async def generate_content(self, **kwargs):
        assert "secret=[REDACTED]" in kwargs["contents"]
        return FakeResponse()

    async def aclose(self):
        pass


class FakeAio:
    models = FakeAsyncAPI()

    async def aclose(self):
        pass


class FakeClient:
    aio = FakeAio()


@pytest.mark.asyncio
async def test_gemini_progress_summarizer_returns_structured_summary():
    summarizer = GeminiProgressSummarizer(
        api_key="test-key",
        model="test-model",
        client_factory=lambda _: FakeClient(),
    )

    result = await summarizer.summarize(
        messages=(SlackMessage("m-1", "eng", "chanh", "secret=do-not-send"),),
        existing_root="# Project",
    )

    assert result.progress == ("API integration shipped",)
    assert result.discussions == ("Reviewed rollout",)
    assert result.decisions == ("Agreed on daily sync",)
    assert result.project_overview == "Slack-driven progress capture"
