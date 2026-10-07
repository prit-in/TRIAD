package triad.core

import org.apache.spark.scheduler.{SparkListener, SparkListenerStageCompleted, SparkListenerTaskEnd}

import scala.collection.mutable

/** One completed Spark stage, as Spark's own listener API measured it (FR-036). */
final case class StageMetric(
    stageId: Int,
    attempt: Int,
    name: String,
    numTasks: Int,
    durationMs: Long,
    executorRunTimeMs: Long,
    jvmGcTimeMs: Long,
    inputBytes: Long,
    outputBytes: Long,
    shuffleReadBytes: Long,
    shuffleWriteBytes: Long,
    memorySpillBytes: Long,
    diskSpillBytes: Long,
    medianTaskMs: Long,
    maxTaskMs: Long,
    failed: Boolean
) {

  /** Slowest task over the median task: 1.0 is perfectly even, and above ~3 one task dominates. */
  def taskSkew: Double = if (medianTaskMs > 0) maxTaskMs.toDouble / medianTaskMs else 1.0

  def toJson: String =
    s"""{"stage_id":$stageId,"attempt":$attempt,"name":"${JsonEscape(name)}",""" +
      s""""num_tasks":$numTasks,"duration_ms":$durationMs,""" +
      s""""executor_run_time_ms":$executorRunTimeMs,"jvm_gc_time_ms":$jvmGcTimeMs,""" +
      s""""input_bytes":$inputBytes,"output_bytes":$outputBytes,""" +
      s""""shuffle_read_bytes":$shuffleReadBytes,"shuffle_write_bytes":$shuffleWriteBytes,""" +
      s""""memory_spill_bytes":$memorySpillBytes,"disk_spill_bytes":$diskSpillBytes,""" +
      s""""median_task_ms":$medianTaskMs,"max_task_ms":$maxTaskMs,""" +
      s""""task_skew":${"%.3f".formatLocal(java.util.Locale.ROOT, taskSkew)},"failed":$failed}"""
}

/**
 * Collects stage durations, shuffle bytes, spill and task skew for every stage a job runs
 * (FR-036: "from Spark's monitoring API ... stored for every stage").
 *
 * A listener in the job's own JVM rather than the REST monitoring endpoint, because the sandbox
 * has no network and the UI is off (ADR-0004): there is no port to ask, during the job or after.
 * Spark delivers listener events asynchronously, so `stages` is complete only once the listener
 * bus has drained - `SparkContext.stop()` does that, which is why `TriadJob` stops Spark before
 * it writes the report.
 */
final class StageMetricsListener extends SparkListener {

  private val taskTimes = mutable.Map.empty[(Int, Int), mutable.ArrayBuffer[Long]]
  private val completed = mutable.ArrayBuffer.empty[StageMetric]

  override def onTaskEnd(event: SparkListenerTaskEnd): Unit = synchronized {
    Option(event.taskInfo).foreach { info =>
      taskTimes.getOrElseUpdate((event.stageId, event.stageAttemptId), mutable.ArrayBuffer.empty) +=
        info.duration
    }
  }

  override def onStageCompleted(event: SparkListenerStageCompleted): Unit = synchronized {
    val info = event.stageInfo
    val times = taskTimes.remove((info.stageId, info.attemptNumber())).map(_.sorted).getOrElse(Seq.empty)
    val m = Option(info.taskMetrics)
    def metric(read: org.apache.spark.executor.TaskMetrics => Long): Long = m.map(read).getOrElse(0L)
    completed += StageMetric(
      stageId = info.stageId,
      attempt = info.attemptNumber(),
      name = info.name.take(200),
      numTasks = info.numTasks,
      durationMs = (for { s <- info.submissionTime; c <- info.completionTime } yield c - s).getOrElse(0L),
      executorRunTimeMs = metric(_.executorRunTime),
      jvmGcTimeMs = metric(_.jvmGCTime),
      inputBytes = metric(_.inputMetrics.bytesRead),
      outputBytes = metric(_.outputMetrics.bytesWritten),
      shuffleReadBytes = metric(_.shuffleReadMetrics.totalBytesRead),
      shuffleWriteBytes = metric(_.shuffleWriteMetrics.bytesWritten),
      memorySpillBytes = metric(_.memoryBytesSpilled),
      diskSpillBytes = metric(_.diskBytesSpilled),
      medianTaskMs = if (times.isEmpty) 0L else times(times.size / 2),
      maxTaskMs = if (times.isEmpty) 0L else times.last,
      failed = info.failureReason.isDefined
    )
  }

  def stages: Seq[StageMetric] = synchronized(completed.toList)

  /** For callers outside the JVM's type system - PySpark reads this through py4j. */
  def toJson: String = stages.map(_.toJson).mkString("[", ",", "]")
}
