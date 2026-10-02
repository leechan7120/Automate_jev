from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re
from typing import Any, Mapping

from .memory import LocalMemoryStore, MemoryKind, MemoryRecord
from .models import ContractError
from .notion import NotionMCP
from .progress_summarizer import ProjectSummary, ProgressSummarizer, ProgressSummary
from .slack import SlackMCP, SlackMessage


@dataclass(frozen=True, slots=True)
class DailyProgress:
    day: date
    messages: tuple[SlackMessage, ...]
    content: str
    memory: MemoryRecord
    version: int
    notion_result: Mapping[str, Any] | None

    def payload(self) -> dict[str, object]:
        return {
            "date": self.day.isoformat(),
            "message_count": len(self.messages),
            "content": self.content,
            "memory": self.memory.payload(),
            "version": self.version,
            "notion": dict(self.notion_result) if self.notion_result else None,
        }


@dataclass(slots=True)
class DailyProgressService:
    slack: SlackMCP
    notion: NotionMCP
    memory: LocalMemoryStore
    notion_parent_id: str = ""
    project_root_page_id: str = ""
    publish_enabled: bool = False
    summarizer: ProgressSummarizer | None = None

    async def collect_and_publish(
        self,
        day: date,
        *,
        query: str = "",
    ) -> DailyProgress:
        messages = await self.slack.search_messages(day, query)
        root = None
        if self.publish_enabled and self.project_root_page_id:
            root = await self.notion.fetch(self.project_root_page_id)
        summary = _summary_from_messages(messages)
        if self.summarizer is not None:
            try:
                summary = await self.summarizer.summarize(
                    messages=messages,
                    existing_root=root.text if root else "",
                )
            except (RuntimeError, ContractError):
                pass
        version = self._next_version(day)
        content = _format_progress(day, messages, version, summary)
        record = MemoryRecord(
            kind=MemoryKind.EPISODIC,
            scope=f"slack-daily-{day.isoformat()}",
            text=content,
            source=("slack", day.isoformat()),
            confidence=0.85,
            created_at=datetime.now(timezone.utc),
        )
        self.memory.upsert(record)
        notion_result: Mapping[str, Any] | None = None
        if self.publish_enabled:
            if not self.notion_parent_id:
                raise ContractError("NOTION_DAILY_PROGRESS_PARENT_ID is not configured")
            marker = self.memory.root / "daily-progress-published" / f"{day.isoformat()}.json"
            marker_data = _read_marker(marker)
            page_id = str(marker_data.get("page_id", "")).strip()
            if page_id:
                notion_result = await self.notion.update_page(
                    page_id=page_id,
                    title=f"Daily Progress - {day.isoformat()}",
                    content=content,
                )
                notion_result = {"status": "updated", "version": version, **notion_result}
            elif marker.exists():
                notion_result = {"status": "already_published", "version": version}
            else:
                notion_result = await self.notion.publish_routine(
                    title=f"Daily Progress - {day.isoformat()}",
                    content=content,
                    parent_id=self.notion_parent_id,
                )
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    json.dumps({"date": day.isoformat(), "version": version, "page_id": _page_id(notion_result)}),
                    encoding="utf-8",
                )
            if self.project_root_page_id:
                if root is None:
                    root = await self.notion.fetch(self.project_root_page_id)
                root_content = _update_root(root.text, day, version, summary)
                root_result = await self.notion.update_page(
                    page_id=self.project_root_page_id,
                    title=root.title,
                    content=root_content,
                )
                notion_result = {**notion_result, "root": root_result}
                project_results = await self._publish_projects(day, version, summary.projects)
                if project_results:
                    notion_result = {**notion_result, "projects": project_results}
        return DailyProgress(day, messages, content, record, version, notion_result)

    async def _publish_projects(
        self,
        day: date,
        version: int,
        projects: tuple[ProjectSummary, ...],
    ) -> list[Mapping[str, Any]]:
        results: list[Mapping[str, Any]] = []
        for project in projects:
            slug = _project_slug(project.name)
            marker = self.memory.root / "project-pages" / f"{slug}.json"
            marker_data = _read_marker(marker)
            page_id = str(marker_data.get("page_id", "")).strip()
            if page_id:
                existing = await self.notion.fetch(page_id)
                content = _append_project_update(existing.text, day, version, project)
                result = await self.notion.update_page(
                    page_id=page_id,
                    title=f"Project - {project.name}",
                    content=content,
                )
                results.append({"name": project.name, "status": "updated", **result})
            else:
                content = _project_content(day, version, project)
                result = await self.notion.publish_routine(
                    title=f"Project - {project.name}",
                    content=content,
                    parent_id=self.project_root_page_id,
                )
                page_id = _page_id(result)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    json.dumps({"name": project.name, "page_id": page_id}),
                    encoding="utf-8",
                )
                results.append({"name": project.name, "status": "created", **result})
        return results

    def _next_version(self, day: date) -> int:
        path = self.memory.root / "daily-progress-versions" / f"{day.isoformat()}.json"
        data = _read_marker(path)
        version = int(data.get("version", 0)) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": day.isoformat(), "version": version}), encoding="utf-8")
        return version


def _format_progress(
    day: date,
    messages: tuple[SlackMessage, ...],
    version: int,
    summary: ProgressSummary,
) -> str:
    lines = [f"# Daily Progress - {day.isoformat()}", "", f"- Document version: v{version}"]
    for title, bullets in (
        ("Progress", summary.progress),
        ("Discussions", summary.discussions),
        ("Decisions", summary.decisions),
    ):
        lines.extend(("", f"## {title}"))
        lines.extend(f"- {bullet}" for bullet in bullets)
        if not bullets:
            lines.append("- None identified.")
    evidence = tuple(message.line() for message in messages) or ("- No Slack messages matched this day.",)
    lines.extend(("", "## Evidence", *evidence))
    lines.extend(("", "## Source", "- Slack MCP search", "- Gemini summary when enabled"))
    return "\n".join(lines)[:4_000]


def _classify(messages: tuple[SlackMessage, ...]) -> dict[str, tuple[SlackMessage, ...]]:
    sections: dict[str, list[SlackMessage]] = {"Progress": [], "Discussions": [], "Decisions": []}
    for message in messages:
        text = message.text.lower()
        if re.search(r"\b(decision|decided|결정|확정|합의)\b", text):
            section = "Decisions"
        elif re.search(r"\b(discuss|discussion|question|review|논의|질문|검토|의견)\b", text):
            section = "Discussions"
        else:
            section = "Progress"
        sections[section].append(message)
    return {key: tuple(value) for key, value in sections.items()}


def _summary_from_messages(messages: tuple[SlackMessage, ...]) -> ProgressSummary:
    sections = _classify(messages)
    return ProgressSummary(
        progress=tuple(message.text for message in sections["Progress"]),
        discussions=tuple(message.text for message in sections["Discussions"]),
        decisions=tuple(message.text for message in sections["Decisions"]),
    )


def _project_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9가-힣._-]+", "-", name.strip().lower()).strip("-")
    return (slug or "unnamed-project")[:96]


def _project_content(day: date, version: int, project: ProjectSummary) -> str:
    return _project_update(day, version, project)


def _append_project_update(existing: str, day: date, version: int, project: ProjectSummary) -> str:
    block = _project_update(day, version, project)
    marker = re.compile(
        rf"<!-- project-update:{re.escape(day.isoformat())} -->.*?<!-- /project-update -->",
        re.DOTALL,
    )
    if marker.search(existing):
        return marker.sub(block, existing, count=1)[:4_000]
    return f"{existing.rstrip()}\n\n{block}"[:4_000] if existing.strip() else block


def _project_update(day: date, version: int, project: ProjectSummary) -> str:
    lines = [
        f"<!-- project-update:{day.isoformat()} -->",
        f"## {day.isoformat()} (v{version})",
        "### Overview",
        project.overview or "No project overview identified.",
    ]
    for title, bullets in (
        ("Progress", project.progress),
        ("Discussions", project.discussions),
        ("Decisions", project.decisions),
        ("Next actions", project.next_actions),
    ):
        lines.extend((f"### {title}", *(f"- {bullet}" for bullet in bullets)))
        if not bullets:
            lines.append("- None identified.")
    lines.append("<!-- /project-update -->")
    return "\n".join(lines)


def _read_marker(path: Any) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _page_id(result: Mapping[str, Any]) -> str:
    direct = str(result.get("id", result.get("page_id", ""))).strip()
    if direct:
        return direct
    for key in ("pages", "results", "data"):
        items = result.get(key)
        if isinstance(items, (list, tuple)) and items and isinstance(items[0], Mapping):
            nested = _page_id(items[0])
            if nested:
                return nested
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return _page_id(structured)
    return ""


def _update_root(existing: str, day: date, version: int, summary: ProgressSummary) -> str:
    block = [
        f"<!-- daily-progress:{day.isoformat()} -->",
        f"## {day.isoformat()} (v{version})",
        "### Progress",
        *(f"- {bullet}" for bullet in summary.progress),
        "### Discussions",
        *(f"- {bullet}" for bullet in summary.discussions),
        "### Decisions",
        *(f"- {bullet}" for bullet in summary.decisions),
        "### Projects identified",
        *(f"- {project.name}: {project.overview}" for project in summary.projects),
        *((["### Project overview", summary.project_overview] if summary.project_overview else [])),
        "<!-- /daily-progress -->",
    ]
    marker = re.compile(
        rf"<!-- daily-progress:{re.escape(day.isoformat())} -->.*?<!-- /daily-progress -->",
        re.DOTALL,
    )
    if marker.search(existing):
        content = marker.sub("\n".join(block), existing, count=1)
    else:
        block_text = "\n".join(block)
        content = f"{existing.rstrip()}\n\n{block_text}" if existing.strip() else block_text
    return content[:4_000]
