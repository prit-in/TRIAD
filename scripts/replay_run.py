"""Replay a completed run from its stored artifacts, and check the outputs are identical.

ATC-18: "Completed run replayed -> stored artifacts reused; no LLM calls -> output hashes
identical." NFR-REP-03: "Replay mode reuses stored artifacts and makes no LLM calls."

    python scripts/replay_run.py --latest
    python scripts/replay_run.py --run-key run-2026-09-19-006

What this does, and what makes the check worth anything:

* The **generated Scala is read back from storage**, not re-rendered from the current
  template. If a template has changed since the original run, a re-render would produce
  different code and the replay would be testing today's pipeline rather than reproducing
  yesterday's. `ChurnPipeline._replay_scala` also re-checks the stored source against its
  recorded hash and refuses to replay code that has been altered underneath it.
* The **original output hashes are read before the replay runs**, because a replay commits to
  the same catalogue folders and supersedes what is there. Reading them afterwards would
  compare the replay against itself and pass unconditionally.
* **LLM invocations are counted for the replayed run** and must be zero. With no provider
  configured that is trivially true here, and the check is still worth making: it is the thing
  that would break first if a provider were added and the replay path started asking it
  questions.

The exit code is non-zero when the hashes differ, when no artifacts were stored to replay, or
when any LLM call was made. A replay that cannot prove identity must not report success.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from agents.graph import churn
from agents.graph import queue as q
from agents.graph.pipeline import ReplayUnavailable
from triad_core.config import settings
from triad_core.db import session_scope
from triad_core.models import CodeArtifact, LlmInvocation, PipelineRun
from triad_core.storage import storage

# A run worth replaying is one that got far enough to commit outputs and generate code.
REPLAYABLE_STATES = ("COMPLETED", "PUBLISHED", "SELECTED", "APPROVAL_REQUIRED", "ESCALATED")
OUTPUT_KINDS = ("clean", "features", "labels")


def log(step: str, message: str, ok: bool | None = None) -> None:
    mark = "" if ok is None else ("  OK  " if ok else " FAIL ")
    print(f"[{step:<18}]{mark} {message}", flush=True)


def _pick_run(session: Any, run_key: str | None) -> PipelineRun:
    if run_key:
        run = session.execute(
            select(PipelineRun).where(PipelineRun.run_key == run_key)
        ).scalars().first()
        if run is None:
            raise SystemExit(f"no run with key {run_key!r}")
        return run

    # The newest run that actually produced code artifacts - a run that escalated before
    # generating anything cannot be replayed, so it is not a candidate.
    rows = session.execute(
        select(PipelineRun)
        .join(CodeArtifact, CodeArtifact.run_id == PipelineRun.id)
        .where(PipelineRun.mode != "replay", PipelineRun.state.in_(REPLAYABLE_STATES))
        .group_by(PipelineRun.id)
        .having(func.count(CodeArtifact.id) > 0)
        .order_by(PipelineRun.started_at.desc())
    ).scalars().all()
    if not rows:
        raise SystemExit(
            "no completed run has stored code artifacts to replay; run `make demo` first"
        )
    return rows[0]


def _stored_output_hashes(store: Any, run: PipelineRun) -> dict[str, str | None]:
    """The committed outputs' content hashes, from the manifests the original run wrote.

    The paths come from the run's own `run_state.json` - `clean_uri`, `features_uri`,
    `labels_uri` - and not from a guess at the catalogue layout. The first version of this
    guessed `features_dir("churn", 1)` where the pipeline writes `churn_features`, so two of
    the three outputs were never compared and were reported as "absent", which looked like
    missing data rather than a broken check. Reading the URIs the run recorded cannot drift
    away from what the pipeline does.

    Read *before* the replay, because a replay commits to the same folders.
    """
    out: dict[str, str | None] = {}
    state_path = store.reports_dir(run.run_key) / "run_state.json"
    if not state_path.exists():
        return dict.fromkeys(OUTPUT_KINDS)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict.fromkeys(OUTPUT_KINDS)

    for kind in OUTPUT_KINDS:
        uri = state.get(f"{kind}_uri")
        if not uri:
            out[kind] = None
            continue
        try:
            manifest = store.read_manifest(store.path(uri))
        except Exception as exc:
            # Said out loud. A path that cannot be read is a broken check, and silently
            # returning None made it indistinguishable from an output that was never written.
            log("baseline", f"{kind}: cannot read the manifest at {uri}: {exc}", False)
            out[kind] = None
            continue
        # Only count it if this run is the one that wrote it; another run's output would make
        # the comparison meaningless.
        if manifest.run_id != run.run_key:
            log("baseline", f"{kind}: last written by {manifest.run_id}, not {run.run_key}",
                False)
            out[kind] = None
            continue
        out[kind] = manifest.content_hash
    return out


def _inputs_of(session: Any, run: PipelineRun) -> dict[str, str]:
    """The dataset versions the original run read, so the replay reads exactly the same bytes.

    `pipeline_runs.input_versions` holds the resolved storage URIs the run was given. Runs from
    before that column was written have it empty, and such a run cannot be replayed: guessing
    at "the current version of each dataset" would read different bytes and prove nothing.
    """
    return dict(run.input_versions or {})


def _spark():
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.appName("triad-replay")
        .master(settings().spark_master_url)
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    return session


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest", action="store_true",
                      help="replay the newest run that has stored code artifacts")
    group.add_argument("--run-key", help="replay this run by key")
    args = parser.parse_args()

    store = storage()
    with session_scope() as session:
        original = _pick_run(session, args.run_key)
        log("source", f"replaying {original.run_key} (state {original.state})", True)

        artifacts = session.execute(
            select(CodeArtifact).where(CodeArtifact.run_id == original.id)
        ).scalars().all()
        log("artifacts", f"{len(artifacts)} stored code artifact(s): "
                         + ", ".join(sorted({a.template_id for a in artifacts})), bool(artifacts))
        if not artifacts:
            log("artifacts", "nothing to replay", False)
            return 2

        before = _stored_output_hashes(store, original)
        named = {k: v[:16] if v else "absent" for k, v in before.items()}
        log("baseline", f"original output hashes: {named}",
            any(v for v in before.values()))
        if not any(before.values()):
            log("baseline", "the original run's outputs are no longer in the catalogue, so "
                           "identity cannot be checked", False)
            return 2

        inputs = _inputs_of(session, original)
        if not inputs:
            log("inputs", "the original run recorded no input versions, so the replay cannot "
                          "read the same bytes; re-run the pipeline once and replay that",
                False)
            return 2
        log("inputs", f"{len(inputs)} dataset version(s) reused", True)

        replay = q.create_run(
            session,
            pipeline_name=original.pipeline_name,
            trigger_type="manual",
            triggered_by="replay",
            mode="replay",
            config=dict(original.config_json or {}, replay_of=original.run_key),
            idempotency_key=f"replay-{uuid.uuid4()}",
        )
        # Linked, so the replay is discoverable from the run it reproduces.
        replay.parent_run_id = original.id
        session.flush()
        log("run", f"created {replay.run_key} in mode=replay", True)

        spark = _spark()
        try:
            result = churn.prepare(
                session, replay, inputs=inputs, spark=spark, store=store,
                seed=int((original.config_json or {}).get("seed", 42)),
                log=log, requested_by="replay", replay_of=original,
            )
        except ReplayUnavailable as exc:
            log("replay", str(exc), False)
            q.set_state(session, replay, "ESCALATED", actor="replay", error_summary=str(exc))
            return 2
        finally:
            spark.stop()

        # --- no LLM calls -------------------------------------------------------
        calls = session.execute(
            select(func.count()).select_from(LlmInvocation)
            .where(LlmInvocation.run_id == replay.id)
        ).scalar_one()
        log("llm", f"{calls} LLM invocation(s) during the replay", calls == 0)

        # --- the artifacts really were reused, not regenerated ------------------
        replayed = session.execute(
            select(CodeArtifact).where(CodeArtifact.run_id == replay.id)
        ).scalars().all()
        reused = [a for a in replayed if a.parent_artifact_id is not None]
        same_hash = [
            a for a in replayed
            if a.content_hash in {o.content_hash for o in artifacts}
        ]
        log("reuse", f"{len(reused)}/{len(replayed)} artifact(s) linked to the original, "
                     f"{len(same_hash)} with an identical content hash",
            len(replayed) > 0 and len(reused) == len(replayed))

        # --- identical output hashes -------------------------------------------
        after = _stored_output_hashes(store, replay)
        identical = {
            kind: (before[kind] is not None and before[kind] == after.get(kind))
            for kind in OUTPUT_KINDS if before[kind] is not None
        }
        for kind, ok in identical.items():
            log("hash", f"{kind}: {'identical' if ok else 'DIFFERENT'} "
                        f"({str(before[kind])[:16]} vs {str(after.get(kind))[:16]})", ok)

        q.set_state(session, replay, "CANCELLED", actor="replay")

        ok = (
            result.ok
            and calls == 0
            and bool(identical)
            and all(identical.values())
            and len(reused) == len(replayed)
        )
        log("done", "ATC-18 satisfied: stored artifacts reused, no LLM calls, output hashes "
                    "identical" if ok else "ATC-18 NOT satisfied; see the lines above", ok)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
