"""EXP-6's evidence that Spark itself saw an executor loss: its own log lines, counted."""

from __future__ import annotations

from experiments.exp06_spark_failures import count_loss_lines

LOG = """\
25/10/10 18:00:00 INFO DAGScheduler: Submitting 2 missing tasks from ResultStage 3
25/10/10 18:00:01 ERROR TaskSchedulerImpl: Lost executor 0 on 172.20.0.4: Command exited with code 137
25/10/10 18:00:01 WARN TaskSetManager: Lost task 1.0 in stage 3.0 (TID 7) (172.20.0.4 executor 0): ExecutorLostFailure (executor 0 exited caused by one of the running tasks) Reason: Command exited with code 137
25/10/10 18:00:01 WARN TaskSetManager: Lost task 0.0 in stage 3.0 (TID 6) (172.20.0.4 executor 0): ExecutorLostFailure (executor 0 exited caused by one of the running tasks)
25/10/10 18:00:09 INFO DAGScheduler: Job 2 finished
"""


def test_spark_s_own_loss_lines_are_counted_and_nothing_else() -> None:
    seen = count_loss_lines(LOG)
    assert (seen["loss_lines"], seen["lost_task_lines"]) == (3, 2)
    assert count_loss_lines("INFO DAGScheduler: Job 0 finished\n")["loss_lines"] == 0
