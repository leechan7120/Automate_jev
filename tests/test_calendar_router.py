from datetime import date

import pytest

from automate_jev.calendar_router import CalendarDecision, CalendarEvent, SlackCalendarRouter, _parse_decision
from automate_jev.memory import LocalMemoryStore
from automate_jev.slack import SlackMCP, SlackMessage


class FakeMCP:
    def __init__(self):
        self.calls = []

    async def call_tool(self, *, server, tool, arguments):
        self.calls.append((server, tool, dict(arguments)))
        if server == "slack":
            return {"messages": [{
                "id": "m-1",
                "channel_name": "eng",
                "user": "chanh",
                "text": "10월 7일 오후 2시 배포 회의로 확정했습니다.",
            }]}
        return {"created": True, "id": "event-1"}


class StaticDecisionProvider:
    def __init__(self, events):
        self.events = events
        self.calls = 0

    async def decide(self, *, messages, reference_day, timezone_name):
        self.calls += 1
        return CalendarDecision(self.events)


def test_parse_decision_requires_confirmed_date_and_evidence():
    messages = (SlackMessage("m-1", "eng", "chanh", "10월 7일 오후 2시로 확정"),)

    decision = _parse_decision(
        '{"events":[{"confirmed":false,"summary":"미정 일정","start_date":"2026-10-07",'
        '"start_time":"14:00","evidence_ids":["m-1"]},{"confirmed":true,"summary":"배포 회의",'
        '"start_date":"2026-10-07","start_time":"14:00","end_time":"15:00",'
        '"evidence_ids":["m-1"]}]}',
        messages,
        "Asia/Seoul",
    )

    assert len(decision.events) == 1
    assert decision.events[0].summary == "배포 회의"


@pytest.mark.asyncio
async def test_router_creates_confirmed_event_once(tmp_path):
    client = FakeMCP()
    event = CalendarEvent(
        summary="배포 회의",
        start_date="2026-10-07",
        start_time="14:00",
        end_date="2026-10-07",
        end_time="15:00",
        timezone="Asia/Seoul",
        all_day=False,
        description="확정된 Slack 대화",
        evidence_ids=("m-1",),
    )
    provider = StaticDecisionProvider((event,))
    router = SlackCalendarRouter(
        slack=SlackMCP(client),
        calendar=client,
        decision_provider=provider,
        memory_root=tmp_path,
        calendar_server="calendar",
        create_tool="calendar-create-event",
        timezone_name="Asia/Seoul",
    )

    first = await router.route(date(2026, 10, 3))
    second = await router.route(date(2026, 10, 3))

    calendar_calls = [call for call in client.calls if call[0] == "calendar"]
    assert len(calendar_calls) == 1
    assert first["created"][0]["summary"] == "배포 회의"
    assert second["skipped_duplicates"] == ["배포 회의"]
    assert provider.calls == 2
