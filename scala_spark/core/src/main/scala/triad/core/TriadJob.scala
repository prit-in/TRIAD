package triad.core

import org.apache.spark.sql.{DataFrame, SparkSession}

import java.nio.file.{Files, Paths}
import java.time.Instant
import scala.util.{Failure, Success, Try}

/**
 * The fixed half of every generated Spark job (PRD sections 21.2, 25).
 *
 * `TriadJob` owns argument parsing, the SparkSession, contract-driven input reading, output
 * schema validation, staging writes, metrics and structured error reporting. A generated
 * artifact only ever supplies `transform`, inside declared slot markers, so the I/O and
 * governance surface is never generated.
 */
trait TriadJob {

  /** Job name; becomes the Spark application name, prefixed with the run key. */
  def name: String

  /** The only method a generated artifact fills. */
  def transform(spark: SparkSession, in: Inputs, cfg: JobConfig): DataFrame

  /** Columns the output must contain; the run fails before writing if any are missing. */
  def requiredOutputColumns: Seq[String] = Seq.empty

  /** Grain that must be unique in the output, e.g. Seq("customer_id", "snapshot_date"). */
  def outputGrain: Seq[String] = Seq.empty

  final def main(args: Array[String]): Unit = {
    val cfg = JobConfig.parse(args)
    val spark = buildSession(cfg)
    val started = System.nanoTime()
    var metrics = Map.empty[String, String]

    val result = Try {
      val inputs = Inputs.read(spark, cfg)
      val out = transform(spark, inputs, cfg)
      val validated = OutputValidator.validate(out, requiredOutputColumns, outputGrain, cfg)
      metrics = validated.metrics
      OutputWriter.writeStaging(validated.df, cfg)
      validated
    }

    result match {
      case Success(validated) =>
        val durationMs = (System.nanoTime() - started) / 1000000L
        val report = JobReport(
          job = name,
          runKey = cfg.runKey,
          status = "succeeded",
          durationMs = durationMs,
          sparkAppId = spark.sparkContext.applicationId,
          outputPath = cfg.outputPath,
          rowCount = validated.rowCount,
          schema = validated.schemaMap,
          metrics = metrics,
          error = None
        )
        JobReport.write(report, cfg)
        spark.stop()

      case Failure(err) =>
        val durationMs = (System.nanoTime() - started) / 1000000L
        val report = JobReport(
          job = name,
          runKey = cfg.runKey,
          status = "failed",
          durationMs = durationMs,
          sparkAppId = Try(spark.sparkContext.applicationId).getOrElse("unknown"),
          outputPath = cfg.outputPath,
          rowCount = -1L,
          schema = Map.empty,
          metrics = metrics,
          error = Some(StructuredError.from(err))
        )
        JobReport.write(report, cfg)
        // Structured error on stderr so the Execution Agent never parses a stack trace.
        System.err.println("TRIAD_JOB_ERROR " + report.errorJson)
        spark.stop()
        sys.exit(1)
    }
  }

  private def buildSession(cfg: JobConfig): SparkSession = {
    val builder = SparkSession
      .builder()
      .appName(s"${cfg.runKey}-$name")
      // ANSI mode stays on (CR-19); generated code uses try_cast plus quarantine.
      .config("spark.sql.ansi.enabled", "true")
      .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
      .config("spark.sql.session.timeZone", "UTC")
      .config("spark.sql.adaptive.enabled", "true")
      .config("spark.sql.adaptive.skewJoin.enabled", "true")
      .config("spark.sql.shuffle.partitions", cfg.shufflePartitions.toString)
      .config("spark.sql.autoBroadcastJoinThreshold", cfg.broadcastThresholdBytes.toString)

    cfg.master.foreach(builder.master)
    // Only allowlisted settings reach here; the Optimization Agent cannot add others (LR-10).
    cfg.sparkConf.foreach { case (k, v) => builder.config(k, v) }
    builder.getOrCreate()
  }
}

/** Structured error payload handed back to the repair loop (FR-043). */
final case class StructuredError(
    errorClass: String,
    message: String,
    exceptionType: String,
    stackHead: String
) {
  def toJson: String =
    s"""{"error_class":"${JsonEscape(errorClass)}","exception_type":"${JsonEscape(exceptionType)}",""" +
      s""""message":"${JsonEscape(message)}","stack_head":"${JsonEscape(stackHead)}"}"""
}

object StructuredError {
  def from(err: Throwable): StructuredError = {
    val msg = Option(err.getMessage).getOrElse(err.toString)
    val cls = err match {
      case _: ContractViolation                                      => "CONTRACT_VIOLATION"
      case _: org.apache.spark.sql.AnalysisException                 => "UNRESOLVED_COLUMN"
      case e if e.getClass.getName.contains("OutOfMemoryError")      => "EXECUTOR_OOM"
      case e if msgContains(e, "CAST_INVALID_INPUT")                 => "INVALID_CAST_ANSI"
      case e if msgContains(e, "Path does not exist")                => "STORAGE_ERROR"
      case _                                                         => "UNKNOWN"
    }
    val head = err.getStackTrace.take(3).map(_.toString).mkString(" | ")
    StructuredError(cls, msg, err.getClass.getName, head)
  }

  private def msgContains(e: Throwable, needle: String): Boolean =
    Option(e.getMessage).exists(_.contains(needle))
}

final class ContractViolation(message: String) extends RuntimeException(message)

private object JsonEscape {
  def apply(s: String): String =
    Option(s)
      .getOrElse("")
      .replace("\\", "\\\\")
      .replace("\"", "\\\"")
      .replace("\n", " ")
      .replace("\r", " ")
      .replace("\t", " ")
      .take(2000)
}

/** Machine-readable job outcome written next to the output (PRD 14.1). */
final case class JobReport(
    job: String,
    runKey: String,
    status: String,
    durationMs: Long,
    sparkAppId: String,
    outputPath: String,
    rowCount: Long,
    schema: Map[String, String],
    metrics: Map[String, String],
    error: Option[StructuredError]
) {
  def errorJson: String = error.map(_.toJson).getOrElse("{}")

  def toJson: String = {
    val schemaJson = schema.toSeq.sortBy(_._1)
      .map { case (k, v) => s""""${JsonEscape(k)}":"${JsonEscape(v)}"""" }.mkString(",")
    val metricsJson = metrics.toSeq.sortBy(_._1)
      .map { case (k, v) => s""""${JsonEscape(k)}":"${JsonEscape(v)}"""" }.mkString(",")
    s"""{"job":"${JsonEscape(job)}","run_key":"${JsonEscape(runKey)}",""" +
      s""""status":"$status","duration_ms":$durationMs,""" +
      s""""spark_app_id":"${JsonEscape(sparkAppId)}","output_path":"${JsonEscape(outputPath)}",""" +
      s""""row_count":$rowCount,"schema":{$schemaJson},"metrics":{$metricsJson},""" +
      s""""error":$errorJson,"finished_at":"${Instant.now().toString}"}"""
  }
}

object JobReport {
  def write(report: JobReport, cfg: JobConfig): Unit = {
    cfg.reportPath.foreach { path =>
      val p = Paths.get(path)
      Option(p.getParent).foreach(Files.createDirectories(_))
      Files.write(p, report.toJson.getBytes("UTF-8"))
    }
    println("TRIAD_JOB_REPORT " + report.toJson)
  }
}
