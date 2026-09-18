"""
sample_data.py
==============
Generates the demo dataset for the POC.

Why this is generated in code instead of a static JSON file:
the demo questions are things like "orders placed yesterday" and
"users who signed up in the last 30 days". If the dates are hard-coded
in a JSON file, those questions return zero results a week later and the
demo looks broken. Everything here is dated RELATIVE to today, so the
demo keeps working forever.

Three indices are produced:
  users     - customer accounts, with signup_date
  orders    - purchases, with order_date / amount / status / product
  app_logs  - application log lines, with timestamp / level / service / message

Explicit mappings are defined too. Relying on OpenSearch dynamic mapping
works, but then `product` becomes `text` and you must remember to aggregate
on `product.keyword`. Declaring the types up front makes the LLM's job
much easier, because IndexMappingTool then reports unambiguous types.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

# Fixed seed => the same dataset every run => reproducible demo output.
random.seed(20240917)

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.date()


def _iso_date(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _iso_ts(days_ago: int, hour: int, minute: int = 0) -> str:
    d = NOW - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=minute, second=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Mappings
# --------------------------------------------------------------------------

MAPPINGS = {
    "users": {
        "mappings": {
            "properties": {
                "user_id": {"type": "keyword"},
                "name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "email": {"type": "keyword"},
                "city": {"type": "keyword"},
                "plan": {"type": "keyword"},
                "signup_date": {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
            }
        }
    },
    "orders": {
        "mappings": {
            "properties": {
                "order_id": {"type": "keyword"},
                "user_id": {"type": "keyword"},
                "product": {"type": "keyword"},
                "category": {"type": "keyword"},
                "amount": {"type": "double"},
                "quantity": {"type": "integer"},
                "status": {"type": "keyword"},
                "order_date": {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
            }
        }
    },
    "app_logs": {
        "mappings": {
            "properties": {
                "timestamp": {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
                "level": {"type": "keyword"},
                "service": {"type": "keyword"},
                "message": {"type": "text"},
                "trace_id": {"type": "keyword"},
                "latency_ms": {"type": "integer"},
            }
        }
    },
}

# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------

_USER_SEED = [
    ("Aarav Sharma", "Agra", "pro", 2),
    ("Priya Nair", "Kochi", "free", 5),
    ("Rohan Gupta", "Delhi", "pro", 9),
    ("Sneha Patel", "Ahmedabad", "enterprise", 12),
    ("Vikram Singh", "Jaipur", "free", 1),
    ("Ananya Rao", "Bengaluru", "pro", 18),
    ("Kabir Menon", "Mumbai", "free", 26),
    ("Ishita Bose", "Kolkata", "enterprise", 29),
    ("Arjun Reddy", "Hyderabad", "pro", 44),      # outside the 30-day window
    ("Meera Joshi", "Pune", "free", 61),          # outside the 30-day window
    ("Dev Malhotra", "Chandigarh", "pro", 95),    # outside the 30-day window
    ("Tanvi Desai", "Surat", "free", 7),
]


def build_users() -> list[dict]:
    users = []
    for i, (name, city, plan, days_ago) in enumerate(_USER_SEED, start=1):
        users.append(
            {
                "user_id": f"u{i}",
                "name": name,
                "email": f"{name.split()[0].lower()}@example.com",
                "city": city,
                "plan": plan,
                "signup_date": _iso_date(days_ago),
            }
        )
    return users


# --------------------------------------------------------------------------
# orders
# --------------------------------------------------------------------------

_CATALOG = [
    ("Mechanical Keyboard", "peripherals", 3499),
    ("Wireless Mouse", "peripherals", 799),
    ("USB-C Hub", "accessories", 1299),
    ("27-inch Monitor", "displays", 18999),
    ("Webcam 1080p", "peripherals", 2199),
    ("Monitor Stand", "accessories", 1599),
    ("Noise Cancelling Headset", "audio", 5499),
    ("Laptop Sleeve", "accessories", 899),
]

_STATUSES = ["completed", "completed", "completed", "completed", "cancelled", "refunded"]


def build_orders(users: list[dict], count: int = 120) -> list[dict]:
    orders = []
    for i in range(1, count + 1):
        product, category, price = random.choice(_CATALOG)
        qty = random.choices([1, 1, 1, 2, 3], k=1)[0]
        # Weight recent days more heavily so "yesterday" always has data.
        days_ago = random.choices(
            population=list(range(0, 45)),
            weights=[14, 16, 12] + [4] * 12 + [2] * 30,
            k=1,
        )[0]
        orders.append(
            {
                "order_id": f"o{i:04d}",
                "user_id": random.choice(users)["user_id"],
                "product": product,
                "category": category,
                "amount": round(price * qty, 2),
                "quantity": qty,
                "status": random.choice(_STATUSES),
                "order_date": _iso_date(days_ago),
            }
        )

    # Guarantee the "yesterday" demo question has a meaningful answer,
    # regardless of what the random weights happened to produce.
    for j in range(6):
        product, category, price = _CATALOG[j % len(_CATALOG)]
        orders.append(
            {
                "order_id": f"o9{j:03d}",
                "user_id": users[j % len(users)]["user_id"],
                "product": product,
                "category": category,
                "amount": float(price),
                "quantity": 1,
                "status": "completed",
                "order_date": _iso_date(1),
            }
        )
    return orders


# --------------------------------------------------------------------------
# app_logs
# --------------------------------------------------------------------------

_LOG_SEED = [
    (0, 8, "ERROR", "payments", "Payment gateway timeout while processing transaction txn_1001", 30140),
    (0, 9, "ERROR", "payments", "Payment failed: insufficient funds for txn_1002", 210),
    (0, 11, "WARN", "payments", "Retrying payment capture for txn_1003 after gateway 502", 8400),
    (0, 13, "INFO", "checkout", "Checkout session completed for order o0007", 180),
    (1, 7, "ERROR", "payments", "Payment declined by issuing bank for txn_1010", 260),
    (1, 10, "ERROR", "auth", "Token validation failed for session sess_88123", 95),
    (1, 15, "INFO", "search", "Index refresh completed for orders", 440),
    (1, 19, "ERROR", "payments", "Payment webhook signature mismatch for txn_1012", 120),
    (2, 6, "WARN", "inventory", "Stock level below threshold for Mechanical Keyboard", 75),
    (2, 12, "ERROR", "checkout", "Cart serialization error for user u3", 310),
    (3, 9, "ERROR", "payments", "Payment gateway returned HTTP 503 for txn_1030", 30250),
    (3, 14, "INFO", "auth", "Password reset email dispatched to priya@example.com", 210),
    (4, 8, "ERROR", "search", "Query rejected: too many clauses in bool filter", 60),
    (5, 16, "WARN", "payments", "Elevated payment latency detected on gateway-eu", 12400),
    (6, 10, "INFO", "checkout", "Checkout session completed for order o0031", 205),
    (7, 11, "ERROR", "payments", "Payment refund failed for txn_1077, will retry", 1800),
]


def build_logs() -> list[dict]:
    logs = []
    for idx, (days_ago, hour, level, service, message, latency) in enumerate(_LOG_SEED):
        logs.append(
            {
                "timestamp": _iso_ts(days_ago, hour),
                "level": level,
                "service": service,
                "message": message,
                "trace_id": f"trace-{idx:04d}",
                "latency_ms": latency,
            }
        )
    return logs


def build_dataset() -> dict[str, list[dict]]:
    users = build_users()
    return {
        "users": users,
        "orders": build_orders(users),
        "app_logs": build_logs(),
    }


if __name__ == "__main__":
    import json

    data = build_dataset()
    for index, docs in data.items():
        print(f"{index:10s} {len(docs):4d} docs   e.g. {json.dumps(docs[0])}")
