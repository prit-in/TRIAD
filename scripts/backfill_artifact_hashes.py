"""Record artifact hashes for model versions registered before hashes were recorded (one-off).

    python scripts/backfill_artifact_hashes.py            # report what would be recorded
    python scripts/backfill_artifact_hashes.py --apply    # record it

`ModelRegistry.load` now refuses a model with no recorded hash (ST-08), and until 2026-10-03
no version had one: `artifact_sha256` was NULL on every row because nothing computed it. This
hashes what the artifact store holds **today** and records it.

That is trust on first use, and it is labelled as such: a hash taken now proves the artifact
has not changed *since now*, not that it was never changed before. Every recorded hash says so
in its audit entry, and versions registered after the fix carry a hash taken at registration
instead, which is the stronger record.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from python_ml.triad_ml.registry import ModelRegistry, artifact_hash
from triad_core import audit
from triad_core.config import settings
from triad_core.db import session_scope
from triad_core.ids import utcnow
from triad_core.models import Model, ModelVersion


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill model artifact hashes.")
    parser.add_argument("--apply", action="store_true", help="record the hashes")
    args = parser.parse_args()

    registry = ModelRegistry(settings().mlflow_tracking_uri)
    with session_scope() as session:
        rows = session.execute(
            select(ModelVersion, Model)
            .join(Model, Model.id == ModelVersion.model_id)
            .where(ModelVersion.artifact_sha256.is_(None),
                   ModelVersion.mlflow_version.is_not(None))
            .order_by(ModelVersion.created_at)
        ).all()
        print(f"{len(rows)} registered version(s) without a recorded hash")

        recorded = 0
        for version, model in rows:
            name = model.mlflow_registered_name or model.name
            uri = f"models:/{name}/{version.mlflow_version}"
            try:
                sha = artifact_hash(registry.mlflow.artifacts.download_artifacts(artifact_uri=uri))
            except Exception as exc:
                print(f"  {name} v{version.mlflow_version}: not hashed ({type(exc).__name__}: {exc})")
                continue
            print(f"  {name} v{version.mlflow_version}: {sha[:16]}...")
            if not args.apply:
                continue
            version.artifact_sha256 = sha
            registry.set_version_tag(name, version.mlflow_version, "artifact_sha256", sha)
            audit.record(
                session,
                actor_type="system",
                actor_id="backfill_artifact_hashes",
                action="model_version.hash_recorded",
                resource_type="model_version",
                resource_id=str(version.id),
                run_id=version.run_id,
                outcome="backfilled",
                details={
                    "artifact_sha256": sha,
                    "basis": "trust on first use: hashed from the artifact store on "
                             f"{utcnow().date().isoformat()}, not at registration",
                },
            )
            recorded += 1

        if not args.apply:
            print("\ndry run: nothing written. Re-run with --apply to record these.")
            session.rollback()
            return 1
        print(f"\nrecorded {recorded} hash(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
