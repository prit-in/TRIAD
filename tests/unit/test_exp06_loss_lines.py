"""EXP-6's evidence that Spark itself saw an executor loss: its own log lines, counted - and the
script that makes the loss, run for real against stand-in processes."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from experiments.exp06_spark_failures import count_loss_lines, kill_executors_script

LOG = """\
25/10/10 18:00:00 INFO DAGScheduler: Submitting 2 missing tasks from ResultStage 3
25/10/10 18:00:01 ERROR TaskSchedulerImpl: Lost executor 0 on 172.20.0.4: Command exited with code 137
25/10/10 18:00:01 WARN TaskSetManager: Lost task 1.0 in stage 3.0 (TID 7) (172.20.0.4 executor 0): ExecutorLostFailure (executor 0 exited caused by one of the running tasks) Reason: Command exited with code 137
25/10/10 18:00:01 WARN TaskSetManager: Lost task 0.0 in stage 3.0 (TID 6) (172.20.0.4 executor 0): ExecutorLostFailure (executor 0 exited caused by one of the running tasks)
25/10/10 18:00:09 INFO DAGScheduler: Job 2 finished
"""

RUNNING = "INFO Executor: Running task 0.0 in stage 0.0 (TID 0)\n"


def test_spark_s_own_loss_lines_are_counted_and_nothing_else() -> None:
    seen = count_loss_lines(LOG)
    assert (seen["loss_lines"], seen["lost_task_lines"]) == (3, 2)
    assert count_loss_lines("INFO DAGScheduler: Job 0 finished\n")["loss_lines"] == 0


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the script reads /proc, as in the worker")
def test_the_executor_killer_kills_the_new_working_executor_and_nothing_else(tmp_path) -> None:
    """It waits for an executor that was not running when it began and has started a task, by
    its own log, then kills it - and leaves an older one, an idle one, and itself alone. The
    pilots killed, in turn, the script itself and an executor that had not registered yet."""
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)",
               "org.apache.spark.executor.CoarseGrainedExecutorBackend"]
    for name in ("older", "idle", "working"):
        (tmp_path / name).mkdir()
    (tmp_path / "older" / "stderr").write_text(RUNNING)
    (tmp_path / "idle" / "stderr").write_text("INFO Executor: Starting executor ID 1\n")
    older = subprocess.Popen(sleeper, cwd=tmp_path / "older")
    idle = working = None
    try:
        time.sleep(0.5)
        killer = subprocess.Popen([sys.executable, "-c", kill_executors_script(20, 0.5)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.0)
        idle = subprocess.Popen(sleeper, cwd=tmp_path / "idle")
        working = subprocess.Popen(sleeper, cwd=tmp_path / "working")
        time.sleep(1.0)
        (tmp_path / "working" / "stderr").write_text(RUNNING)
        out, err = killer.communicate(timeout=30)
        assert killer.returncode == 0, err
        assert out.strip() == str(working.pid)
        assert working.wait(timeout=10) == -9
        assert older.poll() is None and idle.poll() is None
    finally:
        for proc in (older, idle, working):
            if proc is not None and proc.poll() is None:
                proc.kill()
