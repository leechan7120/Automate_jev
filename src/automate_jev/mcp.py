from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
from itertools import count
from typing import Any, Mapping, Protocol

from .models import Action, ContractError


class MCPToolClient(Protocol):
    async def call_tool(
        self,
        *,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(slots=True)
class MCPStdioClient:
    """Minimal MCP JSON-RPC client for a local server process."""

    command: tuple[str, ...]
    startup_timeout: float = 60.0
    request_timeout: float = 30.0
    _process: asyncio.subprocess.Process | None = None
    _ids: Any = None

    def __post_init__(self) -> None:
        if not self.command:
            raise ContractError("MCP command must not be empty")
        self._ids = count(1)

    async def start(self) -> None:
        if self._process is not None:
            return
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "automate-jev", "version": "0.1.0"},
            },
            timeout=self.startup_timeout,
        )
        await self._notify("notifications/initialized", {})

    async def close(self) -> None:
        if self._process is None:
            return
        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        self._process = None

    async def call_tool(
        self,
        *,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not server.strip() or not tool.strip():
            raise ContractError("MCP server and tool are required")
        await self.start()
        response = await self._request(
            "tools/call",
            {"name": tool, "arguments": dict(arguments)},
            timeout=self.request_timeout,
        )
        if response.get("isError") is True:
            detail = _tool_error_detail(response)
            raise ContractError(f"MCP tool failed: {tool}: {detail}")
        return response

    async def _request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Mapping[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise ContractError("MCP server is not running")
        request_id = next(self._ids)
        process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}) + "\n").encode())
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            if not line:
                raise ContractError("MCP server closed its stdout")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                raise ContractError("MCP server returned invalid JSON") from error
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ContractError(f"MCP request failed: {message['error']}")
            result = message.get("result")
            if not isinstance(result, Mapping):
                raise ContractError("MCP result must be an object")
            return result

    async def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise ContractError("MCP server is not running")
        process.stdin.write((json.dumps({"jsonrpc": "2.0", "method": method, "params": dict(params)}) + "\n").encode())
        await process.stdin.drain()


def _tool_error_detail(response: Mapping[str, Any]) -> str:
    content = response.get("content")
    if isinstance(content, (list, tuple)):
        text = " ".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, Mapping) and item.get("text")
        ).strip()
        if text:
            return text[:500]
    structured = response.get("structuredContent")
    if isinstance(structured, Mapping):
        error = structured.get("error", structured.get("message", ""))
        if error:
            return str(error)[:500]
    return "unknown MCP tool error"


@dataclass(slots=True)
class MCPActionExecutor:
    """Routes registered actions to a Notion, Slack, or other MCP tool server."""

    client: MCPToolClient

    async def execute(self, action: Action) -> Mapping[str, Any]:
        server = action.target.get("mcp_server")
        tool = action.target.get("mcp_tool")
        if not isinstance(server, str) or not server.strip():
            raise ContractError("MCP action target must define mcp_server")
        if not isinstance(tool, str) or not tool.strip():
            raise ContractError("MCP action target must define mcp_tool")
        result = await self.client.call_tool(
            server=server,
            tool=tool,
            arguments=action.arguments,
        )
        if not isinstance(result, Mapping):
            raise ContractError("MCP tool result must be an object")
        return result