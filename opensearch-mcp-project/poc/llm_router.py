"""
llm_router.py
=============
One interface, four LLM backends, plus an offline fallback.

    anthropic   Claude          (ANTHROPIC_API_KEY)
    openai      GPT             (OPENAI_API_KEY)
    groq        Llama/GPT-OSS   (GROQ_API_KEY)   - has a free tier
    xai         Grok            (XAI_API_KEY)
    rules       no API key, no network - a deterministic planner

The `rules` backend matters more than it looks. It implements the same
contract as a real LLM -- it receives the tool schemas, decides which tool to
call next, reads the tool result, and writes a final answer -- but it plans
with regular expressions instead of a neural network. That makes the POC
runnable and gradeable with zero credentials, and it makes the difference
between "the plumbing is broken" and "the model chose badly" easy to see:
if `rules` works and `anthropic` does not, the problem is the prompt, not
the MCP wiring.

Every backend returns the same LLMStep object, so agent.py never branches
on which provider is in use.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings

SYSTEM_PROMPT = """You are a data analyst with read-only access to an OpenSearch cluster through MCP tools.

The cluster holds three indices:
  users     - customer accounts (user_id, name, email, city, plan, signup_date)
  orders    - purchases (order_id, user_id, product, category, amount, quantity, status, order_date)
  app_logs  - application logs (timestamp, level, service, message, trace_id, latency_ms)

How to work:
1. If you are unsure which index holds the answer, call ListIndexTool.
2. Before writing any query, call IndexMappingTool on the index you intend to
   search. Use the exact field names and types it reports.
3. Use SearchIndexTool to run the query.
   - For counts, totals, rankings and "top N" questions, set size to 0 and use
     an aggregation. Do not fetch documents and count them yourself.
   - For "show me the records" questions, return documents with a small size.
   - For relative dates use OpenSearch date math: "now-30d/d", "now-1d/d", "now/d".
     "Yesterday" means {"gte": "now-1d/d", "lt": "now/d"}.
   - keyword fields go in term/terms queries; text fields go in match queries.
4. If a tool returns an error, read the message, fix your query, and try again.
5. When you have the data, answer the user in plain language. State the actual
   numbers. Do not show raw JSON unless the user asked for it. If the result is
   empty, say so plainly rather than guessing.

Be concise. Never invent data that is not in a tool result."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMStep:
    """One turn of the model: some text, and/or some tool calls."""
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


class BaseLLM:
    provider = "base"

    def __init__(self, model: str = ""):
        self.model = model or self.default_model
        self.messages: list[dict] = []

    def start(self, question: str) -> None:
        self.messages = [{"role": "user", "content": question}]

    def step(self, tools: list) -> LLMStep:
        raise NotImplementedError

    def add_tool_result(self, call: ToolCall, content: str) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------
# OpenAI-compatible providers (OpenAI, Groq, xAI)
# --------------------------------------------------------------------------


class OpenAICompatLLM(BaseLLM):
    default_model = "gpt-4o-mini"
    base_url = None
    env_key = "OPENAI_API_KEY"

    def __init__(self, model: str = ""):
        super().__init__(model)
        from openai import OpenAI

        api_key = os.environ.get(self.env_key)
        if not api_key:
            raise RuntimeError(
                f"{self.env_key} is not set. Export it, or run with LLM_PROVIDER=rules."
            )
        kwargs = {"api_key": api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self.client = OpenAI(**kwargs)

    def step(self, tools: list) -> LLMStep:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + self.messages,
            tools=[t.to_openai() for t in tools],
            tool_choice="auto",
            temperature=0,
        )
        msg = response.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments or "{}"))
            for tc in (msg.tool_calls or [])
        ]
        self.messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                **({"tool_calls": [tc.model_dump() for tc in msg.tool_calls]} if msg.tool_calls else {}),
            }
        )
        return LLMStep(text=msg.content, tool_calls=calls, raw=msg)

    def add_tool_result(self, call: ToolCall, content: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})


class OpenAILLM(OpenAICompatLLM):
    provider = "openai"
    default_model = "gpt-4o-mini"
    env_key = "OPENAI_API_KEY"


class GroqLLM(OpenAICompatLLM):
    provider = "groq"
    default_model = "llama-3.3-70b-versatile"
    base_url = "https://api.groq.com/openai/v1"
    env_key = "GROQ_API_KEY"


class XaiLLM(OpenAICompatLLM):
    provider = "xai"
    default_model = "grok-4-fast"
    base_url = "https://api.x.ai/v1"
    env_key = "XAI_API_KEY"


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


class AnthropicLLM(BaseLLM):
    provider = "anthropic"
    default_model = "claude-sonnet-4-5"

    def __init__(self, model: str = ""):
        super().__init__(model)
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it, or run with LLM_PROVIDER=rules."
            )
        self.client = anthropic.Anthropic(api_key=api_key)

    def step(self, tools: list) -> LLMStep:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=self.messages,
            tools=[t.to_anthropic() for t in tools],
        )
        text_parts, calls = [], []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        self.messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
        return LLMStep(text="\n".join(text_parts) or None, tool_calls=calls, raw=response)

    def add_tool_result(self, call: ToolCall, content: str) -> None:
        # Anthropic groups consecutive tool results into one user message.
        if self.messages and self.messages[-1]["role"] == "user" and isinstance(
            self.messages[-1]["content"], list
        ):
            self.messages[-1]["content"].append(
                {"type": "tool_result", "tool_use_id": call.id, "content": content}
            )
        else:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": call.id, "content": content}
                    ],
                }
            )


# --------------------------------------------------------------------------
# Rules provider - deterministic, offline
# --------------------------------------------------------------------------

_INDEX_HINTS = {
    "users": ["user", "signup", "sign up", "signed up", "customer", "account", "member", "registered"],
    "orders": ["order", "sale", "sales", "revenue", "product", "purchase", "bought", "spend", "amount"],
    "app_logs": ["log", "error", "exception", "failure", "failed", "warn", "service", "latency", "trace"],
}


def _pick_index(question: str) -> str:
    q = question.lower()
    scores = {
        index: sum(1 for hint in hints if hint in q) for index, hints in _INDEX_HINTS.items()
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] else "orders"


def _date_range(question: str) -> dict | None:
    """Translate English time expressions into OpenSearch date math."""
    q = question.lower()
    if "yesterday" in q:
        return {"gte": "now-1d/d", "lt": "now/d"}
    if "today" in q:
        return {"gte": "now/d"}
    m = re.search(r"last\s+(\d+)\s*(day|days|week|weeks|month|months)", q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
        return {"gte": f"now-{days}d/d"}
    if "last week" in q or "past week" in q:
        return {"gte": "now-7d/d"}
    if "last month" in q or "past month" in q:
        return {"gte": "now-30d/d"}
    return None


_DATE_FIELD = {"users": "signup_date", "orders": "order_date", "app_logs": "timestamp"}


class RulesLLM(BaseLLM):
    """A regex planner that mimics an LLM's tool-calling behaviour."""

    provider = "rules"
    default_model = "rules-planner-v1"

    def __init__(self, model: str = ""):
        super().__init__(model)
        self.question = ""
        self.stage = 0
        self.index = "orders"
        self.results: dict[str, Any] = {}
        self.plan_note = ""

    def start(self, question: str) -> None:
        self.question = question
        self.stage = 0
        self.index = _pick_index(question)
        self.results = {}
        self.messages = [{"role": "user", "content": question}]

    # -- planning ---------------------------------------------------------
    def _build_dsl(self) -> tuple[dict, str]:
        """Return (query_dsl, human explanation of the plan)."""
        q = self.question.lower()
        index = self.index
        date_field = _DATE_FIELD[index]
        rng = _date_range(q)

        filters: list[dict] = []
        if rng:
            filters.append({"range": {date_field: {**rng, "format": "strict_date_optional_time||epoch_millis"}}})

        wants_count = bool(re.search(r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal number\b", q))
        wants_top = bool(re.search(r"\btop\b|\bbest\b|\bmost\b|\branking\b|\brank\b", q))

        # --- top N by a metric -------------------------------------------
        if wants_top and index == "orders":
            m = re.search(r"top\s+(\d+)", q)
            size = int(m.group(1)) if m else 5
            by_revenue = bool(re.search(r"sales|revenue|amount|value|money", q))
            group_field = "category" if "categor" in q else "product"
            filters.append({"term": {"status": "completed"}})
            dsl = {
                "size": 0,
                "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
                "aggs": {
                    "top_items": {
                        "terms": {
                            "field": group_field,
                            "size": size,
                            "order": {"metric": "desc"} if by_revenue else {"_count": "desc"},
                        },
                        "aggs": {"metric": {"sum": {"field": "amount"}}},
                    }
                },
            }
            metric = "total sales value" if by_revenue else "order count"
            return dsl, f"group completed orders by {group_field}, rank by {metric}, keep top {size}"

        # --- log searches -------------------------------------------------
        if index == "app_logs":
            must: list[dict] = []
            if re.search(r"\berror", q):
                must.append({"term": {"level": "ERROR"}})
            elif re.search(r"\bwarn", q):
                must.append({"term": {"level": "WARN"}})
            topic_words = [
                w for w in re.findall(r"[a-z]{4,}", q)
                if w not in {
                    "find", "show", "give", "logs", "log", "error", "errors", "related",
                    "about", "with", "that", "have", "which", "were", "there", "search",
                    "documents", "document", "please", "list", "recent", "last", "from",
                    "warn", "warning", "warnings", "level", "message", "messages",
                }
            ]
            if topic_words:
                must.append({"match": {"message": " ".join(topic_words[:4])}})
            clause = {"bool": {"must": must, "filter": filters}} if (must or filters) else {"match_all": {}}
            dsl = {
                "size": 0 if wants_count else 10,
                "query": clause,
                "sort": [{"timestamp": {"order": "desc"}}],
            }
            if wants_count:
                dsl.pop("sort")
            words = " + ".join(topic_words[:4]) or "any"
            return dsl, f"filter app_logs on level and match message against: {words}"

        # --- plain count ---------------------------------------------------
        if wants_count:
            dsl = {
                "size": 0,
                "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
                "track_total_hits": True,
            }
            when = "in the requested window" if rng else "overall"
            return dsl, f"count matching documents in '{index}' {when} using size 0"

        # --- listing documents ---------------------------------------------
        dsl = {
            "size": 20,
            "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
            "sort": [{date_field: {"order": "desc"}}],
        }
        when = "restricted to the requested date window" if rng else "unfiltered"
        return dsl, f"list up to 20 documents from '{index}', newest first, {when}"

    # -- the tool-calling loop --------------------------------------------
    def step(self, tools: list) -> LLMStep:
        available = {t.name for t in tools}

        if self.stage == 0:
            self.stage = 1
            if "ListIndexTool" in available:
                return LLMStep(
                    text=f"The question looks like it concerns the '{self.index}' data. Listing indices to confirm.",
                    tool_calls=[ToolCall(id=uuid.uuid4().hex[:8], name="ListIndexTool", arguments={})],
                )

        if self.stage == 1:
            self.stage = 2
            if "IndexMappingTool" in available:
                return LLMStep(
                    text=f"Reading the mapping of '{self.index}' so the query uses real field names and types.",
                    tool_calls=[
                        ToolCall(
                            id=uuid.uuid4().hex[:8],
                            name="IndexMappingTool",
                            arguments={"index": self.index},
                        )
                    ],
                )

        if self.stage == 2:
            self.stage = 3
            dsl, note = self._build_dsl()
            self.plan_note = note
            return LLMStep(
                text=f"Plan: {note}.",
                tool_calls=[
                    ToolCall(
                        id=uuid.uuid4().hex[:8],
                        name="SearchIndexTool",
                        arguments={"index": self.index, "query_dsl": dsl, "size": dsl.get("size", 10)},
                    )
                ],
            )

        return LLMStep(text=self._summarise(), tool_calls=[])

    def add_tool_result(self, call: ToolCall, content: str) -> None:
        try:
            self.results[call.name] = json.loads(content)
        except json.JSONDecodeError:
            self.results[call.name] = content

    # -- answer writing ----------------------------------------------------
    def _summarise(self) -> str:
        result = self.results.get("SearchIndexTool")
        if not isinstance(result, dict):
            return "No search result was returned, so I cannot answer this one."
        if "error" in result:
            return f"The search failed: {result.get('message', result['error'])}"

        total = (result.get("hits", {}).get("total") or {}).get("value", 0)
        hits = result.get("hits", {}).get("hits", [])
        aggs = result.get("aggregations") or {}
        lines: list[str] = []

        if "top_items" in aggs:
            buckets = aggs["top_items"]["buckets"]
            if not buckets:
                return "No completed orders matched, so there is nothing to rank."
            lines.append(f"Top {len(buckets)} by total sales value:")
            for i, b in enumerate(buckets, 1):
                revenue = (b.get("metric") or {}).get("value") or 0
                lines.append(
                    f"  {i}. {b['key']} - {b['doc_count']} orders, "
                    f"Rs {revenue:,.0f} total"
                )
            return "\n".join(lines)

        if not hits and total == 0:
            return f"Nothing in the '{self.index}' index matched that. The query ran successfully and returned 0 documents."

        if not hits:
            noun = {"users": "users", "orders": "orders", "app_logs": "log entries"}[self.index]
            window = _date_range(self.question.lower())
            when = ""
            if window:
                if "yesterday" in self.question.lower():
                    when = " yesterday"
                elif window.get("gte", "").startswith("now-"):
                    days = window["gte"].removeprefix("now-").split("d")[0]
                    when = f" in the last {days} days"
            return f"{total} {noun}{when}."

        lines.append(f"{total} matching document{'s' if total != 1 else ''} in '{self.index}'. Showing {len(hits)}:")
        for hit in hits[:10]:
            src = hit["_source"]
            if self.index == "users":
                lines.append(f"  - {src.get('name')} ({src.get('email')}), {src.get('plan')} plan, signed up {src.get('signup_date')}")
            elif self.index == "orders":
                lines.append(f"  - {src.get('order_id')}: {src.get('product')} x{src.get('quantity')}, Rs {src.get('amount')}, {src.get('status')}, {src.get('order_date')}")
            else:
                lines.append(f"  - [{src.get('level')}] {src.get('timestamp')} {src.get('service')}: {src.get('message')}")
        if total > len(hits):
            lines.append(f"  ... and {total - len(hits)} more.")
        return "\n".join(lines)


# --------------------------------------------------------------------------

_PROVIDERS = {
    "anthropic": AnthropicLLM,
    "openai": OpenAILLM,
    "groq": GroqLLM,
    "xai": XaiLLM,
    "rules": RulesLLM,
}


def build_llm(provider: str = "", model: str = "") -> BaseLLM:
    provider = (provider or settings.llm_provider or "rules").lower()
    if provider not in _PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. Choose one of: {', '.join(_PROVIDERS)}"
        )
    return _PROVIDERS[provider](model or settings.llm_model)
