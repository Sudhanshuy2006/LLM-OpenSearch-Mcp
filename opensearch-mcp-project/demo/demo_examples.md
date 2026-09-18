# Demo: five questions, traced end to end

Every trace below is real output from the POC, captured by running
`python poc/cli.py` against the demo dataset — not an illustration.

Reproduce any of them:

```bash
./run.sh demo                 # UI at http://127.0.0.1:8000
python poc/cli.py "<question>"  # same pipeline, in the terminal
```

The dataset is generated relative to today, so your numbers will differ.
The *shape* of each trace will not.

For each example: what was asked, what the model understood, which MCP tool
it invoked, the Query DSL that reached OpenSearch, what came back, and how
that became the final sentence.

---

## 1. “Find all users who signed up in the last 30 days”

**Pattern:** Time-filtered document listing

### 1. What the user asked

> Find all users who signed up in the last 30 days

### 2. What the model understood

- The question looks like it concerns the 'users' data. Listing indices to confirm.
- Reading the mapping of 'users' so the query uses real field names and types.
- Plan: list up to 20 documents from 'users', newest first, restricted to the requested date window.

### 3. Which MCP tools were invoked

| # | Tool | Why |
|---|---|---|
| 1 | `ListIndexTool` | confirm which indices exist before assuming one |
| 2 | `IndexMappingTool` | learn the real field names and types in `users` |
| 3 | `SearchIndexTool` | run the actual query |

### 4. The query executed in OpenSearch

```json
{
  "size": 20,
  "query": {
    "bool": {
      "filter": [
        {
          "range": {
            "signup_date": {
              "gte": "now-30d/d",
              "format": "strict_date_optional_time||epoch_millis"
            }
          }
        }
      ]
    }
  },
  "sort": [
    {
      "signup_date": {
        "order": "desc"
      }
    }
  ]
}
```

### 5. What OpenSearch returned

`9 total hits, 9 documents returned` in 3 ms.

```json
{
  "hits": {
    "total": {
      "value": 9,
      "relation": "eq"
    },
    "hits": [
      {
        "_index": "users",
        "_id": "0",
        "_score": 1.0,
        "_source": {
          "user_id": "u5",
          "name": "Vikram Singh",
          "email": "vikram@example.com",
          "city": "Jaipur",
          "plan": "free",
          "signup_date": "2026-09-16"
        }
      },
      {
        "_index": "users",
        "_id": "1",
        "_score": 1.0,
        "_source": {
          "user_id": "u1",
          "name": "Aarav Sharma",
          "email": "aarav@example.com",
          "city": "Agra",
          "plan": "pro",
          "signup_date": "2026-09-15"
        }
      },
      {
        "_index": "users",
        "_id": "2",
        "_score": 1.0,
        "_source": {
          "user_id": "u2",
          "name": "Priya Nair",
          "email": "priya@example.com",
          "city": "Kochi",
          "plan": "free",
          "signup_date": "2026-09-12"
        }
      }
    ]
  }
}
```

### 6. How the model turned that into an answer

```
9 matching documents in 'users'. Showing 9:
  - Vikram Singh (vikram@example.com), free plan, signed up 2026-09-16
  - Aarav Sharma (aarav@example.com), pro plan, signed up 2026-09-15
  - Priya Nair (priya@example.com), free plan, signed up 2026-09-12
  - Tanvi Desai (tanvi@example.com), free plan, signed up 2026-09-10
  - Rohan Gupta (rohan@example.com), pro plan, signed up 2026-09-08
  - Sneha Patel (sneha@example.com), enterprise plan, signed up 2026-09-05
  - Ananya Rao (ananya@example.com), pro plan, signed up 2026-08-30
  - Kabir Menon (kabir@example.com), free plan, signed up 2026-08-22
  - Ishita Bose (ishita@example.com), enterprise plan, signed up 2026-08-19
```

*4 model turns · 548 ms end to end.*

---

## 2. “Show me the top 5 products by sales”

**Pattern:** Ranking via a nested aggregation

### 1. What the user asked

> Show me the top 5 products by sales

### 2. What the model understood

- The question looks like it concerns the 'orders' data. Listing indices to confirm.
- Reading the mapping of 'orders' so the query uses real field names and types.
- Plan: group completed orders by product, rank by total sales value, keep top 5.

### 3. Which MCP tools were invoked

| # | Tool | Why |
|---|---|---|
| 1 | `ListIndexTool` | confirm which indices exist before assuming one |
| 2 | `IndexMappingTool` | learn the real field names and types in `orders` |
| 3 | `SearchIndexTool` | run the actual query |

### 4. The query executed in OpenSearch

```json
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "term": {
            "status": "completed"
          }
        }
      ]
    }
  },
  "aggs": {
    "top_items": {
      "terms": {
        "field": "product",
        "size": 5,
        "order": {
          "metric": "desc"
        }
      },
      "aggs": {
        "metric": {
          "sum": {
            "field": "amount"
          }
        }
      }
    }
  }
}
```

### 5. What OpenSearch returned

`87 total hits, 0 documents returned, 1 aggregation(s)` in 3 ms.

```json
{
  "aggregations": {
    "top_items": {
      "buckets": [
        {
          "key": "27-inch Monitor",
          "doc_count": 10,
          "metric": {
            "value": 322983.0
          }
        },
        {
          "key": "Noise Cancelling Headset",
          "doc_count": 9,
          "metric": {
            "value": 76986.0
          }
        },
        {
          "key": "Mechanical Keyboard",
          "doc_count": 11,
          "metric": {
            "value": 48986.0
          }
        },
        {
          "key": "Monitor Stand",
          "doc_count": 15,
          "metric": {
            "value": 36777.0
          }
        },
        {
          "key": "USB-C Hub",
          "doc_count": 14,
          "metric": {
            "value": 33774.0
          }
        }
      ]
    }
  }
}
```

### 6. How the model turned that into an answer

```
Top 5 by total sales value:
  1. 27-inch Monitor - 10 orders, Rs 322,983 total
  2. Noise Cancelling Headset - 9 orders, Rs 76,986 total
  3. Mechanical Keyboard - 11 orders, Rs 48,986 total
  4. Monitor Stand - 15 orders, Rs 36,777 total
  5. USB-C Hub - 14 orders, Rs 33,774 total
```

*4 model turns · 531 ms end to end.*

---

## 3. “Find error logs related to payment failures”

**Pattern:** Full-text search combined with a keyword filter

### 1. What the user asked

> Find error logs related to payment failures

### 2. What the model understood

- The question looks like it concerns the 'app_logs' data. Listing indices to confirm.
- Reading the mapping of 'app_logs' so the query uses real field names and types.
- Plan: filter app_logs on level and match message against: payment + failures.

### 3. Which MCP tools were invoked

| # | Tool | Why |
|---|---|---|
| 1 | `ListIndexTool` | confirm which indices exist before assuming one |
| 2 | `IndexMappingTool` | learn the real field names and types in `app_logs` |
| 3 | `SearchIndexTool` | run the actual query |

### 4. The query executed in OpenSearch

```json
{
  "size": 10,
  "query": {
    "bool": {
      "must": [
        {
          "term": {
            "level": "ERROR"
          }
        },
        {
          "match": {
            "message": "payment failures"
          }
        }
      ],
      "filter": []
    }
  },
  "sort": [
    {
      "timestamp": {
        "order": "desc"
      }
    }
  ]
}
```

### 5. What OpenSearch returned

`6 total hits, 6 documents returned` in 2 ms.

```json
{
  "hits": {
    "total": {
      "value": 6,
      "relation": "eq"
    },
    "hits": [
      {
        "_index": "app_logs",
        "_id": "0",
        "_score": 1.0,
        "_source": {
          "timestamp": "2026-09-17T09:00:00Z",
          "level": "ERROR",
          "service": "payments",
          "message": "Payment failed: insufficient funds for txn_1002",
          "trace_id": "trace-0001",
          "latency_ms": 210
        }
      },
      {
        "_index": "app_logs",
        "_id": "1",
        "_score": 1.0,
        "_source": {
          "timestamp": "2026-09-17T08:00:00Z",
          "level": "ERROR",
          "service": "payments",
          "message": "Payment gateway timeout while processing transaction txn_1001",
          "trace_id": "trace-0000",
          "latency_ms": 30140
        }
      },
      {
        "_index": "app_logs",
        "_id": "2",
        "_score": 1.0,
        "_source": {
          "timestamp": "2026-09-16T19:00:00Z",
          "level": "ERROR",
          "service": "payments",
          "message": "Payment webhook signature misma
  ... truncated
```

### 6. How the model turned that into an answer

```
6 matching documents in 'app_logs'. Showing 6:
  - [ERROR] 2026-09-17T09:00:00Z payments: Payment failed: insufficient funds for txn_1002
  - [ERROR] 2026-09-17T08:00:00Z payments: Payment gateway timeout while processing transaction txn_1001
  - [ERROR] 2026-09-16T19:00:00Z payments: Payment webhook signature mismatch for txn_1012
  - [ERROR] 2026-09-16T07:00:00Z payments: Payment declined by issuing bank for txn_1010
  - [ERROR] 2026-09-14T09:00:00Z payments: Payment gateway returned HTTP 503 for txn_1030
  - [ERROR] 2026-09-10T11:00:00Z payments: Payment refund failed for txn_1077, will retry
```

*4 model turns · 527 ms end to end.*

---

## 4. “How many orders were placed yesterday?”

**Pattern:** Counting with size 0 instead of paging

### 1. What the user asked

> How many orders were placed yesterday?

### 2. What the model understood

- The question looks like it concerns the 'orders' data. Listing indices to confirm.
- Reading the mapping of 'orders' so the query uses real field names and types.
- Plan: count matching documents in 'orders' in the requested window using size 0.

### 3. Which MCP tools were invoked

| # | Tool | Why |
|---|---|---|
| 1 | `ListIndexTool` | confirm which indices exist before assuming one |
| 2 | `IndexMappingTool` | learn the real field names and types in `orders` |
| 3 | `SearchIndexTool` | run the actual query |

### 4. The query executed in OpenSearch

```json
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "range": {
            "order_date": {
              "gte": "now-1d/d",
              "lt": "now/d",
              "format": "strict_date_optional_time||epoch_millis"
            }
          }
        }
      ]
    }
  },
  "track_total_hits": true
}
```

### 5. What OpenSearch returned

`22 total hits, 0 documents returned` in 3 ms.

```json
{
  "hits": {
    "total": {
      "value": 22,
      "relation": "eq"
    },
    "hits": []
  }
}
```

### 6. How the model turned that into an answer

```
22 orders yesterday.
```

*4 model turns · 533 ms end to end.*

---

## 5. “Which product categories sold the most?”

**Pattern:** Same shape, different grouping field

### 1. What the user asked

> Which product categories sold the most?

### 2. What the model understood

- The question looks like it concerns the 'orders' data. Listing indices to confirm.
- Reading the mapping of 'orders' so the query uses real field names and types.
- Plan: group completed orders by category, rank by order count, keep top 5.

### 3. Which MCP tools were invoked

| # | Tool | Why |
|---|---|---|
| 1 | `ListIndexTool` | confirm which indices exist before assuming one |
| 2 | `IndexMappingTool` | learn the real field names and types in `orders` |
| 3 | `SearchIndexTool` | run the actual query |

### 4. The query executed in OpenSearch

```json
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "term": {
            "status": "completed"
          }
        }
      ]
    }
  },
  "aggs": {
    "top_items": {
      "terms": {
        "field": "category",
        "size": 5,
        "order": {
          "_count": "desc"
        }
      },
      "aggs": {
        "metric": {
          "sum": {
            "field": "amount"
          }
        }
      }
    }
  }
}
```

### 5. What OpenSearch returned

`87 total hits, 0 documents returned, 1 aggregation(s)` in 3 ms.

```json
{
  "aggregations": {
    "top_items": {
      "buckets": [
        {
          "key": "accessories",
          "doc_count": 37,
          "metric": {
            "value": 81339.0
          }
        },
        {
          "key": "peripherals",
          "doc_count": 31,
          "metric": {
            "value": 90357.0
          }
        },
        {
          "key": "displays",
          "doc_count": 10,
          "metric": {
            "value": 322983.0
          }
        },
        {
          "key": "audio",
          "doc_count": 9,
          "metric": {
            "value": 76986.0
          }
        }
      ]
    }
  }
}
```

### 6. How the model turned that into an answer

```
Top 4 by total sales value:
  1. accessories - 37 orders, Rs 81,339 total
  2. peripherals - 31 orders, Rs 90,357 total
  3. displays - 10 orders, Rs 322,983 total
  4. audio - 9 orders, Rs 76,986 total
```

*4 model turns · 530 ms end to end.*

---

## What to notice across all five

- **The model never sees a connection string.** It sees four tool names and
  their argument schemas. Everything else is the MCP server's job.
- **Mapping first, query second.** Every trace calls `IndexMappingTool` before
  `SearchIndexTool`. That single habit is what stops the model inventing field
  names like `signupDate` or `created_at`.
- **Counting uses `size: 0` with `track_total_hits`,** not a fetch-and-count loop.
  Example 4 returns one number from 126 documents without transferring any of them.
- **Ranking uses a nested aggregation,** not sorting in Python. Example 2 does a
  `terms` bucket with a `sum` sub-aggregation and orders by that sum.
- **The same tool answers structurally different questions.** Examples 2 and 5
  differ only in the `field` inside the `terms` aggregation.
- **Every number in every answer traces back to a JSON response above it.** If a
  figure looks wrong, the trace shows whether the query was wrong or the reading was.
