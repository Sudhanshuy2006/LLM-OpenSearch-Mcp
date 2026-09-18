# 3. Architecture

## The whole system

```
┌─────────────────────────────────────────────────────────────────────┐
│  BROWSER                                                            │
│  poc/web/static/index.html                                          │
│  Ask · Explore data · MCP tools · How it flows                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTP  /api/ask  /api/browse  /api/tools
┌────────────────────────────▼────────────────────────────────────────┐
│  BACKEND — poc/web/app.py (FastAPI)                                 │
│                                                                     │
│   agent.py ─── the loop, and the trace it records                   │
│      │                                                              │
│      ├── llm_router.py ──► Anthropic │ OpenAI │ Groq │ xAI │ rules  │
│      │                                                              │
│      └── mcp_client.py ──► MCP CLIENT                               │
└────────────────────────────┬────────────────────────────────────────┘
                             │ JSON-RPC 2.0 over stdio
┌────────────────────────────▼────────────────────────────────────────┐
│  MCP SERVER            (subprocess)                                 │
│  local_mcp_server.py   or   uvx opensearch-mcp-server-py            │
│  ListIndexTool · IndexMappingTool · SearchIndexTool · …             │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTPS — the OpenSearch REST API
┌────────────────────────────▼────────────────────────────────────────┐
│  OPENSEARCH 2.15          users · orders · app_logs                 │
│  (or demo_engine.py, in-process, when DEMO_MODE=true)               │
└─────────────────────────────────────────────────────────────────────┘
```

Two boundaries carry all the weight.

**The stdio boundary** between client and server is where MCP lives. Swap the
server for a Postgres one and nothing above it changes.

**The HTTPS boundary** between server and OpenSearch is where credentials live.
They exist only in the server's environment. The model never sees them, which
is not a matter of discipline — it is structural.

## One request, in sequence

```
user      UI        agent      llm_router    mcp_client   mcp_server   opensearch
 │         │          │             │             │            │            │
 │ ask ───►│          │             │             │            │            │
 │         │ POST ───►│             │             │            │            │
 │         │          │ open_mcp ───────────────► │ spawn ───► │            │
 │         │          │             │             │ initialize │            │
 │         │          │             │             │ tools/list │            │
 │         │          │◄──────── 4 tool schemas ──┤            │            │
 │         │          │             │             │            │            │
 │         │          │ step(tools) │             │            │            │
 │         │          ├────────────►│             │            │            │
 │         │          │◄─ tool_call ┤             │            │            │
 │         │          │  IndexMappingTool{orders} │            │            │
 │         │          │             │             │            │            │
 │         │          │ call() ────────────────►  │ tools/call │            │
 │         │          │             │             ├───────────►│ GET        │
 │         │          │             │             │            │ _mapping ─►│
 │         │          │             │             │            │◄───────────┤
 │         │          │◄─ field types ────────────┤            │            │
 │         │          │             │             │            │            │
 │         │          │ step(tools) │   ← model now knows the schema        │
 │         │          ├────────────►│             │            │            │
 │         │          │◄─ tool_call SearchIndexTool{query_dsl} │            │
 │         │          │ call() ─────────────────► ├───────────►│ POST       │
 │         │          │             │             │            │ _search ──►│
 │         │          │◄─ hits + aggregations ────┤            │◄───────────┤
 │         │          │             │             │            │            │
 │         │          │ step(tools) │             │            │            │
 │         │          ├────────────►│             │            │            │
 │         │          │◄─ text, no tool calls ────┤  ← loop ends here       │
 │         │◄─ trace ─┤             │             │            │            │
 │◄─ answer┤          │             │             │            │            │
```

The loop exits on the first model turn that contains no tool calls. That is the
only termination condition, plus a hard cap of eight iterations.

## Why the trace is a first-class object

`agent.py` records a `TraceEvent` for every stage. It would be simpler to return
just the answer string. The trace exists because an LLM answer about data is
unfalsifiable on its own — "22 orders were placed yesterday" looks identical
whether it came from an aggregation or from nowhere.

With the trace, three distinct failure modes become distinguishable at a glance:

| What you see | What went wrong |
|---|---|
| No `tool_call` events at all | The model answered from memory. It fabricated. |
| Tool called, query is wrong | Prompt or tool-description problem. |
| Query correct, answer disagrees with the JSON | The model misread a correct result. |

Those three need completely different fixes, and without the trace they all
present as "the answer was wrong".

## Module responsibilities

| File | Responsibility | Deliberately does not |
|---|---|---|
| `config.py` | Read every setting once | Contain logic |
| `sample_data.py` | Generate the dataset and mappings | Touch the network |
| `load_sample_data.py` | Create indices, bulk-index | Know about MCP |
| `demo_engine.py` | In-memory Query DSL execution | Claim to be complete |
| `local_mcp_server.py` | Expose tools over MCP | Know which LLM is calling |
| `mcp_client.py` | Handshake, discovery, invocation | Know anything about OpenSearch |
| `llm_router.py` | One interface over five backends | Know about MCP transport |
| `agent.py` | The loop, and the trace | Know about HTTP |
| `web/app.py` | HTTP surface | Contain pipeline logic |

`mcp_client.py` containing no OpenSearch knowledge is the test that the
abstraction is real. If it needed to know about indices, MCP would not be
buying anything.

## Design decisions worth explaining

**The dataset is generated, not stored as JSON.** Demo questions ask about
"yesterday" and "the last 30 days". Fixed dates in a file make those questions
return nothing a week later, and the demo looks broken when it is not.

**Mappings are explicit.** Dynamic mapping turns `product` into a `text` field,
and `terms` aggregations on `text` fail with a fielddata error. Declaring
`keyword` up front means `IndexMappingTool` reports unambiguous types, so the
model writes a working aggregation on the first attempt.

**Only four tools are enabled.** Fewer, better-described tools beat more tools.

**Writes are blocked at the server.** `OPENSEARCH_SETTINGS_ALLOW_WRITE=false`
makes "read-only" a property of the system rather than an instruction in a
prompt that a model may ignore.

**The data explorer goes through MCP too.** `/api/browse` calls
`SearchIndexTool` rather than talking to OpenSearch directly. It costs a little
performance and demonstrates something worth demonstrating: MCP tools are a
perfectly good API for ordinary application code, not just for models.

**The `rules` provider is not a toy.** It implements the same contract as a real
LLM — receives tool schemas, chooses a tool, reads the result, writes an answer.
It makes the project runnable with no credentials, and it isolates faults: if
`rules` works and `anthropic` does not, the MCP wiring is fine and the problem
is the prompt.
