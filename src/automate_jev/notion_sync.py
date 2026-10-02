from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .mcp import MCPStdioClient
from .memory import LocalMemoryStore
from .notion import NotionMCP


async def sync(
    command: tuple[str, ...],
    query: str,
    memory_root: Path,
    *,
    search_tool: str,
    server: str,
) -> None:
    client = MCPStdioClient(command)
    try:
        notion = NotionMCP(client, server=server, search_tool=search_tool)
        records = await notion.sync_to_memory(query, LocalMemoryStore(memory_root))
        print({"server": server, "query": query, "records": [record.payload() for record in records]})
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Notion pages through an MCP server into local memory.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--memory-root", type=Path, default=Path(".automate-jev") / "memory")
    parser.add_argument("--server", default="notion")
    parser.add_argument("--search-tool", default="notion-search")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="MCP server command after --")
    arguments = parser.parse_args()
    command = tuple(item for item in arguments.command if item != "--")
    if not command:
        parser.error("an MCP server command is required after --")
    asyncio.run(sync(command, arguments.query, arguments.memory_root, search_tool=arguments.search_tool, server=arguments.server))


if __name__ == "__main__":
    main()