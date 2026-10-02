from datetime import datetime, timezone

from automate_jev.activity import ObservedActivity
from automate_jev.memory import ActivityMemoryExtractor, LocalMemoryStore, MemoryKind, MemoryRecord


def test_memory_is_local_inspectable_and_searchable(tmp_path):
    store = LocalMemoryStore(tmp_path / "memory")
    record = MemoryRecord(
        kind=MemoryKind.SEMANTIC,
        scope="project-alpha",
        text="The team reviews project alpha in Slack every morning.",
        source=("activity:1",),
        confidence=0.9,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    store.upsert(record)
    store.upsert(record)

    matches = store.search("project alpha Slack")
    assert len(matches) == 1
    assert matches[0].count(record.memory_id) == 1


def test_extractor_creates_site_memory_from_observation(tmp_path):
    store = LocalMemoryStore(tmp_path / "memory")
    activity = ObservedActivity(
        observed_at=datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
        surface="notion",
        topic="project alpha",
        foreground="Notion - project alpha",
    )

    records = ActivityMemoryExtractor(store).consolidate([activity])

    assert records[0].kind is MemoryKind.SITE
    assert "project alpha" in store.search("project alpha")[0]