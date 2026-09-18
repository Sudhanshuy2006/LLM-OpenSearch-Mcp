# 1. Model Context Protocol

## What it is

MCP is an open protocol that lets a language model use external systems through
a uniform interface. A model connected over MCP can list what a system can do,
call one of those capabilities with structured arguments, and read the result.

The protocol itself is small. Messages are JSON-RPC 2.0, carried over stdio
(the server runs as a subprocess) or HTTP streaming. The specification defines
the handshake, the method names, and the shape of tool descriptions. It says
nothing about OpenSearch, or about any particular model.

The useful comparison is a device driver. Before a common driver interface,
every application needed bespoke code for every printer. MCP plays that role
for model-to-system connections: implement a server once and every MCP-capable
client can use it.

## Why it is needed

An LLM on its own has three hard limits. It knows only what was in its training
data. It cannot read anything private. And it cannot act — it produces text,
not effects.

The obvious fix is to write glue code: fetch some data, paste it into the
prompt, parse what comes back. That works for one integration. It stops working
at scale, and the reason is combinatorial. With *M* models and *N* systems you
need *M × N* integrations. Swap the model and you rewrite all *N*. Add a system
and you write *M* more.

MCP turns that into *M + N*. Each model needs one MCP client. Each system needs
one MCP server. They compose.

There is a second, less obvious benefit. Because tool descriptions arrive from
the server at runtime rather than being baked into the prompt, capabilities can
change without redeploying anything on the model side. Add a tool to the server
and the model can use it on the next request.

## How it works

```
┌──────────────┐                              ┌──────────────┐
│  MCP HOST    │                              │  MCP SERVER  │
│ (Claude app, │◄──── JSON-RPC over stdio ───►│ (opensearch, │
│  your agent) │        or HTTP stream        │  postgres…)  │
│              │                              │              │
│ ┌──────────┐ │   1. initialize              │   exposes:   │
│ │  model   │ │   2. tools/list              │   · tools    │
│ └──────────┘ │   3. tools/call              │   · resources│
│ ┌──────────┐ │   4. result                  │   · prompts  │
│ │MCP client│ │                              │              │
│ └──────────┘ │                              │      ↓       │
└──────────────┘                              │  real system │
                                              └──────────────┘
```

A single request goes through five steps.

**1 — Handshake.** The client starts the server process and sends `initialize`.
Both sides declare protocol version and capabilities.

**2 — Discovery.** The client sends `tools/list`. The server replies with every
tool it offers: a name, a natural-language description, and a JSON Schema for
the arguments. The client hands these to the model as its available functions.

**3 — Selection.** The model reads the user's question alongside those tool
descriptions and emits a structured call — a tool name and a JSON object of
arguments. It has produced an *intent*, not an effect.

**4 — Execution.** The client sends `tools/call`. The server validates the
arguments against the schema, performs the real work, and returns content.
Note who executes: the client, not the model. The model cannot reach the
database on its own, which is what makes the boundary enforceable.

**5 — Interpretation.** The result goes back into the conversation. The model
either calls another tool or writes its answer. Steps 3–5 repeat until it stops
calling tools.

## The components

| Component | What it is | Here |
|---|---|---|
| **Host** | The application the user talks to | The FastAPI app, or Claude Desktop |
| **Client** | Speaks MCP; one per server connection | `poc/mcp_client.py` |
| **Server** | Exposes a system's capabilities | `poc/local_mcp_server.py`, or `opensearch-mcp-server-py` |
| **Tool** | A callable action, with a schema. Model-controlled. | `SearchIndexTool` |
| **Resource** | Readable context, addressed by URI. App-controlled. | Not used here |
| **Prompt** | A reusable template the user can invoke. User-controlled. | Not used here |
| **Transport** | stdio or HTTP streaming | stdio |

The distinction between the three primitives is about who decides. The **model**
decides when to call a tool. The **application** decides which resources to
attach. The **user** decides when to invoke a prompt. Most servers, including
OpenSearch's, are overwhelmingly tools.

## Tool descriptions are the real interface

Worth dwelling on, because it is where most MCP work actually goes. The model
never sees your code. It sees the tool's name, its description, and its argument
schema — a few hundred words in total. That text is the entire contract.

A vague description produces bad tool selection, and no amount of prompt
engineering on the client side fixes it. Compare:

```
"Searches an index."
```

against what this project ships:

```
"Runs an OpenSearch Query DSL request against one index and returns the
 matching documents and any aggregation results. PREREQUISITE: know the
 index mapping first (IndexMappingTool). For counting or statistics, set
 size to 0 and use an aggregation instead of paging through documents.
 For date ranges you may use date math such as
 {"range":{"order_date":{"gte":"now-30d/d","lt":"now/d"}}}."
```

The second one teaches the model the workflow, the efficient pattern, and the
date syntax. Writing tool descriptions is prompt engineering — just relocated
to the server, where every client benefits from it at once.

## Benefits

- **Integrations compose.** *M + N* instead of *M × N*.
- **Model-agnostic.** This repo runs the same server against Claude, GPT, Llama
  via Groq, Grok, and a regex planner. None of them know about each other.
- **Capabilities are discovered, not hard-coded.** Add a tool server-side; the
  model can use it immediately.
- **A real security boundary.** The model emits intents. The server decides what
  is permitted. This project sets `OPENSEARCH_SETTINGS_ALLOW_WRITE=false`, so
  a model that tries to delete an index is refused by the server — regardless of
  what the prompt said.
- **Auditable.** Every call is a logged, structured event. That is what makes
  the trace in this project's UI possible.
- **An ecosystem.** Servers exist for Postgres, GitHub, Slack, Puppeteer,
  filesystems and many more, all usable by the same client.

## Limitations

- **Tools consume context.** Forty tool schemas can occupy several thousand
  tokens before the user has said anything. This project exposes four.
- **Selection is probabilistic.** A model can pick the wrong tool, or skip tools
  entirely and answer from memory. Nothing in the protocol prevents this.
- **Latency compounds.** Each tool call is a full model round-trip. A three-call
  answer means three inference passes plus three queries.
- **Schemas are validated; semantics are not.** `SearchIndexTool` will happily
  run a syntactically valid query that answers a different question.
- **Tool descriptions are injected text.** A malicious server can describe a
  tool in a way that manipulates the model. Only run servers you trust.
- **Stdio servers are local.** One subprocess per client. Remote and
  multi-tenant deployments need the HTTP streaming transport, plus real auth.
- **Young and moving.** The Python SDK's v2 release renamed `FastMCP` and
  changed the server decorator API — which is exactly why `requirements.txt`
  here pins `mcp<2`.
