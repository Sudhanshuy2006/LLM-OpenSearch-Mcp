"""
cli.py
======
Terminal version of the POC. Same pipeline as the web UI, printed as text.

    python poc/cli.py "How many orders were placed yesterday?"
    python poc/cli.py --demo          run the five demo questions in sequence
    python poc/cli.py --tools         just list what the MCP server exposes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import answer_question  # noqa: E402
from config import settings  # noqa: E402
from mcp_client import open_mcp  # noqa: E402

DEMO_QUESTIONS = [
    "Find all users who signed up in the last 30 days",
    "Show me the top 5 products by sales",
    "Find error logs related to payment failures",
    "How many orders were placed yesterday?",
    "Which product categories sold the most?",
]

# ANSI colours, disabled automatically when piping to a file.
_TTY = sys.stdout.isatty()


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


DIM, BOLD = "2", "1"
VIOLET, BLUE, AMBER, TEAL, RED = "35", "34", "33", "36", "31"


def print_result(result) -> None:
    for ev in result.events:
        if ev.stage == "user":
            print(c("\n  YOU        ", VIOLET) + str(ev.detail))
        elif ev.stage == "llm":
            detail = ev.detail if isinstance(ev.detail, str) else json.dumps(ev.detail)
            print(c("  MODEL      ", BLUE) + c(ev.label, DIM))
            for line in str(detail).splitlines():
                print("             " + line)
        elif ev.stage == "tool_call":
            print(c("\n  MCP CALL   ", AMBER) + c(ev.detail["tool"], BOLD))
            args = json.dumps(ev.detail["arguments"], indent=2)
            for line in args.splitlines():
                print(c("             " + line, DIM))
        elif ev.stage == "tool_result":
            ms = f" ({ev.duration_ms} ms)" if ev.duration_ms is not None else ""
            print(c("  OPENSEARCH ", TEAL) + ev.detail["summary"] + c(ms, DIM))
        elif ev.stage == "answer":
            print(c("\n  ANSWER", BOLD))
            for line in str(ev.detail).splitlines():
                print("    " + line)
        elif ev.stage == "error":
            print(c("\n  ERROR      ", RED) + str(ev.detail))

    print(
        c(
            f"\n  {result.provider}/{result.model} · {result.iterations} model turn(s) · {result.total_ms} ms\n",
            DIM,
        )
    )


async def run_raw_query(index: str, dsl_text: str) -> int:
    """Run a hand-written Query DSL body through SearchIndexTool. No LLM.

    Returns an exit code rather than raising, because raising inside the MCP
    context manager unwinds through anyio's task group and prints an
    unreadable exception group instead of the error message.
    """
    try:
        dsl = json.loads(dsl_text)
    except json.JSONDecodeError as exc:
        print(f"That is not valid JSON: {exc}", file=sys.stderr)
        return 1

    async with open_mcp() as mcp:
        print(c(f"\n  MCP CALL   SearchIndexTool on '{index}'", AMBER))
        for line in json.dumps(dsl, indent=2).splitlines():
            print(c("             " + line, DIM))

        result = await mcp.call(
            "SearchIndexTool",
            {"index": index, "query_dsl": dsl, "size": int(dsl.get("size", 10))},
        )
        data = result["data"] or {}

        if "error" in data:
            print(c(f"\n  ERROR      {data.get('message', data['error'])}\n", RED))
            return 1

        total = (data.get("hits", {}).get("total") or {}).get("value", 0)
        hits = data.get("hits", {}).get("hits", [])
        print(c(f"\n  OPENSEARCH {total} total hits, {len(hits)} documents returned\n", TEAL))

        if data.get("aggregations"):
            print(c("  AGGREGATIONS", BOLD))
            for line in json.dumps(data["aggregations"], indent=2).splitlines():
                print("    " + line)
            print()

        for hit in hits:
            print("    " + json.dumps(hit["_source"]))
        print()
        return 0


async def show_tools() -> None:
    async with open_mcp() as mcp:
        print(f"\nMCP server mode: {settings.mcp_server_mode}")
        print(f"{len(mcp.tools)} tools exposed to the model:\n")
        for t in mcp.tools:
            print(f"  {c(t.name, BOLD)}")
            print(f"    {t.description}")
            props = t.schema.get("properties") or {}
            required = set(t.schema.get("required") or [])
            for name, spec in props.items():
                mark = "*" if name in required else " "
                print(c(f"      {mark}{name}: {spec.get('type', 'any')}", DIM))
            print()


async def run(questions: list[str]) -> None:
    print(
        c(
            f"\n  cluster {settings.opensearch_url}"
            f"{'  [DEMO MODE - in-memory]' if settings.demo_mode else ''}\n"
            f"  mcp     {settings.mcp_server_mode}\n"
            f"  llm     {settings.llm_provider}",
            DIM,
        )
    )
    for q in questions:
        print(c("\n" + "─" * 74, DIM))
        result = await answer_question(q)
        print_result(result)


def main() -> None:
    p = argparse.ArgumentParser(description="OpenSearch MCP + LLM proof of concept")
    p.add_argument("question", nargs="*", help="a natural-language question")
    p.add_argument("--demo", action="store_true", help="run all five demo questions")
    p.add_argument("--tools", action="store_true", help="list the MCP tools and exit")
    p.add_argument("--query", metavar="JSON",
                   help="run a hand-written Query DSL body instead of asking a question "
                        "(use '-' to read it from stdin)")
    p.add_argument("--index", default="orders", help="index for --query (default: orders)")
    args = p.parse_args()

    if args.tools:
        asyncio.run(show_tools())
        return

    if args.query:
        dsl_text = sys.stdin.read() if args.query == "-" else args.query
        raise SystemExit(asyncio.run(run_raw_query(args.index, dsl_text)))

    if args.demo:
        asyncio.run(run(DEMO_QUESTIONS))
        return

    question = " ".join(args.question).strip()
    if not question:
        p.print_help()
        print("\nExample questions:")
        for q in DEMO_QUESTIONS:
            print(f"  python poc/cli.py \"{q}\"")
        print("\nOr write the query yourself, with no LLM involved:")
        print('  python poc/cli.py --index orders --query \'{"size":0,"query":{"match_all":{}}}\'')
        return

    asyncio.run(run([question]))


if __name__ == "__main__":
    main()
