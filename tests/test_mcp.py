import sys

import pytest

from automate_jev.mcp import MCPActionExecutor, MCPStdioClient
from automate_jev.models import Action


class FakeMCP:
    def __init__(self):
        self.calls = []

    async def call_tool(self, *, server, tool, arguments):
        self.calls.append((server, tool, dict(arguments)))
        return {"ok": True}


@pytest.mark.asyncio
async def test_mcp_executor_routes_registered_action_to_platform_tool():
    client = FakeMCP()
    action = Action(
        id="notion.create-page",
        domain="mcp.notion",
        verb="create",
        description="Create a page in the approved Notion database.",
        target={"mcp_server": "notion", "mcp_tool": "create_page"},
        arguments={"database_id": "approved", "title": "Daily note"},
    )

    result = await MCPActionExecutor(client).execute(action)

    assert result == {"ok": True}
    assert client.calls == [("notion", "create_page", {"database_id": "approved", "title": "Daily note"})]


@pytest.mark.asyncio
async def test_stdio_client_performs_mcp_initialize_and_tool_call(tmp_path):
    server = tmp_path / "mcp_server.py"
    server.write_text(
        """
import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get('method') == 'initialize':
        result = {'protocolVersion': '2025-06-18', 'capabilities': {}, 'serverInfo': {'name': 'test'}}
    elif message.get('method') == 'tools/call':
        result = {'content': [{'type': 'text', 'text': 'ok'}]}
    else:
        continue
    if 'id' in message:
        print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
""",
        encoding="utf-8",
    )
    client = MCPStdioClient((sys.executable, str(server)))

    result = await client.call_tool(server="notion", tool="notion-search", arguments={"query": "today"})
    await client.close()

    assert result["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_stdio_client_preserves_tool_error_detail(tmp_path):
    server = tmp_path / "mcp_error_server.py"
    server.write_text(
        """
import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get('method') == 'initialize':
        result = {'protocolVersion': '2025-06-18', 'capabilities': {}, 'serverInfo': {'name': 'test'}}
    elif message.get('method') == 'tools/call':
        result = {'isError': True, 'content': [{'type': 'text', 'text': 'missing_scope'}]}
    else:
        continue
    if 'id' in message:
        print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
""",
        encoding="utf-8",
    )
    client = MCPStdioClient((sys.executable, str(server)))

    with pytest.raises(Exception, match="missing_scope"):
        await client.call_tool(server="slack", tool="slack-search-messages", arguments={})
    await client.close()