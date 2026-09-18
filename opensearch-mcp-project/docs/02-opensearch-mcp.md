# 2. The OpenSearch MCP server

## What it is

`opensearch-mcp-server-py` is the official MCP server maintained by the
OpenSearch project. It wraps a cluster's APIs as MCP tools so any MCP-capable
model can query it, inspect it and analyse it without anyone writing
OpenSearch-specific client code.

Installed and run through `uv`:

```bash
uvx opensearch-mcp-server-py --transport stdio
```

It supports both transports (`stdio` for local subprocess use, `stream` for
HTTP), and both single-cluster and multi-cluster modes.

This repo also ships `poc/local_mcp_server.py`: a four-tool server in one
readable file, using the **same tool names**, so you can read an MCP server
end to end in ten minutes and switch to the upstream one without changing
anything else.

## What it exposes

The upstream server registers roughly forty tools. Grouped by purpose:

### Core data access — what this POC uses

| Tool | Arguments | Purpose |
|---|---|---|
| `ListIndexTool` | `index`, `include_detail` | List indices and their metadata |
| `IndexMappingTool` | `index` | Field names and types for an index |
| `SearchIndexTool` | `index`, `query_dsl`, `size`, `format` | Run a Query DSL request |
| `GetIndexInfoTool` | `index` | Settings, aliases, detailed info |
| `GetIndexStatsTool` | `index` | Document count and store size |
| `MsearchTool` | `index`, `query` | Several searches in one round trip |
| `PPLQueryTool` | `query` | Run Piped Processing Language instead of DSL |
| `GenericOpenSearchApiTool` | `method`, `path`, `body` | Escape hatch to any REST endpoint |

### Cluster operations

`GetShardsTool`, `GetClusterStateTool`, `GetSegmentsTool`, `CatNodesTool`,
`GetNodesTool`, `GetAllocationTool`, `GetNodesHotThreadsTool`,
`GetLongRunningTasksTool`, `GetQueryInsightsTool`.

### Search relevance tuning

`CreateQuerySetTool`, `GetQuerySetTool`, `SampleQuerySetTool`,
`CreateExperimentTool`, `GetExperimentTool`, `SearchJudgmentsTool`,
`CreateSearchConfigurationTool`, `CreateLLMJudgmentListTool`, and others —
an A/B testing workbench for ranking, driven conversationally.

### Observability analysis

`DataDistributionTool`, `LogPatternAnalysisTool`, `MetricChangeAnalysisTool`.
These do statistical work server-side (clustering, distribution comparison)
and return a summary rather than raw documents.

### Memory

`SaveMemoryTool`, `SearchMemoryTool`, `DeleteMemoryTool` — persist facts across
sessions in an OpenSearch index.

### Why this project enables only four

Forty tool schemas cost thousands of tokens on every request, and a model given
both `SearchIndexTool` and `GetNodesHotThreadsTool` will occasionally reach for
the wrong one. `OPENSEARCH_ENABLED_TOOLS` narrows the surface:

```bash
OPENSEARCH_ENABLED_TOOLS=ListIndexTool,IndexMappingTool,SearchIndexTool,GetIndexStatsTool
```

Fewer, better-described tools beat more tools. This is the single highest-value
configuration change in the whole setup.

## How a model interacts with it

There is no OpenSearch-specific code on the model side. The sequence for
*"how many orders were placed yesterday?"*:

```
model                          MCP server                    OpenSearch
  │                                 │                             │
  │── tools/list ──────────────────►│                             │
  │◄─ 4 tool schemas ───────────────│                             │
  │                                 │                             │
  │── tools/call ListIndexTool ────►│── GET /_cat/indices ───────►│
  │◄─ users, orders, app_logs ──────│◄────────────────────────────│
  │                                 │                             │
  │── tools/call IndexMappingTool ─►│── GET /orders/_mapping ────►│
  │◄─ order_date: date, … ──────────│◄────────────────────────────│
  │                                 │                             │
  │── tools/call SearchIndexTool ──►│── POST /orders/_search ────►│
  │   {range: {order_date:          │                             │
  │     {gte: "now-1d/d",           │                             │
  │      lt:  "now/d"}}, size: 0}   │                             │
  │◄─ {"total": {"value": 22}} ─────│◄────────────────────────────│
  │                                 │                             │
  └─► "22 orders were placed yesterday."
```

The middle step is the one that matters. The model does not guess that the date
field is called `order_date` — it asks. That is why `IndexMappingTool` is
described with "ALWAYS call this before SearchIndexTool": a hallucinated field
name produces a query that runs fine and returns zero results, which is far
worse than an error.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| OpenSearch | 2.x (1.x partially) | Some tools declare a `min_version` |
| Python | 3.10+ | |
| `uv` / `uvx` | latest | Only for the upstream server |
| Docker | any recent | Only for running OpenSearch locally |
| MCP SDK | `>=1.9.4,<2` | The upstream server requires `mcp<2` |
| Credentials | basic auth, or AWS IAM | `OPENSEARCH_USERNAME`/`PASSWORD`, or `AWS_REGION`/`AWS_PROFILE` |

Memory matters more than people expect: OpenSearch with a 512 MB heap plus
Dashboards needs roughly 2 GB available to Docker. Below that the container
exits during bootstrap with no obvious message.

## Configuration reference

Every setting is an environment variable on the server process.

| Variable | Purpose |
|---|---|
| `OPENSEARCH_URL` | Cluster endpoint |
| `OPENSEARCH_USERNAME` / `OPENSEARCH_PASSWORD` | Basic auth |
| `OPENSEARCH_SSL_VERIFY` | Set `false` for local self-signed certificates |
| `OPENSEARCH_NO_AUTH` | Skip authentication entirely |
| `OPENSEARCH_ENABLED_TOOLS` | Comma-separated allowlist |
| `OPENSEARCH_DISABLED_TOOLS` | Comma-separated denylist |
| `OPENSEARCH_ENABLED_TOOLS_REGEX` | Allowlist by pattern |
| `OPENSEARCH_TOOL_CATEGORIES` | Enable whole categories at once |
| `OPENSEARCH_SETTINGS_ALLOW_WRITE` | `false` blocks every mutating tool |
| `OPENSEARCH_MAX_RESPONSE_SIZE` | Cap on response bytes returned to the model |
| `OPENSEARCH_QUERY_TIMEOUT` / `OPENSEARCH_TIMEOUT` | Timeouts |
| `AWS_REGION`, `AWS_PROFILE`, `AWS_IAM_ARN` | Amazon OpenSearch Service |
| `AWS_OPENSEARCH_SERVERLESS` | Serverless collections |

Two of these are security controls rather than conveniences.
`OPENSEARCH_SETTINGS_ALLOW_WRITE=false` is what makes "the model cannot delete
an index" a property of the system rather than a hope about the prompt.
`OPENSEARCH_MAX_RESPONSE_SIZE` stops one careless `size: 10000` from blowing
through the context window and the token budget at the same time.
