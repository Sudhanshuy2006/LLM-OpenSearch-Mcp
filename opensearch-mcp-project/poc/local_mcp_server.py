"""
local_mcp_server.py
===================
A bundled OpenSearch MCP server, speaking MCP over stdio.

The upstream package `opensearch-mcp-server-py` is the real thing and this
POC can use it (MCP_SERVER_MODE=official). This file exists for two reasons:

  1. It is readable. The upstream server is ~40 tools across a dozen modules;
     this is one file you can read top to bottom to understand exactly what an
     MCP server is: a process that answers "what tools do you have?" and
     "run this tool with these arguments", over stdin/stdout.
  2. It runs in DEMO_MODE against the in-memory engine, so the whole pipeline
     is demonstrable without Docker or a network.

The tool names deliberately match the upstream server's names exactly
(ListIndexTool, IndexMappingTool, SearchIndexTool, GetIndexStatsTool), so
switching MCP_SERVER_MODE between "local" and "official" changes nothing
about the client, the prompts or the UI.

Run it by hand to inspect it:
    python poc/local_mcp_server.py
(then type MCP JSON-RPC frames on stdin -- normally the client does this)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from config import settings

# --------------------------------------------------------------------------
# Backend: real OpenSearch, or the in-memory demo engine
# --------------------------------------------------------------------------


class RealBackend:
    """Thin adapter over opensearch-py, matching DemoOpenSearch's interface."""

    def __init__(self) -> None:
        from opensearchpy import OpenSearch

        self.client = OpenSearch(
            hosts=[settings.opensearch_url],
            http_auth=(settings.opensearch_username, settings.opensearch_password),
            use_ssl=settings.opensearch_url.startswith("https"),
            verify_certs=settings.opensearch_verify_certs,
            ssl_show_warn=False,
            timeout=30,
        )

    def info(self) -> dict:
        return self.client.info()

    def list_indices(self) -> list[dict]:
        rows = self.client.cat.indices(format="json")
        # Hide OpenSearch's internal/system indices from the LLM.
        return [r for r in rows if not r.get("index", "").startswith(".")]

    def get_mapping(self, index: str) -> dict:
        return self.client.indices.get_mapping(index=index)

    def index_stats(self, index: str) -> dict:
        stats = self.client.indices.stats(index=index)
        primaries = stats["indices"][index]["primaries"]
        return {
            "index": index,
            "doc_count": primaries["docs"]["count"],
            "size_in_bytes": primaries["store"]["size_in_bytes"],
        }

    def search(self, index: str, body: dict) -> dict:
        return self.client.search(index=index, body=body)


def make_backend():
    return RealBackend() if not settings.demo_mode else __import__(
        "demo_engine", fromlist=["DemoOpenSearch"]
    ).DemoOpenSearch()


backend = make_backend()
server = Server("opensearch-mcp")


# --------------------------------------------------------------------------
# Tool catalogue
#
# Each entry is what the MCP client receives from list_tools(). The
# `description` and `inputSchema` are the ONLY things the LLM ever sees about
# these tools, which is why they are written as instructions to a model rather
# than as terse developer docs.
# --------------------------------------------------------------------------

TOOLS: list[types.Tool] = [
    types.Tool(
        name="ListIndexTool",
        description=(
            "Lists the indices available in the OpenSearch cluster, with document "
            "counts and health. Call this first when you do not know which index "
            "holds the data the user is asking about."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "index": {
                    "type": "string",
                    "description": "Optional index name or wildcard pattern to filter by.",
                }
            },
        },
    ),
    types.Tool(
        name="IndexMappingTool",
        description=(
            "Returns the field mappings (the schema) of an index: every field name "
            "and its type. ALWAYS call this before SearchIndexTool so you use real "
            "field names and correct types. Fields of type 'keyword' are used "
            "directly in term queries and aggregations. Fields of type 'text' are "
            "used in match queries, and aggregated on their '.keyword' subfield "
            "if one exists."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "index": {"type": "string", "description": "The index to describe."}
            },
            "required": ["index"],
        },
    ),
    types.Tool(
        name="SearchIndexTool",
        description=(
            "Runs an OpenSearch Query DSL request against one index and returns the "
            "matching documents and any aggregation results. "
            "PREREQUISITE: know the index mapping first (IndexMappingTool). "
            "For counting or statistics, set size to 0 and use an aggregation "
            "instead of paging through documents. "
            "For date ranges you may use date math such as "
            '{"range":{"order_date":{"gte":"now-30d/d","lt":"now/d"}}}.'
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "index": {"type": "string", "description": "The index to search."},
                "query_dsl": {
                    "type": "object",
                    "description": (
                        "The full request body: may contain 'query', 'aggs', 'sort', "
                        "'size' and 'from'."
                    ),
                },
                "size": {
                    "type": "integer",
                    "description": "Number of documents to return (0-100). Use 0 for pure aggregations.",
                    "default": 10,
                },
            },
            "required": ["index", "query_dsl"],
        },
    ),
    types.Tool(
        name="GetIndexStatsTool",
        description=(
            "Returns document count and storage size for an index. Use this for "
            "'how many documents are in X' style questions, which is cheaper than "
            "running a search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "index": {"type": "string", "description": "The index to get stats for."}
            },
            "required": ["index"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """MCP 'tools/list'. This is how the LLM discovers what it can do."""
    enabled = set(settings.enabled_tools)
    return [t for t in TOOLS if not enabled or t.name in enabled]


# --------------------------------------------------------------------------
# Tool execution
# --------------------------------------------------------------------------


def _run_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "ListIndexTool":
        rows = backend.list_indices()
        pattern = args.get("index")
        if pattern:
            prefix = pattern.rstrip("*")
            rows = [r for r in rows if r.get("index", "").startswith(prefix)]
        return {
            "indices": [
                {"index": r.get("index"), "doc_count": r.get("docs.count"), "health": r.get("health")}
                for r in rows
            ]
        }

    if name == "IndexMappingTool":
        return backend.get_mapping(args["index"])

    if name == "GetIndexStatsTool":
        return backend.index_stats(args["index"])

    if name == "SearchIndexTool":
        index = args["index"]
        body = dict(args.get("query_dsl") or {})
        # `size` may arrive as its own argument or inside the body. The explicit
        # argument wins, but only if the body did not already set it, because a
        # body of {"size": 0, "aggs": ...} is an intentional aggregation-only query.
        if "size" not in body and "size" in args:
            body["size"] = args["size"]
        body["size"] = min(int(body.get("size", 10)), 100)
        return backend.search(index, body)

    raise ValueError(f"Unknown tool: {name}")


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    """MCP 'tools/call'. Executes one tool and returns its result as text."""
    args = arguments or {}
    try:
        result = _run_tool(name, args)
        payload = json.dumps(result, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        # Errors are returned as content, not raised. The LLM can read the
        # message and correct its next attempt -- that self-correction loop is
        # a large part of why MCP tool calling works well in practice.
        payload = json.dumps(
            {"error": type(exc).__name__, "message": str(exc)}, indent=2
        )
    return [types.TextContent(type="text", text=payload)]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
