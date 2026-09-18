"""
agent.py
========
The orchestration loop. This is the "→" in:

    User → LLM → MCP → OpenSearch → results → LLM → user

Everything the loop does is recorded into a trace, because in this project the
trace IS the deliverable. The UI renders it stage by stage so you can see which
tool the model chose, the exact Query DSL it wrote, what OpenSearch returned,
and how the answer was derived from that. Without the trace you just have a
chatbot that might be making things up.

The loop itself is only about 30 lines. That is worth noticing: the LLM is not
"integrated with" OpenSearch in any bespoke way. It is handed a list of tool
descriptions, it names one, we run it, we hand back the output, repeat.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from config import settings
from llm_router import ToolCall, build_llm
from mcp_client import open_mcp

MAX_ITERATIONS = 8


@dataclass
class TraceEvent:
    stage: str           # user | llm | tool_call | tool_result | answer | error
    label: str
    detail: Any = None
    duration_ms: int | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    question: str
    answer: str | None
    events: list[TraceEvent]
    tools_available: list[dict]
    provider: str
    model: str
    iterations: int
    total_ms: int
    error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["events"] = [asdict(e) if not isinstance(e, dict) else e for e in self.events]
        return d


def _truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters]"


async def answer_question(
    question: str,
    provider: str = "",
    model: str = "",
) -> AgentResult:
    """Run one natural-language question all the way through the pipeline."""
    started = time.time()
    events: list[TraceEvent] = [TraceEvent("user", "Question received", question)]
    tools_meta: list[dict] = []
    llm = None
    answer: str | None = None
    error: str | None = None
    iterations = 0

    try:
        llm = build_llm(provider, model)
    except Exception as exc:  # noqa: BLE001
        return AgentResult(
            question=question, answer=None, events=events + [TraceEvent("error", "LLM unavailable", str(exc))],
            tools_available=[], provider=provider or settings.llm_provider, model=model,
            iterations=0, total_ms=int((time.time() - started) * 1000), error=str(exc),
        )

    try:
        async with open_mcp() as mcp:
            tools_meta = [t.to_dict() for t in mcp.tools]
            events.append(
                TraceEvent(
                    "llm",
                    f"MCP handshake complete - {len(mcp.tools)} tools discovered",
                    {
                        "server_mode": settings.mcp_server_mode,
                        "tools": [t.name for t in mcp.tools],
                    },
                )
            )

            llm.start(question)

            for iterations in range(1, MAX_ITERATIONS + 1):
                t0 = time.time()
                step = llm.step(mcp.tools)
                llm_ms = int((time.time() - t0) * 1000)

                if step.text:
                    events.append(
                        TraceEvent(
                            "llm",
                            f"Model reasoning (turn {iterations})",
                            step.text,
                            duration_ms=llm_ms,
                        )
                    )

                if not step.tool_calls:
                    answer = step.text or "The model finished without producing an answer."
                    events.append(TraceEvent("answer", "Final answer", answer, duration_ms=llm_ms))
                    break

                for call in step.tool_calls:
                    events.append(
                        TraceEvent(
                            "tool_call",
                            f"MCP tools/call → {call.name}",
                            {"tool": call.name, "arguments": call.arguments},
                        )
                    )

                    t1 = time.time()
                    result = await mcp.call(call.name, call.arguments)
                    tool_ms = int((time.time() - t1) * 1000)

                    events.append(
                        TraceEvent(
                            "tool_result",
                            f"OpenSearch response ← {call.name}",
                            {
                                "tool": call.name,
                                "is_error": result["is_error"],
                                "parsed": result["data"],
                                "raw": _truncate(result["text"]),
                                "summary": _summarise_tool_result(call.name, result["data"]),
                            },
                            duration_ms=tool_ms,
                        )
                    )

                    llm.add_tool_result(call, _truncate(result["text"], 12000))
            else:
                error = f"Stopped after {MAX_ITERATIONS} tool iterations without a final answer."
                events.append(TraceEvent("error", "Iteration limit reached", error))

    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        events.append(TraceEvent("error", "Pipeline failed", error))

    return AgentResult(
        question=question,
        answer=answer,
        events=events,
        tools_available=tools_meta,
        provider=getattr(llm, "provider", provider or settings.llm_provider),
        model=getattr(llm, "model", model),
        iterations=iterations,
        total_ms=int((time.time() - started) * 1000),
        error=error,
    )


def _summarise_tool_result(tool: str, data: Any) -> str:
    """A one-line human summary of a tool result, for the trace header."""
    if not isinstance(data, dict):
        return "non-JSON response"
    if "error" in data:
        return f"error: {data.get('message', data['error'])}"
    if tool == "ListIndexTool":
        names = [i.get("index") for i in data.get("indices", [])]
        return f"{len(names)} indices: {', '.join(str(n) for n in names)}"
    if tool == "IndexMappingTool":
        for _, body in data.items():
            props = (body or {}).get("mappings", {}).get("properties", {})
            if props:
                return f"{len(props)} fields: {', '.join(props)}"
        return "mapping returned"
    if tool == "GetIndexStatsTool":
        return f"{data.get('doc_count')} documents"
    if tool == "SearchIndexTool":
        total = (data.get("hits", {}).get("total") or {}).get("value")
        n = len(data.get("hits", {}).get("hits", []))
        agg = f", {len(data['aggregations'])} aggregation(s)" if data.get("aggregations") else ""
        return f"{total} total hits, {n} documents returned{agg}"
    return "ok"


if __name__ == "__main__":
    import asyncio
    import sys

    q = " ".join(sys.argv[1:]) or "How many orders were placed yesterday?"
    res = asyncio.run(answer_question(q))
    print(json.dumps(res.to_dict(), indent=2, default=str)[:3000])
