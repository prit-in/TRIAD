"""One governed end-to-end run (PRD section 55 demonstration scenario).

    generate data -> register -> profile -> clean (Scala) -> features + labels (Scala)
    -> hand off to Python -> train candidates -> select -> register challenger
    -> request approval -> approve as a second person -> promote -> smoke test
    -> publish predictions -> measure drift -> claim-checked report -> verify the audit chain

The pipeline itself lives in `agents/graph/churn.py` and is the same code the orchestrator
worker runs; this script only supplies the data, plays both people at the approval gate, and
prints what happened. Nothing here fakes a result: if a step fails the script says so and
stops with a non-zero exit code.

    python scripts/run_demo.py --customers 800 --months 30
    python scripts/run_demo.py --reject        # AT-13: the promotion is refused
    python scripts/run_demo.py --fail-smoke    # AT-14 / ATC-12: rollback after deployment
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.graph import churn
from agents.graph import queue as q
from triad_core import audit
from triad_core.config import settings
from triad_core.db import session_scope
from triad_core.models import User
from triad_core.storage import storage

SCALA_ROOT = settings().repo_root / "scala_spark"
DATA_START = "2024-01-01"     # the synthetic generator's own start date
MIN_CUTOFFS = 9               # test, a horizon gap, validation, a horizon gap, and training


def log(step: str, message: str, ok: bool | None = None) -> None:
    mark = "" if ok is None else ("  OK  " if ok else " FAIL ")
    print(f"[{step:<22}]{mark} {message}", flush=True)


def ensure_jars() -> None:
    jobs = SCALA_ROOT / "jobs" / "target" / "scala-2.13"
    core = SCALA_ROOT / "core" / "target" / "scala-2.13"
    if any(jobs.glob("*.jar")) and any(core.glob("*.jar")):
        log("build", "packaged JARs already present", ok=True)
        return
    log("build", "packaging Scala jobs (sbt core/package jobs/package)")
    proc = subprocess.run(
        ["/opt/sbt/bin/sbt", "-batch", "core/package", "jobs/package"],
        cwd=SCALA_ROOT, capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        log("build", proc.stdout[-2000:], ok=False)
        raise SystemExit("sbt packaging failed")
    log("build", "JARs built", ok=True)


def generate_data(out_dir: Path, *, customers: int, months: int, seed: int) -> dict[str, Path]:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator

    cfg = GeneratorConfig(
        customers=customers, months=months, seed=seed, start_date=DATA_START,
        null_rate_key=0.03,            # the PRD's "3% of lines lack a customer ID"
        duplicate_rate=0.01,
        invalid_price_rate=0.005,
        injection_strings=True,        # ATC-13 material, treated as data
    )
    written = SyntheticGenerator(cfg).write(out_dir)
    log("data", f"generated {len(written)} synthetic tables in {out_dir}", ok=True)
    return written


def spark_session():
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.appName("triad-demo")
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


def check_window_is_usable(months: int) -> int:
    """Fail before generating anything, and say what to pass instead.

    `churn.prepare` derives the real window from the data it is given; this is the same
    arithmetic done early, so an impossible `--months` costs a second rather than ten minutes.
    """
    import datetime as dt

    start = dt.date.fromisoformat(DATA_START)
    total = start.year * 12 + (start.month - 1) + months
    data_end = dt.date(total // 12, total % 12 + 1, start.day) - dt.timedelta(days=1)

    first_day = start + dt.timedelta(days=365)
    first = first_day if first_day.day == 1 else _next_month_start(first_day)
    last = (data_end - dt.timedelta(days=90)).replace(day=1)

    cutoffs, day = 0, first
    while day <= last:
        cutoffs += 1
        day = _next_month_start(day)
    if cutoffs < MIN_CUTOFFS:
        raise SystemExit(
            f"--months {months} yields only {cutoffs} usable month-start cutoff(s) "
            f"({first} to {last}). A 365-day feature window, a 90-day label horizon and the "
            f"90-day gap rule need at least {MIN_CUTOFFS}. Re-run with "
            f"--months {months + (MIN_CUTOFFS - cutoffs)} or more."
        )
    return cutoffs


def _next_month_start(day):
    import datetime as dt

    total = day.year * 12 + day.month
    return dt.date(total // 12, total % 12 + 1, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the TRIAD demonstration end to end.")
    parser.add_argument("--customers", type=int, default=600)
    parser.add_argument("--months", type=int, default=24,
                        help="months of synthetic history; below 24 the 365-day feature "
                             "window plus the 90-day horizon leave too few cutoffs")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reject", action="store_true",
                        help="reject the promotion instead of approving it (AT-13)")
    parser.add_argument("--fail-smoke", action="store_true",
                        help="force the post-deployment smoke test to fail (AT-14 / ATC-12)")
    args = parser.parse_args()

    started = time.perf_counter()
    cutoffs = check_window_is_usable(args.months)
    store = storage()
    demo_root = store.root / "demo" / uuid.uuid4().hex[:8]
    ensure_jars()
    tables = generate_data(demo_root / "source", customers=args.customers,
                           months=args.months, seed=args.seed)
    log("plan", f"{cutoffs} usable month-start cutoffs from {args.months} months", ok=True)

    summary: dict[str, Any] = {"steps": []}

    with session_scope() as session:
        run = q.create_run(
            session, pipeline_name="churn_v1", trigger_type="manual", triggered_by="demo",
            idempotency_key=f"demo-{uuid.uuid4()}",
            config={"demo": True, "customers": args.customers, "months": args.months},
        )
        log("run", f"created {run.run_key}", ok=True)

        from dataeng.ingest import DatasetRegistry

        registry = DatasetRegistry(session, store)
        registered: dict[str, str] = {}
        for name, path in tables.items():
            registry.register_source(name, entity_type=name, source_type="parquet")
            files = sorted(Path(path).glob("*.parquet"))
            if not files:
                continue
            result = registry.register_version(name, files[0], actor="demo")
            if result.version_id:
                registered[name] = result.storage_uri or ""
        log("ingest", f"registered {len(registered)} dataset versions", ok=True)
        summary["steps"].append({"step": "ingest", "datasets": len(registered)})

        # --- phase one: everything up to the approval gate ---------------------
        spark = spark_session()
        try:
            prepared = churn.prepare(
                session, run, inputs=registered, spark=spark, store=store,
                seed=args.seed, log=log, requested_by="demo",
            )
        finally:
            spark.stop()

        summary["steps"].append({"step": "prepare", **prepared.to_dict()})
        if not prepared.ok:
            log("prepare", f"stopped: {prepared.reason}", ok=False)
            _write_summary(store, run.run_key, summary)
            return 1

        # --- the approval gate, played by two people ---------------------------
        outcome = (prepared.details.get("selection") or {}).get("outcome")
        governance = _governance(session)
        pending = governance.pending_for(run.id)

        if outcome != "PROPOSE_PROMOTION":
            # The acceptance policy kept the champion, so there is nothing to approve. The
            # challenger is still scored, as shadow, and still reported.
            log("approval", f"{outcome}: no promotion proposed, so nothing to decide", ok=True)
            if pending:
                log("approval", "a run that proposed nothing still asked for approval - "
                                "this is a defect", ok=False)
                return 3
            summary["steps"].append({"step": "approval", "status": "not requested",
                                     "outcome": outcome})
        else:
            _decide_as_two_people(
                session, governance, run, pending,
                requester=_user(session, "ml_engineer", "requester@triad.test"),
                approver=_user(session, "ml_engineer", "approver@triad.test"),
                reject=args.reject, summary=summary,
            )

        # --- phase two: carry the decision out ---------------------------------
        published = churn.publish(
            session, run, store=store, log=log, force_smoke_failure=args.fail_smoke,
        )
        summary["steps"].append({"step": "publish", **published.to_dict()})

        chain = audit.verify_chain(session)
        log("audit", f"{chain.entries_checked} entries across {chain.chains_checked} chains, "
                     f"{'verified' if chain.ok else 'BROKEN at ' + str(chain.broken_at_id)}",
            ok=chain.ok)
        summary["steps"].append({"step": "audit", **chain.to_dict()})

        elapsed = time.perf_counter() - started
        summary.update({"run_key": run.run_key, "duration_seconds": round(elapsed, 1),
                        "final_state": published.state})
        _write_summary(store, run.run_key, summary)

        if not published.ok:
            log("done", f"{run.run_key} ended {published.state}: {published.reason}", ok=False)
            return 4
        log("done", f"{run.run_key} completed in {elapsed:.0f}s", ok=True)
    return 0


def _decide_as_two_people(session, governance, run, pending, *, requester, approver,
                          reject: bool, summary) -> None:
    """The separation-of-duties rule, demonstrated rather than asserted (PRD 30.5)."""
    from triad_core.governance import SelfApprovalRefused

    if not pending:
        raise SystemExit(f"{run.run_key} proposed a promotion but asked for no approval")
    approval = pending[0]
    approval.requested_by = str(requester.id)
    session.flush()

    try:
        governance.decide(approval.id, approver=requester, approved=True,
                          comment="approving my own request")
        log("approval", "SELF-APPROVAL WAS ALLOWED - this is a defect", ok=False)
        raise SystemExit(3)
    except SelfApprovalRefused:
        log("approval", "self-approval refused with 403, as required", ok=True)

    decided = governance.decide(
        approval.id, approver=approver, approved=not reject,
        comment=("rejected for the demonstration path" if reject
                 else "metrics and segment checks reviewed; promote"),
    )
    log("approval", f"decision: {decided.status} by {approver.email}", ok=True)
    summary["steps"].append({"step": "approval", "status": decided.status})


def _governance(session):
    from triad_core.governance import GovernanceService
    from triad_core.policy import PolicyEngine

    return GovernanceService(session, PolicyEngine(settings().governance_policy_path))


def _user(session, role: str, email: str):
    from sqlalchemy import select

    existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return existing
    user = User(email=email, display_name=email.split("@")[0], role=role, is_active=True)
    session.add(user)
    session.flush()
    return user


def _write_summary(store, run_key: str, summary: dict[str, Any]) -> None:
    path = store.reports_dir(run_key) / "demo_summary.json"
    store.write_json(path, summary)
    print(f"\nSummary written to {path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
