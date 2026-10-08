"""Generate a synthetic dataset and register its tables under the names the pipeline reads.

    python scripts/prepare_demo_data.py --customers 2000                    # a clean version
    python scripts/prepare_demo_data.py --customers 2000 --fault key-nulls  # failure injection

Every table becomes the next version of its dataset (`transactions`, `customers`, ...), so the
next run started from the dashboard reads it. A fault is put into the *data* by the generator,
never into the code, so a demonstration can show each defence without touching the source:

    renamed-key    transactions.customer_id renamed: the ingest contract refuses the version
    price-as-text  prices as text with currency symbols: try_cast and quarantine, then the
                   cleaning policy decides whether the share of rejected rows is within limits
    key-nulls      3% of order lines without a customer key: quarantined, rows still accounted
    drift          customer behaviour shifts from month 18: profiling reports the shift and the
                   drift monitor and retraining triggers fire on the next scored run

Registration goes through `DatasetRegistry`, the same code `POST /datasets` uses, so contracts,
checksums and the audit trail apply exactly as they do to an upload.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator  # noqa: E402
from dataeng.ingest import DatasetRegistry  # noqa: E402
from triad_core.db import session_scope  # noqa: E402
from triad_core.storage import storage  # noqa: E402

ENTITY = {"transactions": "transaction", "customers": "customer", "products": "product",
          "returns": "return", "web_sessions": "web_session",
          "support_tickets": "support_ticket", "promotions": "promotion"}
FAULTS = {
    "none": {},
    "renamed-key": {"rename_column": "customer_id:cust_id"},
    "price-as-text": {"price_as_text": True},
    "key-nulls": {"null_rate_key": 0.03},
    "drift": {"drift_from_month": 18},
}
CONTRACTS = Path(__file__).resolve().parents[1] / "pipelines" / "contracts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customers", type=int, default=2000)
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fault", choices=sorted(FAULTS), default="none")
    args = parser.parse_args()

    store = storage()
    folder = store.root / "incoming" / f"demo-{args.fault}-{args.customers}-{uuid.uuid4().hex[:6]}"
    written = SyntheticGenerator(GeneratorConfig(
        customers=args.customers, months=args.months, seed=args.seed, **FAULTS[args.fault],
    )).write(folder)
    print(f"generated {len(written)} tables in {folder}")

    refused = 0
    with session_scope() as session:
        registry = DatasetRegistry(session, store)
        for table, entity in ENTITY.items():
            files = sorted((folder / table).glob("*.parquet"))
            if not files:
                print(f"  {table:<16} not generated")
                continue
            contract = CONTRACTS / f"{table}.yaml"
            registry.register_source(
                table, entity_type=entity, source_type="parquet",
                contract_yaml=contract.read_text(encoding="utf-8") if contract.exists() else None,
                actor="demo-setup",
            )
            result = registry.register_version(table, files[0], actor="demo-setup")
            if result.version_id and result.created:
                print(f"  {table:<16} registered as version {result.version_no}")
            elif result.version_id:
                print(f"  {table:<16} unchanged: identical to version {result.version_no}")
            else:
                refused += 1
                print(f"  {table:<16} REFUSED: {result.reason}")
    # A refusal is the expected outcome of `--fault renamed-key`, and is what that fault shows.
    return 0 if refused == 0 or args.fault != "none" else 1


if __name__ == "__main__":
    raise SystemExit(main())
