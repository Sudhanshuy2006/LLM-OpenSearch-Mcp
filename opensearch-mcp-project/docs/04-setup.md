# 4. Setup

Three paths, in increasing order of effort.

| Path | Docker | API key | Demonstrates |
|---|---|---|---|
| A — demo | no | no | The whole pipeline, in-memory |
| B — real cluster | yes | no | Real OpenSearch, rules planner |
| C — real cluster + LLM | yes | yes | The complete POC |

---

## Path A — nothing to install

```bash
git clone <repo> && cd opensearch-mcp-project
./run.sh demo
```

Open <http://127.0.0.1:8000>. `run.sh` creates a virtualenv and installs
dependencies on first run.

`DEMO_MODE=true` swaps OpenSearch for `demo_engine.py`, an in-process Query DSL
executor. The MCP server, the stdio transport, the tool schemas, the agent loop
and the UI are all real — only the storage layer is substituted.

---

## Path B — a real cluster

### 1. Check Docker

```bash
docker --version
docker compose version
```

OpenSearch with a 512 MB heap plus Dashboards needs about 2 GB available to
Docker. Docker Desktop → Settings → Resources on macOS and Windows.

### 2. Configure

```bash
cp .env.example .env
```

The default password is `StrongPass@2026`. If you change it, it must satisfy
OpenSearch's policy — 8+ characters with uppercase, lowercase, a digit and a
special character — or the container exits during bootstrap.

### 3. Start and load

```bash
./run.sh up
```

which runs:

```bash
docker compose -f poc/docker-compose.yml up -d --wait
python poc/load_sample_data.py
```

`--wait` blocks on the healthcheck, so the loader never runs against a cluster
that is still starting. Expect 40–60 seconds on first boot.

Verify by hand:

```bash
curl -k -u admin:StrongPass@2026 https://localhost:9200
curl -k -u admin:StrongPass@2026 https://localhost:9200/_cat/indices?v
```

You should see `users` (12 docs), `orders` (126), `app_logs` (16).

### 4. Run

```bash
./run.sh ui           # http://127.0.0.1:8000
./run.sh cli          # the five demo questions in the terminal
```

OpenSearch Dashboards is also available at <http://localhost:5601> — useful for
the before/after comparison in doc 5.

---

## Path C — add a real LLM

Add one key to `.env`:

```bash
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
```

| Provider | Variable | Default model | Cost |
|---|---|---|---|
| `groq` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | Free tier, no card |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` | Trial credit, then paid |
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | Paid |
| `xai` | `XAI_API_KEY` | `grok-4-fast` | Trial credit, then paid |
| `rules` | — | `rules-planner-v1` | Free, offline |

Groq is the easiest starting point: free tier, no card, OpenAI-compatible.

On Grok specifically — the free tier applies to the grok.com and X chat
interfaces. The **developer API** used here is billed per token after the
signup credit. The original version of this README implied otherwise.

Install the client library you need:

```bash
.venv/bin/pip install openai      # covers openai, groq, xai
.venv/bin/pip install anthropic   # covers anthropic
```

---

## Using the upstream MCP server

The default is the bundled `local` server. To use the official one:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uvx --version

MCP_SERVER_MODE=official ./run.sh ui
```

`uvx` downloads and runs `opensearch-mcp-server-py` in an isolated environment,
so it does not interact with this project's virtualenv. The first run takes a
minute or two.

Confirm which tools came back:

```bash
MCP_SERVER_MODE=official python poc/cli.py --tools
```

---

## Claude Desktop, with no Python client

You can skip this repo's client entirely. Merge `poc/mcp_server_config.json`
into Claude Desktop's config:

- macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows — `%APPDATA%\Claude\claude_desktop_config.json`

Restart Claude Desktop. The OpenSearch tools appear in the tools menu, and you
can ask the same questions in the normal chat window. This is the shortest
possible demonstration that MCP is model-agnostic: the same server, a completely
different host application, no code changes.

---

## Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `OPENSEARCH_URL` | `https://localhost:9200` | Cluster endpoint |
| `OPENSEARCH_USERNAME` | `admin` | |
| `OPENSEARCH_PASSWORD` | `StrongPass@2026` | |
| `OPENSEARCH_VERIFY_CERTS` | `false` | Local dev uses a self-signed cert |
| `MCP_SERVER_MODE` | `local` | `local` or `official` |
| `OPENSEARCH_ENABLED_TOOLS` | four core tools | Allowlist |
| `LLM_PROVIDER` | `rules` | `rules`/`groq`/`anthropic`/`openai`/`xai` |
| `LLM_MODEL` | provider default | Override the model |
| `DEMO_MODE` | `false` | In-memory engine instead of a cluster |

`OPENSEARCH_VERIFY_CERTS=false` is for local development only. Against a real
cluster, use proper certificates.

---

## Verifying the install

Work down this list; the first failure tells you where the problem is.

```bash
# 1. cluster reachable
curl -k -u admin:StrongPass@2026 https://localhost:9200

# 2. data loaded
curl -k -u admin:StrongPass@2026 https://localhost:9200/_cat/indices?v

# 3. MCP server starts and exposes tools
python poc/cli.py --tools

# 4. full pipeline
python poc/cli.py "How many orders were placed yesterday?"

# 5. web backend
python poc/web/app.py     # then open http://127.0.0.1:8000
```

If step 3 fails but steps 1–2 pass, the problem is the MCP server, not
OpenSearch. If step 4 fails but step 3 passes, the problem is the LLM
configuration. That ordering is the fastest way to localise a fault.
