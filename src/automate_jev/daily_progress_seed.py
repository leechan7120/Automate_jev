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
        "text": """# 일일 진행 상황 - 2026-09-30

    ## 진행 상황
    - Slack에서 Notion으로 일일 진행 상황을 수집하는 연결을 구성했다.
    - Google Calendar OAuth 흐름을 준비했다.

    ## 논의 사항
    - 확정된 캘린더 일정과 제안 일정을 구분하는 방법을 결정해야 한다.
    - Notion과 Calendar에 같은 내용이 반복 기록되지 않도록 확인해야 한다.

    ## 결정 사항
    - Notion 진행 상황 기록과 Calendar 자동화를 별도 출력 경로로 유지한다.

    ## 프로젝트
    ### Automate Jev
    - 후속 작업: 엄격한 확인 규칙과 영구 중복 방지 기능을 추가한다.
""",
    },
    {
        "date": "2026-10-01",
        "text": """# 일일 진행 상황 - 2026-10-01

    ## 진행 상황
    - Calendar 일정 생성은 이제 확정된 날짜와 구체적인 업무가 있어야 진행된다.
    - OAuth 토큰 갱신과 Calendar 일정 생성을 확인했다.

    ## 논의 사항
    - 원격 Calendar에서 삭제된 일정을 어떻게 재생성할지 결정해야 한다.
    - 확정된 회의에 종료 시간이 없는 경우를 검토해야 한다.

    ## 결정 사항
    - 확정된 시작 시간에 종료 시간이 없으면 한 시간을 기본값으로 사용한다.

    ## 프로젝트
### Automate Jev
    - 후속 작업: 로컬 marker와 원격 Calendar 일정을 대조하고 일일 agenda 생성을 개선한다.
""",
    },
    {
        "date": "2026-10-02",
        "text": """# 일일 진행 상황 - 2026-10-02

    ## 진행 상황
    - Calendar polling을 10분마다 실행하도록 구성했다.
    - Calendar 일정 중복 생성을 막기 위해 원격 일정 조회를 추가했다.

    ## 논의 사항
    - 로컬 marker가 없을 때에도 중복 생성이 발생할 수 있는지 확인해야 한다.
    - 다음 일일 회의 전에 해결되지 않은 구현 이슈를 모아야 한다.

    ## 결정 사항
    - 일정을 생성하기 전에 제목과 날짜/시간으로 Google Calendar를 검색한다.

    ## 프로젝트
### Automate Jev
    - 후속 작업: 운영 환경에서 지속 polling을 확인하고 남은 중복 또는 날짜 필터링 문제를 수정한다.
""",
    },
)


def seed(memory_root: Path) -> int:
    store = LocalMemoryStore(memory_root)
    created = 0
    for item in SEED_RECORDS:
        path = memory_root / "episodic" / f"slack-daily-{item['date']}.md"
        if path.exists():
            path.unlink()
        seed_daily_progress_records(
            store,
            day=item["date"],
            content="\n".join(line.lstrip() for line in item["text"].splitlines()).strip(),
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
