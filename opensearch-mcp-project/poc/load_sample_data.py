"""
load_sample_data.py
===================
Creates the three demo indices in OpenSearch and bulk-indexes the generated
documents into them.

Run this once, after `docker compose up -d` reports OpenSearch as healthy:

    python poc/load_sample_data.py

It is safe to re-run: each index is deleted and recreated from scratch.
"""

from __future__ import annotations

import sys

from opensearchpy import OpenSearch, helpers

from config import settings
from sample_data import MAPPINGS, build_dataset

# Document ID fields, so re-running never creates duplicates.
ID_FIELD = {"users": "user_id", "orders": "order_id", "app_logs": "trace_id"}


def get_client() -> OpenSearch:
    """Build an OpenSearch client from the shared settings object."""
    return OpenSearch(
        hosts=[settings.opensearch_url],
        http_auth=(settings.opensearch_username, settings.opensearch_password),
        use_ssl=settings.opensearch_url.startswith("https"),
        verify_certs=settings.opensearch_verify_certs,
        ssl_show_warn=False,
        timeout=30,
    )


def recreate_index(client: OpenSearch, index: str, body: dict) -> None:
    if client.indices.exists(index=index):
        client.indices.delete(index=index)
        print(f"  dropped existing index '{index}'")
    client.indices.create(index=index, body=body)
    print(f"  created index '{index}' with explicit mappings")


def index_docs(client: OpenSearch, index: str, docs: list[dict]) -> None:
    id_field = ID_FIELD[index]
    actions = [
        {"_index": index, "_id": doc[id_field], "_source": doc} for doc in docs
    ]
    success, errors = helpers.bulk(client, actions, stats_only=False, raise_on_error=False)
    client.indices.refresh(index=index)
    if errors:
        print(f"  WARNING: {len(errors)} documents failed to index into '{index}'")
        print(f"  first failure: {errors[0]}")
    print(f"  indexed {success} documents into '{index}'")


def main() -> int:
    client = get_client()

    try:
        info = client.info()
    except Exception as exc:  # noqa: BLE001 - we want the friendly message
        print("Could not reach OpenSearch.", file=sys.stderr)
        print(f"  URL:   {settings.opensearch_url}", file=sys.stderr)
        print(f"  Error: {exc}", file=sys.stderr)
        print(
            "\nStart it first:  docker compose -f poc/docker-compose.yml up -d\n"
            "Then wait until:  curl -k -u admin:<password> " + settings.opensearch_url,
            file=sys.stderr,
        )
        return 1

    print(
        f"Connected to cluster '{info['cluster_name']}' "
        f"running OpenSearch {info['version']['number']}\n"
    )

    dataset = build_dataset()
    for index, docs in dataset.items():
        print(f"{index}:")
        recreate_index(client, index, MAPPINGS[index])
        index_docs(client, index, docs)
        print()

    counts = {idx: client.count(index=idx)["count"] for idx in dataset}
    print("Sample data loaded. Document counts:")
    for idx, n in counts.items():
        print(f"  {idx:10s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
