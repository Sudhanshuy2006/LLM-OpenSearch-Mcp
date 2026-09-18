"""
demo_engine.py
==============
A small in-process stand-in for OpenSearch.

Why it exists: the POC should be demonstrable on a machine that cannot run
Docker (low RAM, locked-down laptop, CI sandbox). Setting DEMO_MODE=true
swaps the real OpenSearch client for this engine. Everything above it --
the MCP server, the tool schemas, the LLM loop, the web UI -- is unchanged,
so what you see in the trace is the same shape you would see against a real
cluster.

It is NOT a reimplementation of OpenSearch. It supports the slice of Query
DSL this POC actually generates:

  queries        match, match_all, match_phrase, term, terms, range,
                 wildcard, exists, bool(must/should/filter/must_not)
  aggregations   terms, sum, avg, min, max, value_count, cardinality,
                 date_histogram, and sub-aggregations one level deep
  other          size, from, sort, _source, track_total_hits

Anything outside that raises a clear error rather than silently returning
wrong numbers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sample_data import MAPPINGS, build_dataset


class DemoQueryError(Exception):
    """Raised when the query uses a feature this engine does not implement."""


# --------------------------------------------------------------------------
# value helpers
# --------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO date/datetime, or OpenSearch date-math like 'now-30d/d'."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    if value.startswith("now"):
        base = _now()
        rest = value[3:]
        rounding = None
        if "/" in rest:
            rest, rounding = rest.split("/", 1)
        m = re.match(r"^([+-])(\d+)([smhdwMy])$", rest) if rest else None
        if m:
            sign, amount, unit = m.group(1), int(m.group(2)), m.group(3)
            delta = {
                "s": timedelta(seconds=amount),
                "m": timedelta(minutes=amount),
                "h": timedelta(hours=amount),
                "d": timedelta(days=amount),
                "w": timedelta(weeks=amount),
                "M": timedelta(days=30 * amount),
                "y": timedelta(days=365 * amount),
            }[unit]
            base = base - delta if sign == "-" else base + delta
        elif rest:
            raise DemoQueryError(f"Unsupported date math: {value!r}")
        if rounding == "d":
            base = base.replace(hour=0, minute=0, second=0, microsecond=0)
        elif rounding == "h":
            base = base.replace(minute=0, second=0, microsecond=0)
        return base

    if not _DATE_RE.match(value):
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _comparable(value: Any) -> Any:
    """Normalise a field value so range comparisons work on dates and numbers."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    dt = _parse_dt(value)
    return dt if dt is not None else value


def _get_field(doc: dict, path: str) -> Any:
    """Resolve a field path; '.keyword' subfields resolve to the parent value."""
    if path.endswith(".keyword"):
        path = path[: -len(".keyword")]
    current: Any = doc
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _tokens(text: Any) -> list[str]:
    return re.findall(r"[a-z0-9_]+", str(text).lower())


# --------------------------------------------------------------------------
# query matching
# --------------------------------------------------------------------------


def _match_clause(doc: dict, clause: dict) -> bool:
    if len(clause) != 1:
        raise DemoQueryError(f"Expected exactly one query type, got {list(clause)}")
    kind, body = next(iter(clause.items()))

    if kind == "match_all":
        return True

    if kind in ("match", "match_phrase"):
        field, spec = next(iter(body.items()))
        wanted = spec["query"] if isinstance(spec, dict) else spec
        haystack = " ".join(_tokens(_get_field(doc, field)))
        if kind == "match_phrase":
            return " ".join(_tokens(wanted)) in haystack
        needles = _tokens(wanted)
        operator = (spec.get("operator", "or") if isinstance(spec, dict) else "or").lower()
        hay_set = set(haystack.split())
        hits = [n for n in needles if n in hay_set]
        return len(hits) == len(needles) if operator == "and" else bool(hits)

    if kind == "term":
        field, spec = next(iter(body.items()))
        wanted = spec["value"] if isinstance(spec, dict) else spec
        actual = _get_field(doc, field)
        if isinstance(actual, str) and isinstance(wanted, str):
            return actual.lower() == wanted.lower()
        return actual == wanted

    if kind == "terms":
        field, values = next(iter(body.items()))
        actual = _get_field(doc, field)
        lowered = {str(v).lower() for v in values}
        return str(actual).lower() in lowered

    if kind == "range":
        field, spec = next(iter(body.items()))
        actual = _comparable(_get_field(doc, field))
        if actual is None:
            return False
        for op in ("gte", "gt", "lte", "lt"):
            if op not in spec:
                continue
            bound = _comparable(spec[op])
            if isinstance(actual, datetime) != isinstance(bound, datetime):
                return False
            try:
                if op == "gte" and not actual >= bound:
                    return False
                if op == "gt" and not actual > bound:
                    return False
                if op == "lte" and not actual <= bound:
                    return False
                if op == "lt" and not actual < bound:
                    return False
            except TypeError:
                return False
        return True

    if kind == "wildcard":
        field, spec = next(iter(body.items()))
        pattern = spec["value"] if isinstance(spec, dict) else spec
        regex = re.escape(str(pattern)).replace(r"\*", ".*").replace(r"\?", ".")
        actual = _get_field(doc, field)
        return actual is not None and re.fullmatch(regex, str(actual), re.IGNORECASE) is not None

    if kind == "exists":
        return _get_field(doc, body["field"]) is not None

    if kind == "bool":
        must = body.get("must", [])
        should = body.get("should", [])
        filt = body.get("filter", [])
        must_not = body.get("must_not", [])
        must = [must] if isinstance(must, dict) else must
        should = [should] if isinstance(should, dict) else should
        filt = [filt] if isinstance(filt, dict) else filt
        must_not = [must_not] if isinstance(must_not, dict) else must_not

        if not all(_match_clause(doc, c) for c in must):
            return False
        if not all(_match_clause(doc, c) for c in filt):
            return False
        if any(_match_clause(doc, c) for c in must_not):
            return False
        if should:
            minimum = body.get("minimum_should_match", 0 if (must or filt) else 1)
            hits = sum(1 for c in should if _match_clause(doc, c))
            if hits < int(minimum):
                return False
        return True

    raise DemoQueryError(f"Query type '{kind}' is not supported in demo mode")


# --------------------------------------------------------------------------
# aggregations
# --------------------------------------------------------------------------

_METRICS = {
    "sum": lambda vs: float(sum(vs)),
    "avg": lambda vs: (float(sum(vs)) / len(vs)) if vs else None,
    "min": lambda vs: min(vs) if vs else None,
    "max": lambda vs: max(vs) if vs else None,
}


def _run_aggs(docs: list[dict], aggs: dict) -> dict:
    out: dict[str, Any] = {}
    for name, spec in aggs.items():
        sub_aggs = spec.get("aggs") or spec.get("aggregations") or {}
        agg_types = [k for k in spec if k not in ("aggs", "aggregations")]
        if len(agg_types) != 1:
            raise DemoQueryError(f"Aggregation '{name}' must declare exactly one type")
        agg_type = agg_types[0]
        body = spec[agg_type]
        field = body.get("field") if isinstance(body, dict) else None

        if agg_type in _METRICS:
            values = [
                v for v in (_get_field(d, field) for d in docs) if isinstance(v, (int, float))
            ]
            out[name] = {"value": _METRICS[agg_type](values)}

        elif agg_type == "value_count":
            out[name] = {"value": sum(1 for d in docs if _get_field(d, field) is not None)}

        elif agg_type == "cardinality":
            out[name] = {
                "value": len({
                    str(_get_field(d, field)) for d in docs if _get_field(d, field) is not None
                })
            }

        elif agg_type == "stats":
            values = [
                v for v in (_get_field(d, field) for d in docs) if isinstance(v, (int, float))
            ]
            out[name] = {
                "count": len(values),
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "avg": (sum(values) / len(values)) if values else None,
                "sum": float(sum(values)),
            }

        elif agg_type == "terms":
            buckets: dict[Any, list[dict]] = defaultdict(list)
            for d in docs:
                key = _get_field(d, field)
                if key is not None:
                    buckets[key].append(d)
            order_key = "_count"
            order_dir = "desc"
            if isinstance(body.get("order"), dict):
                order_key, order_dir = next(iter(body["order"].items()))

            rows = []
            for key, group in buckets.items():
                row = {"key": key, "doc_count": len(group)}
                if sub_aggs:
                    row.update(_run_aggs(group, sub_aggs))
                rows.append(row)

            def sort_value(row: dict) -> Any:
                if order_key in ("_count", "doc_count"):
                    return row["doc_count"]
                if order_key == "_key":
                    return str(row["key"])
                nested = row.get(order_key)
                return (nested or {}).get("value") or 0

            rows.sort(key=sort_value, reverse=(order_dir == "desc"))
            out[name] = {"buckets": rows[: int(body.get("size", 10))]}

        elif agg_type == "date_histogram":
            interval = body.get("calendar_interval") or body.get("fixed_interval") or "1d"
            buckets: dict[str, list[dict]] = defaultdict(list)
            for d in docs:
                dt = _parse_dt(_get_field(d, field))
                if dt is None:
                    continue
                if interval in ("1d", "day"):
                    key = dt.strftime("%Y-%m-%d")
                elif interval in ("1M", "month"):
                    key = dt.strftime("%Y-%m-01")
                elif interval in ("1h", "hour"):
                    key = dt.strftime("%Y-%m-%dT%H:00:00Z")
                else:
                    key = dt.strftime("%Y-%m-%d")
                buckets[key].append(d)
            rows = []
            for key in sorted(buckets):
                row = {"key_as_string": key, "doc_count": len(buckets[key])}
                if sub_aggs:
                    row.update(_run_aggs(buckets[key], sub_aggs))
                rows.append(row)
            out[name] = {"buckets": rows}

        else:
            raise DemoQueryError(f"Aggregation type '{agg_type}' is not supported in demo mode")

    return out


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------


class DemoOpenSearch:
    """Exposes the handful of opensearch-py methods the MCP tools call."""

    def __init__(self) -> None:
        self._data = build_dataset()
        self._mappings = MAPPINGS

    # -- cluster ----------------------------------------------------------
    def info(self) -> dict:
        return {
            "cluster_name": "demo-in-memory",
            "version": {"number": "2.15.0-demo", "distribution": "opensearch"},
        }

    def ping(self) -> bool:
        return True

    # -- metadata ---------------------------------------------------------
    def list_indices(self) -> list[dict]:
        return [
            {
                "index": name,
                "docs.count": str(len(docs)),
                "health": "green",
                "status": "open",
                "store.size": f"{max(1, len(docs) * 350 // 1024)}kb",
            }
            for name, docs in self._data.items()
        ]

    def get_mapping(self, index: str) -> dict:
        if index not in self._mappings:
            raise DemoQueryError(f"No such index: '{index}'")
        return {index: self._mappings[index]}

    def index_stats(self, index: str) -> dict:
        if index not in self._data:
            raise DemoQueryError(f"No such index: '{index}'")
        docs = self._data[index]
        return {
            "index": index,
            "doc_count": len(docs),
            "size_in_bytes": len(docs) * 350,
            "fields": sorted(self._mappings[index]["mappings"]["properties"]),
        }

    def count(self, index: str) -> int:
        return len(self._data.get(index, []))

    # -- search -----------------------------------------------------------
    def search(self, index: str, body: dict) -> dict:
        if index not in self._data:
            raise DemoQueryError(
                f"No such index: '{index}'. Available: {', '.join(self._data)}"
            )
        body = body or {}
        query = body.get("query", {"match_all": {}})
        docs = [d for d in self._data[index] if _match_clause(d, query)]
        total = len(docs)

        for sort_spec in reversed(body.get("sort", []) or []):
            if isinstance(sort_spec, str):
                field, direction = sort_spec, "asc"
            else:
                field, opts = next(iter(sort_spec.items()))
                direction = (opts.get("order", "asc") if isinstance(opts, dict) else opts)
            docs.sort(
                key=lambda d: (_comparable(_get_field(d, field)) is None,
                               str(_comparable(_get_field(d, field)))),
                reverse=(direction == "desc"),
            )

        offset = int(body.get("from", 0))
        size = int(body.get("size", 10))
        window = docs[offset: offset + size]

        response = {
            "took": 1,
            "timed_out": False,
            "hits": {
                "total": {"value": total, "relation": "eq"},
                "max_score": 1.0 if window else None,
                "hits": [
                    {"_index": index, "_id": str(i), "_score": 1.0, "_source": d}
                    for i, d in enumerate(window)
                ],
            },
        }
        aggs = body.get("aggs") or body.get("aggregations")
        if aggs:
            response["aggregations"] = _run_aggs(docs, aggs)
        return response
