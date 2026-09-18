# 5. Before and after

## The two paths

```
TRADITIONAL
  user → learns Query DSL → writes JSON → OpenSearch → raw JSON → user interprets

MCP + LLM
  user → plain English → LLM → MCP → OpenSearch → JSON → LLM → plain answer
```

The interesting difference is not "less typing". It is *who needs to hold the
schema in their head*. In the first path, the user must already know that the
index is called `orders`, that the date field is `order_date` and not
`created_at`, that `product` is a `keyword` and therefore aggregatable, and that
counting is done with `size: 0` rather than by paging. In the second, the model
discovers all of that at runtime by calling `IndexMappingTool`.

---

## Example 1 — "How many orders were placed yesterday?"

### Before

You need to know the index name, the date field, date-math syntax, and that a
count should not fetch documents.

```json
POST /orders/_search
{
  "size": 0,
  "track_total_hits": true,
  "query": {
    "bool": {
      "filter": [
        { "range": {
            "order_date": {
              "gte": "now-1d/d",
              "lt":  "now/d",
              "format": "strict_date_optional_time||epoch_millis"
        }}}
      ]
    }
  }
}
```

Response:

```json
{ "took": 3, "hits": { "total": { "value": 22, "relation": "eq" }, "hits": [] } }
```

You then read `hits.total.value` and know it means 22.

**Common failure:** writing `"gte": "now-1d"` without `/d`. That means "the last
24 hours", not "yesterday", and silently returns a different number. Nothing
errors.

### After

> **You:** How many orders were placed yesterday?
>
> **Answer:** 22 orders.

Three tool calls happened underneath — `ListIndexTool`, `IndexMappingTool`,
`SearchIndexTool` — and the trace shows the exact DSL above.

---

## Example 2 — "Show me the top 5 products by sales"

### Before

This is where Query DSL gets genuinely hard. Ranking by revenue rather than by
count needs a `terms` bucket, a `sum` sub-aggregation, *and* an `order` clause
pointing at that sub-aggregation by name.

```json
POST /orders/_search
{
  "size": 0,
  "query": { "bool": { "filter": [ { "term": { "status": "completed" } } ] } },
  "aggs": {
    "top_items": {
      "terms": {
        "field": "product",
        "size": 5,
        "order": { "metric": "desc" }
      },
      "aggs": { "metric": { "sum": { "field": "amount" } } }
    }
  }
}
```

**Common failures:** aggregating on `product` when it was dynamically mapped as
`text` (fielddata error — you needed `product.keyword`); forgetting the `order`
clause, which silently ranks by document count instead of revenue; forgetting
the `status` filter and including cancelled orders in the revenue.

### After

> **You:** Show me the top 5 products by sales
>
> **Answer:**
> ```
> 1. 27-inch Monitor          - 10 orders, Rs 322,983 total
> 2. Noise Cancelling Headset -  9 orders, Rs  76,986 total
> 3. Mechanical Keyboard      - 11 orders, Rs  48,986 total
> 4. Monitor Stand            - 15 orders, Rs  36,777 total
> 5. USB-C Hub                - 14 orders, Rs  33,774 total
> ```

Note the model chose to filter on `status: completed` without being told to.
"Sales" implies completed sales. That inference is the part traditional tooling
cannot do for you.

---

## Example 3 — "Find error logs related to payment failures"

### Before

Requires knowing that `level` is a `keyword` (so `term`, exact case `ERROR`)
while `message` is `text` (so `match`, analysed).

```json
POST /app_logs/_search
{
  "size": 10,
  "query": {
    "bool": {
      "must": [
        { "term":  { "level": "ERROR" } },
        { "match": { "message": "payment" } }
      ]
    }
  },
  "sort": [ { "timestamp": { "order": "desc" } } ]
}
```

**Common failure:** using `match` on `level`, or `term` on `message`. The first
works but matches too much; the second returns nothing, because `term` does not
analyse and the indexed tokens are lowercase.

### After

> **You:** Find error logs related to payment failures
>
> **Answer:** 6 matching entries, all from the `payments` service — gateway
> timeout, insufficient funds, declined by issuing bank, webhook signature
> mismatch, HTTP 503, and a failed refund.

---

## Combining tools

Some questions need more than one call. "Which of our newest customers spent the
most?" requires the `users` index for signup dates and the `orders` index for
amounts — OpenSearch has no joins.

Traditionally: run one query, copy the IDs out, paste them into a `terms` filter
for the second query, then correlate by hand.

Over MCP, the model does that itself. It calls `SearchIndexTool` on `users`,
reads the returned `user_id` values from the JSON, and puts them into the next
query's filter. The loop in `agent.py` supports this for free — each tool result
goes back into the conversation, so the next tool call can use it.

---

## What each audience actually gains

**Developers** get exploration without context-switching. Checking what is in an
index during debugging normally means leaving the editor, remembering the
`_cat/indices` syntax, then the mapping syntax. With an MCP server configured in
the IDE it is one sentence. The tools also work *without* an LLM — `/api/browse`
in this project calls `SearchIndexTool` from ordinary Python.

**Analysts** get a much shorter path from question to number. The bottleneck for
an analyst is rarely analysis; it is translating a business question into DSL,
and the surface area of nested aggregations is where most of the time goes.

**End users** get access at all. Someone in support who needs "what errors did
this customer hit yesterday" currently files a ticket and waits. With a
read-only, tool-restricted MCP server they can ask directly.

---

## Where the traditional path still wins

Being honest about this matters, because the comparison above is genuinely
lopsided in favour of MCP and that should make you suspicious.

- **Repeated queries.** A dashboard that runs the same aggregation every minute
  should have that DSL written once and checked into a repo. Paying for model
  inference to regenerate a known query is waste.
- **Correctness-critical numbers.** If the figure goes in a financial report, a
  reviewed query beats a generated one. The model's query is correct most of
  the time, which is not the same as always.
- **Complex relevance tuning.** Function scores, custom analysers, `rank_feature`
  fields — expressing these in English is harder than writing them.
- **Latency.** A hand-written query is one round trip. The MCP path is three
  model inferences plus three queries, typically several seconds.
- **Cost.** Every question costs tokens. At high volume that is real money for
  something a cached query would do free.

The honest framing: MCP wins decisively for **exploration and ad-hoc
questions**, and loses for **production, repeated, correctness-critical
queries**. A good setup uses both — conversation to find the query, then the
query checked into code once it is proven.
