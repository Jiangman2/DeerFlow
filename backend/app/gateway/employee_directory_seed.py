"""Explicit release initialization; never invoked by Gateway startup or GET.

Run with the production agents configuration and the verified shared mount:
python -m app.gateway.employee_directory_seed --initialize --actor release-ID
The operator must freeze publication during this one-time snapshot. Repeated
invocation validates the existing document, preserving every administrator edit.
"""

import argparse
import json

from app.gateway.employee_featured_store import FeaturedStore
from app.gateway.routers.employee_directory import _catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true", required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    store = FeaturedStore()
    created = store.initialize(_catalog("", include_personal=False), actor=args.actor)
    document = store.snapshot().document
    print(json.dumps({"employee_hot_initialization": "CREATED" if created else "PRESERVED", "seed_count": document["seed_count"], "revision": document["revision"], "database_schema_changed": False}))


if __name__ == "__main__":
    main()
