"""
web/app.py
==========
FastAPI backend behind the UI.

Endpoints:
  GET  /                  the single-page UI
  GET  /api/status        which cluster, which MCP server, which LLM
  GET  /api/tools         the tools the MCP server currently exposes
  GET  /api/indices       index list, straight from the MCP server
  GET  /api/mapping/{ix}  field mappings for one index
  POST /api/browse        raw documents from an index (the data explorer)
  POST /api/ask           natural-language question -> full pipeline trace

Note that /api/indices, /api/mapping and /api/browse deliberately go through
the MCP server rather than talking to OpenSearch directly. The data explorer
is therefore itself a demonstration that MCP tools are useful outside an LLM:
the same tool that Claude calls is the one powering the table you are reading.
"""

from __future__ import annotations

import sys
from pathlib import Path

POC_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC_DIR))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent import answer_question  # noqa: E402
from config import settings  # noqa: E402
from mcp_client import open_mcp  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="OpenSearch MCP Explorer", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AskRequest(BaseModel):
    question: str
    provider: str = ""
    model: str = ""


class BrowseRequest(BaseModel):
    index: str
    size: int = 25
    search: str = ""


class RawQueryRequest(BaseModel):
    """A hand-written Query DSL body, run through SearchIndexTool unchanged."""
    index: str
    query_dsl: dict


@app.get("/")
async def index_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict:
    info = settings.describe()
    try:
        async with open_mcp() as mcp:
            info["mcp_connected"] = True
            info["tool_count"] = len(mcp.tools)
            result = await mcp.call("ListIndexTool", {})
            indices = (result["data"] or {}).get("indices", [])
            info["indices"] = indices
            info["opensearch_reachable"] = not (result["data"] or {}).get("error")
    except Exception as exc:  # noqa: BLE001
        info["mcp_connected"] = False
        info["opensearch_reachable"] = False
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


@app.get("/api/tools")
async def tools() -> dict:
    async with open_mcp() as mcp:
        return {"server_mode": settings.mcp_server_mode, "tools": [t.to_dict() for t in mcp.tools]}


@app.get("/api/indices")
async def indices() -> dict:
    async with open_mcp() as mcp:
        result = await mcp.call("ListIndexTool", {})
        return result["data"] or {"indices": []}


@app.get("/api/mapping/{index}")
async def mapping(index: str) -> dict:
    async with open_mcp() as mcp:
        result = await mcp.call("IndexMappingTool", {"index": index})
        data = result["data"] or {}
        if "error" in data:
            raise HTTPException(status_code=404, detail=data.get("message", "index not found"))
        # Normalise: the tool returns {index: {mappings: {properties: {...}}}}
        for _, body in data.items():
            props = (body or {}).get("mappings", {}).get("properties")
            if props:
                return {
                    "index": index,
                    "fields": [
                        {"name": name, "type": spec.get("type", "object")}
                        for name, spec in sorted(props.items())
                    ],
                    "raw": data,
                }
        return {"index": index, "fields": [], "raw": data}


@app.post("/api/browse")
async def browse(req: BrowseRequest) -> dict:
    """Fetch raw documents so the user can see what is actually in the index."""
    query: dict = {"match_all": {}}
    if req.search.strip():
        query = {
            "bool": {
                "should": [
                    {"match": {"message": req.search}},
                    {"match": {"name": req.search}},
                    {"wildcard": {"product": f"*{req.search}*"}},
                    {"wildcard": {"status": f"*{req.search}*"}},
                    {"wildcard": {"city": f"*{req.search}*"}},
                    {"wildcard": {"service": f"*{req.search}*"}},
                ],
                "minimum_should_match": 1,
            }
        }

    body = {"query": query, "size": max(1, min(req.size, 100))}

    async with open_mcp() as mcp:
        result = await mcp.call(
            "SearchIndexTool",
            {"index": req.index, "query_dsl": body, "size": body["size"]},
        )
        data = result["data"] or {}
        if "error" in data:
            raise HTTPException(status_code=400, detail=data.get("message", "search failed"))
        hits = data.get("hits", {}).get("hits", [])
        docs = [h.get("_source", {}) for h in hits]
        columns: list[str] = []
        for doc in docs:
            for key in doc:
                if key not in columns:
                    columns.append(key)
        return {
            "index": req.index,
            "total": (data.get("hits", {}).get("total") or {}).get("value", 0),
            "columns": columns,
            "documents": docs,
            "query_dsl": body,
        }


@app.post("/api/raw_query")
async def raw_query(req: RawQueryRequest) -> dict:
    """Run a hand-written Query DSL body, exactly as typed.

    This is the manual path, for comparison with /api/ask. No LLM is involved:
    the body you send is the body that reaches OpenSearch. It still travels
    through SearchIndexTool, so the same read-only limits and size clamp apply
    as when the model drives it.
    """
    import time

    started = time.time()
    async with open_mcp() as mcp:
        result = await mcp.call(
            "SearchIndexTool",
            {
                "index": req.index,
                "query_dsl": req.query_dsl,
                "size": int(req.query_dsl.get("size", 10)),
            },
        )
        data = result["data"] or {}
        elapsed = int((time.time() - started) * 1000)

        if "error" in data:
            # Returned as 200 with an error body on purpose: a malformed query
            # is a normal thing to hit while writing one, and the editor should
            # show the message inline rather than treating it as a crash.
            return {
                "ok": False,
                "error": data.get("message", data["error"]),
                "took_ms": elapsed,
            }

        hits = data.get("hits", {}).get("hits", [])
        docs = [h.get("_source", {}) for h in hits]
        columns: list[str] = []
        for doc in docs:
            for key in doc:
                if key not in columns:
                    columns.append(key)

        return {
            "ok": True,
            "index": req.index,
            "total": (data.get("hits", {}).get("total") or {}).get("value", 0),
            "columns": columns,
            "documents": docs,
            "aggregations": data.get("aggregations"),
            "raw": data,
            "took_ms": elapsed,
        }


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    result = await answer_question(question, provider=req.provider, model=req.model)
    return result.to_dict()


def main() -> None:
    import uvicorn

    print("\n  OpenSearch MCP Explorer")
    print(f"  cluster      {settings.opensearch_url}{'  (DEMO MODE - in-memory)' if settings.demo_mode else ''}")
    print(f"  MCP server   {settings.mcp_server_mode}")
    print(f"  LLM          {settings.llm_provider}")
    print("  open         http://127.0.0.1:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
