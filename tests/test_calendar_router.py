from datetime import date
import json

import pytest

from automate_jev.calendar_router import CalendarDecision, CalendarEvent, SlackCalendarRouter, _event_key, _parse_decision
from automate_jev.memory import LocalMemoryStore
from automate_jev.slack import SlackMCP, SlackMessage


class FakeMCP:
    def __init__(self):
        self.calls = []
        self.find_existing = False

    async def call_tool(self, *, server, tool, arguments):
        self.calls.append((server, tool, dict(arguments)))
        if server == "slack":
            return {"messages": [{
                "id": "m-1",
                "channel_name": "eng",
                "user": "chanh",
                "text": "10월 7일 오후 2시 배포 회의로 확정했습니다.",
            }]}
        if tool == "calendar-get-event":
            return {"structuredContent": {"found": True}}
        if tool == "calendar-find-event":
            return {"structuredContent": {"found": self.find_existing}}
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


def test_parse_decision_defaults_one_hour_when_confirmed_start_has_no_end():
    messages = (SlackMessage("m-1", "eng", "chanh", "10월 7일 오후 2시 회의로 확정"),)

    decision = _parse_decision(
        '{"events":[{"confirmed":true,"summary":"배포 회의",'
        '"start_date":"2026-10-07","start_time":"14:00",'
        '"evidence_ids":["m-1"]}]}',
        messages,
        "Asia/Seoul",
    )

    assert len(decision.events) == 1
    assert decision.events[0].end_date == "2026-10-07"
    assert decision.events[0].end_time == "15:00"


def test_event_key_deduplicates_same_date_even_when_evidence_changes():
    first = CalendarEvent(
        summary="오프라인 회의",
        start_date="2026-10-07",
        start_time="14:00",
        end_date="2026-10-07",
        end_time="15:00",
        timezone="Asia/Seoul",
        all_day=False,
        description="첫 번째 확정 메시지",
        evidence_ids=("m-1",),
    )
    second = CalendarEvent(
        summary=" 오프라인   회의 ",
        start_date="2026-10-07",
        start_time="14:00",
        end_date="2026-10-07",
        end_time="15:00",
        timezone="Asia/Seoul",
        all_day=False,
        description="후속 확정 메시지",
        evidence_ids=("m-2",),
    )
    different_date = CalendarEvent(
        summary="오프라인 회의",
        start_date="2026-10-08",
        start_time="14:00",
        end_date="2026-10-08",
        end_time="15:00",
        timezone="Asia/Seoul",
        all_day=False,
        description="다른 날짜의 확정 메시지",
        evidence_ids=("m-3",),
    )

    assert _event_key(first) == _event_key(second)
    assert _event_key(first) != _event_key(different_date)


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

    calendar_calls = [call for call in client.calls if call[0] == "calendar" and call[1] == "calendar-create-event"]
    assert len(calendar_calls) == 1
    assert first["created"][0]["summary"] == "배포 회의"
    assert second["skipped_duplicates"] == ["배포 회의"]
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_router_recreates_event_when_remote_event_was_deleted(tmp_path):
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
    )

    await router.route(date(2026, 10, 3))
    marker = next((tmp_path / "calendar-events").glob("*.json"))
    marker.write_text(json.dumps({"summary": event.summary, "result": {"structuredContent": {"event": {"id": "deleted-event"}}}}))

    original_call_tool = client.call_tool

    async def report_deleted(*, server, tool, arguments):
        if tool == "calendar-get-event":
            return {"structuredContent": {"found": False}}
        return await original_call_tool(server=server, tool=tool, arguments=arguments)

    client.call_tool = report_deleted
    result = await router.route(date(2026, 10, 3))

    assert result["created"][0]["summary"] == "배포 회의"


@pytest.mark.asyncio
async def test_router_skips_remote_existing_event_without_marker(tmp_path):
    client = FakeMCP()
    client.find_existing = True
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
    router = SlackCalendarRouter(
        slack=SlackMCP(client),
        calendar=client,
        decision_provider=StaticDecisionProvider((event,)),
        memory_root=tmp_path,
    )

    result = await router.route(date(2026, 10, 3))

    create_calls = [call for call in client.calls if call[1] == "calendar-create-event"]
    assert create_calls == []
    assert result["skipped_duplicates"] == ["배포 회의"]
