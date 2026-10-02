import pytest

from automate_jev.progress_summarizer import GeminiProgressSummarizer
from automate_jev.slack import SlackMessage


class FakeResponse:
    text = '{"progress":["API integration shipped"],"discussions":["Reviewed rollout"],"decisions":["Agreed on daily sync"],"project_overview":"Slack-driven progress capture","implementation_approach":"Slack MCP, Gemini, and Notion replace-content updates","projects":[{"name":"Automate Jev","overview":"Slack-driven progress capture","progress":["API integration shipped"],"discussions":["Reviewed rollout"],"decisions":["Agreed on daily sync"],"next_actions":["Deploy the worker"]}]}'


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
    assert result.implementation_approach.startswith("Slack MCP")
    assert result.projects[0].name == "Automate Jev"
    assert result.projects[0].next_actions == ("Deploy the worker",)


class BusyAsyncAPI:
    def __init__(self):
        self.models = []

    async def generate_content(self, **kwargs):
        self.models.append(kwargs["model"])
        if len(self.models) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return FakeResponse()

    async def aclose(self):
        pass


class BusyAio:
    def __init__(self):
        self.models = BusyAsyncAPI()

    async def aclose(self):
        pass


class BusyClient:
    def __init__(self):
        self.aio = BusyAio()


@pytest.mark.asyncio
async def test_gemini_progress_summarizer_uses_fallback_for_transient_failure():
    client = BusyClient()
    summarizer = GeminiProgressSummarizer(
        api_key="test-key",
        model="gemini-3.8-flash",
        client_factory=lambda _: client,
    )

    result = await summarizer.summarize(
        messages=(SlackMessage("m-1", "eng", "chanh", "API shipped"),),
    )

    assert result.project_overview == "Slack-driven progress capture"
    assert client.aio.models.models == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
