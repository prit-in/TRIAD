"""The orchestrator worker (PRD sections 12.5, 39; FR-073, AT-11).

    python -m agents.worker

It claims one task at a time with `FOR UPDATE SKIP LOCKED`, holds a lease while it works,
extends that lease with a heartbeat, and completes or fails it. Two workers never get the same
task, and a worker that dies mid-task strands nothing: the lease expires and the reaper puts
the task back, which is what makes an orchestrator crash survivable without a second broker.

The worker is deterministic. It decides *which* handler runs from the task type and nothing
else - no model chooses what happens next, and no handler may invent a state transition the
pipeline did not make.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any

from agents.graph import churn
from agents.graph import queue as q
from triad_core.config import settings
from triad_core.db import session_scope
from triad_core.errors import classify
from triad_core.models import PipelineRun

log = logging.getLogger("triad.worker")

RUN_TASK = "run"
PUBLISH_TASK = "publish"
REAP_EVERY_SECONDS = 30.0


class UnknownTaskType(Exception):
    """A task nobody can run. Failing loudly beats marking it done."""


@dataclass
class Heartbeat:
    """Keeps a lease alive while a long stage runs, and reports if the lease was lost.

    Losing a lease means the reaper already re-queued the task and another worker may be
    running it, so the work in progress must not be written as if it were still owned.
    """

    task_id: Any
    owner: str
    lease_seconds: int
    interval: float
    _stop: threading.Event = None  # type: ignore[assignment]
    _thread: threading.Thread | None = None
    lost: bool = False

    def __post_init__(self) -> None:
        self._stop = threading.Event()

    def start(self) -> Heartbeat:
        self._thread = threading.Thread(target=self._beat, name="lease-heartbeat", daemon=True)
        self._thread.start()
        return self

    def _beat(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                with session_scope() as session:
                    alive = q.heartbeat(
                        session, self.task_id, owner=self.owner,
                        lease_seconds=self.lease_seconds,
                    )
                if not alive:
                    self.lost = True
                    log.warning("lease for task %s was lost; the reaper re-queued it",
                                self.task_id)
                    return
            except Exception:
                log.exception("heartbeat failed for task %s", self.task_id)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def __enter__(self) -> Heartbeat:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


class Worker:
    def __init__(
        self,
        *,
        name: str | None = None,
        lease_seconds: int | None = None,
        poll_interval: float | None = None,
    ) -> None:
        cfg = settings()
        self.name = name or cfg.worker_name
        self.lease_seconds = lease_seconds or cfg.lease_seconds
        self.poll_interval = poll_interval or cfg.poll_interval_seconds
        self.stopping = threading.Event()
        self._spark: Any = None
        self._last_reap = 0.0

    # -- lifecycle -------------------------------------------------------------
    def install_signal_handlers(self) -> None:
        def stop(signum: int, _frame: Any) -> None:
            log.info("signal %s received; finishing the current task then stopping", signum)
            self.stopping.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, stop)

    def spark(self) -> Any:
        """One SparkSession per worker: starting a JVM per task would dominate every run."""
        if self._spark is None:
            from pyspark.sql import SparkSession

            cfg = settings()
            self._spark = (
                SparkSession.builder.appName(f"triad-worker-{self.name}")
                .master(cfg.spark_master_url)
                .config("spark.sql.ansi.enabled", "true")
                .config("spark.sql.session.timeZone", "UTC")
                .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
                .config("spark.sql.shuffle.partitions", "8")
                .config("spark.ui.enabled", "false")
                .config("spark.driver.memory", cfg.spark_driver_memory)
                .getOrCreate()
            )
            self._spark.sparkContext.setLogLevel("ERROR")
        return self._spark

    def close(self) -> None:
        if self._spark is not None:
            self._spark.stop()
            self._spark = None

    # -- the loop --------------------------------------------------------------
    def run_forever(self, *, max_tasks: int | None = None) -> int:
        """Claim and run tasks until stopped. Returns the number of tasks handled."""
        handled = 0
        log.info("worker %s started; lease %ss, poll %.1fs",
                 self.name, self.lease_seconds, self.poll_interval)
        try:
            while not self.stopping.is_set():
                self._reap_if_due()
                task = self._claim()
                if task is None:
                    if max_tasks is not None and handled >= max_tasks:
                        break
                    self.stopping.wait(self.poll_interval)
                    continue

                self.handle(task)
                handled += 1
                if max_tasks is not None and handled >= max_tasks:
                    break
        finally:
            self.close()
        log.info("worker %s stopped after %d task(s)", self.name, handled)
        return handled

    def _claim(self) -> q.ClaimedTask | None:
        with session_scope() as session:
            return q.claim(session, owner=self.name, lease_seconds=self.lease_seconds)

    def _reap_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_reap < REAP_EVERY_SECONDS:
            return
        self._last_reap = now
        with session_scope() as session:
            reaped = q.reap_expired(session)
        if reaped:
            log.warning("re-queued %d task(s) whose lease had expired", reaped)

    # -- one task --------------------------------------------------------------
    def handle(self, task: q.ClaimedTask) -> None:
        log.info("claimed task %s (%s/%s) for run %s",
                 task.id, task.task_type, task.stage, task.run_id)
        beat = Heartbeat(
            task_id=task.id, owner=self.name, lease_seconds=self.lease_seconds,
            interval=max(1.0, self.lease_seconds / 3),
        )
        with beat:
            try:
                with session_scope() as session:
                    q.mark_running(session, task.id)
                result = self._dispatch(task)
            except Exception as exc:
                self._record_failure(task, exc)
                return

        if beat.lost:
            # The lease went; another worker may already own this task. Writing a completion
            # now would overwrite its work, so the outcome is only logged.
            log.error("task %s finished but its lease was lost; not recording completion",
                      task.id)
            return

        with session_scope() as session:
            if result.ok:
                q.complete(session, task.id, output_refs=_refs(result))
                log.info("task %s finished: %s", task.id, result.state)
            else:
                # The classifier already computed the fingerprint, and it is the *normalised*
                # one: two runs failing the same way share it, which is what the circuit
                # breaker counts.
                diagnosis = classify(result.reason)
                q.fail(
                    session, task.id,
                    error_class=diagnosis.error_class.value,
                    fingerprint=diagnosis.fingerprint,
                    escalate=result.state == "ESCALATED",
                )
                log.warning("task %s did not succeed: %s (%s)",
                            task.id, result.reason, result.state)

    def _dispatch(self, task: q.ClaimedTask) -> churn.PhaseResult:
        if task.task_type == RUN_TASK:
            return self._run_phase(task)
        if task.task_type == PUBLISH_TASK:
            return self._publish_phase(task)
        raise UnknownTaskType(
            f"no handler for task type {task.task_type!r}; known types are "
            f"{RUN_TASK!r} and {PUBLISH_TASK!r}"
        )

    def _run_phase(self, task: q.ClaimedTask) -> churn.PhaseResult:
        spark = self.spark()
        with session_scope() as session:
            run = session.get(PipelineRun, task.run_id)
            if run is None:
                return churn.PhaseResult(False, "FAILED", f"run {task.run_id} is missing")
            inputs = {k: v for k, v in (task.input_refs or {}).items() if isinstance(v, str)}
            return churn.prepare(
                session, run, inputs=inputs, spark=spark,
                log=_worker_log, requested_by=run.triggered_by,
            )

    def _publish_phase(self, task: q.ClaimedTask) -> churn.PhaseResult:
        with session_scope() as session:
            run = session.get(PipelineRun, task.run_id)
            if run is None:
                return churn.PhaseResult(False, "FAILED", f"run {task.run_id} is missing")
            return churn.publish(session, run, log=_worker_log)

    def _record_failure(self, task: q.ClaimedTask, exc: BaseException) -> None:
        message = f"{type(exc).__name__}: {exc}"
        log.exception("task %s raised", task.id)
        diagnosis = classify(message)
        with session_scope() as session:
            q.fail(
                session, task.id,
                error_class=diagnosis.error_class.value,
                fingerprint=diagnosis.fingerprint,
                # A task nobody can run will never succeed on a retry, so it escalates
                # immediately rather than burning its attempts.
                escalate=isinstance(exc, UnknownTaskType),
                requeue=diagnosis.recoverable and not isinstance(exc, UnknownTaskType),
            )


def _worker_log(step: str, message: str, ok: bool | None = None) -> None:
    if ok is False:
        log.warning("[%s] %s", step, message)
    else:
        log.info("[%s] %s", step, message)


def _refs(result: churn.PhaseResult) -> dict[str, Any]:
    """Only the references, never the payload: task rows are not a data store."""
    details = result.details or {}
    return {
        "state": result.state,
        "approval_id": details.get("approval_id"),
        "model_version_id": details.get("model_version_id"),
        "features_uri": details.get("features_uri"),
        "labels_uri": details.get("labels_uri"),
        "report_id": details.get("report_id"),
        "drift_report_id": details.get("drift_report_id"),
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    worker = Worker()
    worker.install_signal_handlers()
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
