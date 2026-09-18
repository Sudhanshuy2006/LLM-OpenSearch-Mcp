# OpenSearch MCP + LLM

Ask an OpenSearch cluster questions in plain English, and watch every step of
how the answer was produced.

```
you  →  LLM  →  MCP client  →  MCP server  →  OpenSearch
                                                  ↓
you  ←  LLM  ←──────────── hits + aggregations ───┘
```

The point of this project is not the chat box. It is the **trace**: for every
question the UI shows which tool the model picked, the exact Query DSL it
wrote, the raw JSON OpenSearch returned, and how the final sentence was derived
from that JSON. If a number in the answer is wrong, you can see exactly where
it came from.

---

## Run it in 30 seconds

No Docker, no API key, no cluster:

```bash
./run.sh demo
```

Open <http://127.0.0.1:8000>. An in-memory search engine stands in for
OpenSearch, and a deterministic rules planner stands in for the LLM. Everything
in between — the MCP server, the stdio transport, the tool schemas, the agent
loop, the UI — is the real thing.

### Run it against a real cluster

```bash
cp .env.example .env          # edit if you want a different password
./run.sh up                   # starts OpenSearch in Docker, loads sample data
./run.sh ui                   # UI at http://127.0.0.1:8000
```

`./run.sh up` waits for the cluster healthcheck before loading data, so there
is no guessing how long to sleep.

If you prefer to set things up by hand instead of using `.env`:

```bash
export OPENSEARCH_URL="https://localhost:9200"
export OPENSEARCH_USERNAME="admin"
export OPENSEARCH_PASSWORD="<your-password>"   # must match .env / docker-compose
export MCP_SERVER_MODE=local
unset DEMO_MODE

.venv/bin/python poc/load_sample_data.py
.venv/bin/python poc/web/app.py
```

### Use a real LLM

Pick any one provider and put its key in `.env`:

| Provider | Variable | Model used | Notes |
|---|---|---|---|
| `rules` | *(none)* | `rules-planner-v1` | Default. No key, no network — a deterministic offline planner |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | free tier, no card, easiest to start with |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` | trial credit on signup |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | pay per token |
| xAI (Grok) | `XAI_API_KEY` | `grok-4-fast` | trial credit; the *API* is not free, only the chat UI is |

```bash
LLM_PROVIDER=groq ./run.sh ui
```

Whichever provider is active, the pipeline behaves identically — this is the
practical proof that the MCP integration is model-agnostic, not hard-coded to
one vendor.

---

## The UI, tab by tab

Once running, open **http://127.0.0.1:8000**.

- **Ask** — type a plain-English question, press Run. The full trace renders
  below: the question, each model reasoning turn, each MCP tool call (with
  exact arguments), each OpenSearch response (raw JSON expandable), and the
  final plain-English answer.
- **Explore data** — browse `users`, `orders`, `app_logs` as real tables.
  Field types are shown live via `IndexMappingTool`. This tab uses the same
  MCP tools the LLM uses — no direct OpenSearch access, no LLM either.
- **Write query** — a raw Query DSL text editor. No model is involved: the
  JSON you type is exactly what reaches OpenSearch, through `SearchIndexTool`.
  Six ready-made presets are included (count yesterday's orders, top products
  by revenue, signups in the last 30 days, payment errors, orders per day,
  everything in an index). `Ctrl`/`Cmd`+`Enter` runs it.
  The two tabs connect: run a question on **Ask**, then press **Open in
  editor** on its `SearchIndexTool` call — the exact query the model wrote
  lands here, ready to edit and re-run. Fastest way to check whether a
  generated answer matches what you would have written by hand.
- **MCP tools** — a live listing of every tool the running MCP server
  exposes: name, full description, and JSON Schema for its arguments. This
  is the entire contract the model operates under.
- **How it flows** — a diagram and step-by-step explanation of the request
  lifecycle, for quick reference during a demo.

### Same thing from the terminal

```bash
python poc/cli.py "How many orders were placed yesterday?"
python poc/cli.py --demo                          # all five demo questions
python poc/cli.py --tools                          # list MCP tools, no LLM
python poc/cli.py --index orders --query '{"size":0,"query":{"match_all":{}}}'
cat query.json | python poc/cli.py --index users --query -
```

The CLI runs through the exact same `agent.py` and `mcp_client.py` as the web
UI — only the display differs (colored terminal text vs. an HTML trace).

---

## What's in here

```
opensearch-mcp-project/
├── run.sh                    one-command launcher
├── requirements.txt
├── .env.example               template — copy to .env, never commit .env
├── .gitignore                 keeps .env, .venv/, __pycache__/ out of git
├── poc/
│   ├── config.py             every setting, read in one place
│   ├── sample_data.py        dataset generator (dates relative to today)
│   ├── load_sample_data.py   creates indices with explicit mappings, bulk-loads
│   ├── demo_engine.py        in-memory OpenSearch stand-in for DEMO_MODE
│   ├── local_mcp_server.py   a readable MCP server, one file, four tools
│   ├── mcp_client.py         MCP client: handshake, tools/list, tools/call
│   ├── llm_router.py         Anthropic / OpenAI / Groq / xAI / rules
│   ├── agent.py              the loop, and the trace it records
│   ├── cli.py                terminal version
│   ├── mcp_server_config.json  drop-in config for Claude Desktop
│   ├── docker-compose.yml
│   └── web/
│       ├── app.py            FastAPI backend (Ask / Explore / Write query / Tools APIs)
│       └── static/index.html the UI, five tabs, self-contained
├── docs/                     1–6, read in order
└── demo/demo_examples.md     five worked examples, full traces
```

---

## The data

| Index | Documents | Key fields |
|---|---|---|
| `users` | 12 | `user_id`, `name`, `email`, `city`, `plan`, `signup_date` |
| `orders` | ~126 | `order_id`, `user_id`, `product`, `category`, `amount`, `quantity`, `status`, `order_date` |
| `app_logs` | 16 | `timestamp`, `level`, `service`, `message`, `trace_id`, `latency_ms` |

All dates are generated relative to *today* at load time — never hard-coded —
so questions like "orders placed yesterday" or "signups in the last 30 days"
keep working correctly no matter when the project is run.

### Adding data manually (outside the LLM)

The MCP server is deliberately **read-only**
(`OPENSEARCH_SETTINGS_ALLOW_WRITE=false`), so the LLM can never write or
delete data. To add a document yourself, go through OpenSearch directly —
Dev Tools in OpenSearch Dashboards (`http://localhost:5601`), or curl:

```bash
curl -k -u admin:<your-password> -X POST "https://localhost:9200/orders/_doc" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"o9999","user_id":"u13","product":"Wireless Keyboard",
       "category":"peripherals","amount":1999,"quantity":1,
       "status":"completed","order_date":"2026-09-17"}'
```

Verify it landed with the **Explore data** tab, or:

```bash
curl -k -u admin:<your-password> "https://localhost:9200/orders/_count"
```

---

## Ports

| Port | What runs there | What it's for |
|---|---|---|
| `9200` | OpenSearch REST API | The real database. All data lives here. |
| `9600` | OpenSearch Performance Analyzer | Internal monitoring, not used directly. |
| `5601` | OpenSearch Dashboards | OpenSearch's own visual tool — ships with OpenSearch, not built by this project. |
| `8000` | This project's web app | `poc/web/app.py` serves both the API and the UI here. This is what you demo. |

---

## The two MCP servers

`MCP_SERVER_MODE` chooses which server the client launches:

- **`local`** — `poc/local_mcp_server.py`, bundled here. One file, four tools,
  readable in ten minutes. Works in demo mode. This is the default.
- **`official`** — the upstream `opensearch-mcp-server-py`, fetched and run by
  `uvx`. ~40 tools covering search relevance, cluster admin, PPL and more.

Both expose tools under the **same names** (`ListIndexTool`, `IndexMappingTool`,
`SearchIndexTool`, `GetIndexStatsTool`), so switching between them changes
nothing in the client, the prompts or the UI. That interchangeability is the
whole argument for MCP, demonstrated on itself.

To use the official server:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
MCP_SERVER_MODE=official ./run.sh ui
```

---

## Using it from Claude Desktop instead

You do not need this repo's Python client at all. Copy `poc/mcp_server_config.json`
into Claude Desktop's config file, restart it, and ask Claude the same questions
directly:

- macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows — `%APPDATA%\Claude\claude_desktop_config.json`

---

## What was broken in the original version

| Problem | Effect | Fix |
|---|---|---|
| Prompt told the model to call `get_mapping` and `search_index` | Those tools do not exist. The real names are `IndexMappingTool` and `SearchIndexTool`. The model would hallucinate tool names and every call would fail. | Prompt and code use the verified upstream names |
| Sample data had hard-coded dates | "Orders placed yesterday" returned 0 results the day after the file was written | Dataset generated relative to today |
| No index mappings declared | `product` became a `text` field, so `terms` aggregations on it failed | Explicit mappings with `keyword` types |
| `mcp` dependency unpinned | v2 renamed `FastMCP` and changed the server API; a fresh install broke the server | Pinned `mcp>=1.9.4,<2`, matching what the upstream OpenSearch server requires |
| Dashboards had no credentials | Container could not reach a security-enabled cluster; UI hung on a spinner | Credentials and SSL mode passed through |
| No healthcheck | `docker-compose up` returned before the cluster was ready, so loading data failed | Healthcheck plus `--wait` |
| `load_sample_data.py` contained a dead `if False else None` line and duplicated logic | Confusing, and `app_logs` was created twice | Rewritten |
| All ~40 upstream tools exposed | Wasted context; model picked cluster-admin tools for data questions | `OPENSEARCH_ENABLED_TOOLS` restricts to four |
| Writes allowed | A confused model could delete an index | `OPENSEARCH_SETTINGS_ALLOW_WRITE=false` |
| Config re-read from `os.environ` in each script with different defaults | Loader and server could silently target different clusters | Single `config.py` |
| API key required to run anything | Nothing was demonstrable without a paid account | `rules` provider and `DEMO_MODE` |
| Stray `.swp` editor swap file committed | — | Removed |
| Raw-query editor showed "matched nothing" on `size:0` count queries | Misleading — 0 documents returned is normal for a count query, not zero matches | UI now distinguishes "N documents matched (size was 0)" from an actual zero-match result |
| Running `poc/mcp_client.py` directly instead of `poc/cli.py` | It only ever prints the discovered tool list, regardless of any question passed on the command line | Documented: use `poc/cli.py "<question>"` to actually ask something; `mcp_client.py` is a low-level smoke test only |
| Environment variables exported inside `poc/` instead of the project root | Relative paths (`.venv/bin/python`) resolved incorrectly, and `MCP_SERVER_MODE=official` connecting with unset/blank credentials produced `401 Unauthorized` | Always run commands from the project root; keep credentials in `.env` so they don't depend on which shell/folder you're in |

---

## Security notes — read before pushing to GitHub

- **Never commit `.env`.** It holds real OpenSearch credentials (and, if you
  add one, a real LLM API key). Only `.env.example` — with placeholder
  values — is meant to be tracked. `.gitignore` already excludes `.env`,
  `.venv/`, `__pycache__/`, and `*.pyc`.
- **Before your first `git add -A`, run `git status` and read it.** If `.env`
  or `.venv/` show up as new/untracked files about to be committed, stop —
  something is wrong with `.gitignore` in that checkout. Fix it before
  committing.
- **The MCP server is read-only by design**
  (`OPENSEARCH_SETTINGS_ALLOW_WRITE=false`). The LLM can query data but can
  never modify or delete an index, regardless of what a prompt asks it to do.
  This is enforced by the server, not by trusting the model.
- **Set your own password in `.env`** before starting the cluster — do not
  reuse whatever example value appears in `.env.example` or in this README.
  `.env` is git-ignored, so your real password never gets committed. Never
  put a real password directly in this README, in `docker-compose.yml`, or
  in any other file that gets committed.
- **If a secret is ever accidentally committed**, deleting the file in a new
  commit is not enough — it still exists in git history. Rotate the
  credential immediately, then either rewrite history (`git filter-repo`) or
  start the repository fresh.

### Pushing to GitHub — the actual steps

```bash
cd opensearch-mcp-project

# clean up any stray bytecode
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

git init
git add -A
git status                 # confirm .env and .venv/ are NOT listed
git commit -m "OpenSearch MCP + LLM proof of concept - working end to end"

# create an empty repo on GitHub first (no README/.gitignore from their side),
# then:
git remote add origin https://github.com/<username>/<repo-name>.git
git branch -M main
git push -u origin main
```

After pushing, open the repo on GitHub and confirm `.env` and `.venv/` are
**not** present in the file listing — only `.env.example` should be there.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| UI loads but every pill is red | Backend cannot start the MCP server. Run `python poc/cli.py --tools` to see the real error. |
| `uvx: command not found` | Only needed for `MCP_SERVER_MODE=official`. Install uv, then `source "$HOME/.local/bin/env"` and open a new shell. Or use `MCP_SERVER_MODE=local`. |
| Connection refused on port 9200 | The container is still starting, or isn't running at all. Check `docker ps`; if empty, run `docker compose -f poc/docker-compose.yml up -d` and wait for `healthy`. |
| `AuthenticationException: 401 Unauthorized` in MCP server logs | The password the client is using doesn't match the cluster's actual password. Verify with `curl -k -u admin:<your-password> https://localhost:9200` first — if that also 401s, the cluster was bootstrapped with a different password than what's in `.env`. Fix: `docker compose -f poc/docker-compose.yml down -v` (wipes the volume) then `up -d` again, so the cluster bootstraps fresh using the current `.env` password. |
| OpenSearch container exits immediately | Password policy rejected, or not enough memory. Use 8+ chars with upper/lower/digit/symbol, and give Docker at least 2 GB. |
| `certificate verify failed` | Expected with the local self-signed cert. Keep `OPENSEARCH_VERIFY_CERTS=false` for local development only. |
| Model answers without calling any tool | It thinks it already knows. Ask something that requires the data ("how many orders yesterday"), and check the trace — zero `MCP CALL` entries means the tools were never sent. |
| `terms` aggregation fails with "Fielddata is disabled" | You are aggregating on a `text` field. Use its `.keyword` subfield, or declare it as `keyword` in the mapping. |
| Answer disagrees with the data | Open the `tool_result` entry in the trace and read the raw JSON. Either the query was wrong (prompt problem) or the model misread the result (model problem). The trace tells you which. |
| OpenSearch Dashboards: "ScopedHistory instance has fell out of navigation scope" | An internal Dashboards routing glitch, unrelated to this project. Hard refresh (`Ctrl+Shift+R`) or navigate to `http://localhost:5601/app/discover` directly. |
| Running `mcp_client.py` prints tools but ignores my question | That file is a low-level MCP smoke test only. Use `poc/cli.py "<question>"` instead — that's the one that actually runs the full pipeline. |

---

## Reading order

1. `docs/01-mcp-overview.md` — what MCP is and why it exists
2. `docs/02-opensearch-mcp.md` — the OpenSearch MCP server and its tools
3. `docs/03-architecture.md` — the flow, end to end
4. `docs/04-setup.md` — setup in detail
5. `docs/05-before-after.md` — Query DSL by hand vs. by conversation
6. `docs/06-limitations.md` — where this breaks, and what to do about it
7. `demo/demo_examples.md` — five questions traced all the way through
