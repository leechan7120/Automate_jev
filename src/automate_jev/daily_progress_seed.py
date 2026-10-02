from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .daily_progress import seed_daily_progress_records
from .memory import LocalMemoryStore


SEED_RECORDS = (
    {
        "date": "2026-09-30",
        "text": """# Daily Progress - 2026-09-30

## Progress
- Slack to Notion daily progress collection was connected.
- Google Calendar OAuth flow was prepared.

## Discussions
- Decide how confirmed calendar events should be distinguished from suggestions.
- Review how to prevent repeated Notion and Calendar writes.

## Decisions
- Keep Notion progress and Calendar automation as separate output paths.

## Projects
### Automate Jev
- Next actions: Add strict confirmation rules and persistent duplicate protection.
""",
    },
    {
        "date": "2026-10-01",
        "text": """# Daily Progress - 2026-10-01

## Progress
- Calendar decisions now require confirmed dates and concrete tasks.
- OAuth token refresh and Calendar event creation were verified.

## Discussions
- Decide how a deleted remote Calendar event should be recreated.
- Review missing end times for confirmed meetings.

## Decisions
- Use a one-hour default when a confirmed start time has no end time.

## Projects
### Automate Jev
- Next actions: Reconcile local markers with remote Calendar events and improve daily agenda generation.
""",
    },
    {
        "date": "2026-10-02",
        "text": """# Daily Progress - 2026-10-02

## Progress
- Calendar polling was configured to run every ten minutes.
- Remote event lookup was added to prevent duplicate Calendar events.

## Discussions
- Check whether a missing local marker can still cause duplicate creation.
- Collect unresolved implementation issues before the next daily meeting.

## Decisions
- Search Google Calendar by summary and date/time before creating an event.

## Projects
### Automate Jev
- Next actions: Verify continuous polling in production and fix any remaining duplicate or date-filtering issues.
""",
    },
)


def seed(memory_root: Path) -> int:
    store = LocalMemoryStore(memory_root)
    created = 0
    for item in SEED_RECORDS:
        seed_daily_progress_records(
            store,
            day=item["date"],
            content=item["text"],
            created_at=datetime.now(timezone.utc),
        )
        created += 1
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed fixed historical Daily Progress records.")
    parser.add_argument("--memory-root", default=".automate-jev/memory")
    arguments = parser.parse_args()
    created = seed(Path(arguments.memory_root))
    print(json.dumps({"seeded": created, "memory_root": arguments.memory_root}))


if __name__ == "__main__":
    main()
