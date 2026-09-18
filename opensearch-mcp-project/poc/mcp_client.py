"""
mcp_client.py
=============
The MCP client half of the protocol.

Responsibilities:
  * launch the MCP server as a subprocess and speak MCP over its stdin/stdout
  * perform the MCP handshake (initialize)
  * discover the server's tools (tools/list)
  * execute tools on demand (tools/call)
  * translate MCP tool schemas into the JSON-schema "tools" format that
    OpenAI-compatible and Anthropic chat APIs expect

Note that the client knows nothing about OpenSearch. That is the entire point
of MCP: swap the server for a Postgres or Jira MCP server and this file is
unchanged.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import settings

POC_DIR = Path(__file__).resolve().parent


def server_parameters() -> StdioServerParameters:
    """Describe how to start the MCP server subprocess."""
    if settings.mcp_server_mode == "official":
        # The upstream server, fetched and run by uv. Requires `uvx` on PATH.
        return StdioServerParameters(
            command="uvx",
            args=["opensearch-mcp-server-py", "--transport", "stdio"],
            env=settings.mcp_env(),
        )
    # The bundled server in this repo.
    return StdioServerParameters(
        command=sys.executable,
        args=[str(POC_DIR / "local_mcp_server.py")],
        env=settings.mcp_env(),
    )


class McpTool:
    """One discovered tool, plus conversions to each LLM vendor's format."""

    def __init__(self, name: str, description: str, schema: dict):
        self.name = name
        self.description = description or ""
        self.schema = schema or {"type": "object", "properties": {}}

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
        }

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "schema": self.schema}


class McpConnection:
    """A live MCP session. Use via `open_mcp()`."""

    def __init__(self, session: ClientSession):
        self._session = session
        self.tools: list[McpTool] = []

    async def discover(self) -> list[McpTool]:
        response = await self._session.list_tools()
        self.tools = [
            McpTool(
                name=t.name,
                description=t.description or "",
                schema=getattr(t, "inputSchema", None) or {"type": "object", "properties": {}},
            )
            for t in response.tools
        ]
        return self.tools

    async def call(self, name: str, arguments: dict[str, Any]) -> dict:
        """Run a tool and return {'text': raw, 'data': parsed-or-None}."""
        result = await self._session.call_tool(name, arguments)
        text = "\n".join(
            block.text for block in result.content if getattr(block, "text", None)
        )
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = None
        return {"text": text, "data": data, "is_error": bool(getattr(result, "isError", False))}


@asynccontextmanager
async def open_mcp():
    """Start the MCP server, complete the handshake, discover tools, yield."""
    params = server_parameters()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            connection = McpConnection(session)
            await connection.discover()
            yield connection


if __name__ == "__main__":
    # Quick smoke test: what tools does the configured server expose?
    import asyncio

    async def _main() -> None:
        async with open_mcp() as mcp:
            print(f"MCP server mode: {settings.mcp_server_mode}")
            print(f"Discovered {len(mcp.tools)} tools:\n")
            for tool in mcp.tools:
                print(f"  {tool.name}")
                print(f"    {tool.description[:110]}")
                print(f"    args: {list((tool.schema.get('properties') or {}))}\n")

    asyncio.run(_main())
