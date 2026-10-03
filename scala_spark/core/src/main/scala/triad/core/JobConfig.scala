package triad.core

import org.apache.spark.sql.{DataFrame, SparkSession}

/**
 * Job configuration parsed from `--key=value` arguments. Generated code reads it but never
 * builds it, so a job cannot widen its own inputs or outputs.
 */
final case class JobConfig(
    runKey: String,
    inputs: Map[String, String],          // logical name -> URI
    inputFormats: Map[String, String],    // logical name -> parquet|csv|json
    outputPath: String,                   // final location
    stagingPath: String,                  // run-scoped staging (written first)
    quarantinePath: Option[String],
    reportPath: Option[String],
    master: Option[String],
    shufflePartitions: Int,
    broadcastThresholdBytes: Long,
    partitionBy: Seq[String],
    sparkConf: Map[String, String],
    params: Map[String, String]
) {
  def param(key: String): Option[String] = params.get(key)
  def paramInt(key: String, default: Int): Int = params.get(key).map(_.toInt).getOrElse(default)
  def paramLong(key: String, default: Long): Long = params.get(key).map(_.toLong).getOrElse(default)
  def paramStr(key: String, default: String): String = params.getOrElse(key, default)

  // Convenience accessors used by the feature/label templates.
  def maxWindowDays: Int = paramInt("max_window_days", 365)
  def horizonDays: Int = paramInt("horizon_days", 90)
  def firstCutoff: String = paramStr("first_cutoff", "1970-01-01")
  def lastCutoff: String = paramStr("last_cutoff", "2100-01-01")
  /**
   * Last date for which data exists. There is no safe default: labels mature only when
   * `snapshot_date + horizon <= data_end`, so defaulting this to the last cutoff makes every
   * snapshot immature and the job writes no rows - which is how it failed the first time.
   */
  def dataEnd: String =
    params.getOrElse(
      "data_end",
      throw new ContractViolation(
        "--param=data_end is required by this job: it is the last date with data, and a " +
          "label matures only when snapshot_date + horizon_days <= data_end. Pass the real " +
          "end of the input, not the last cutoff."
      )
    )
}

object JobConfig {

  /**
   * Arguments:
   *   --run-key=run-2026-10-05-001
   *   --input=transactions:file:///data/clean/transactions/v=3[:parquet]
   *   --output=file:///data/features/churn/fsv=2
   *   --staging=file:///data/sandbox/run-x/staging/features
   *   --quarantine=... --report=... --master=local[2]
   *   --shuffle-partitions=200 --broadcast-threshold=67108864 --partition-by=snapshot_date
   *   --conf=spark.executor.memory=1g
   *   --param=horizon_days=90
   */
  def parse(args: Array[String]): JobConfig = {
    var runKey = "run-unknown"
    val inputs = scala.collection.mutable.LinkedHashMap.empty[String, String]
    val formats = scala.collection.mutable.LinkedHashMap.empty[String, String]
    var output = ""
    var staging = ""
    var quarantine: Option[String] = None
    var report: Option[String] = None
    var master: Option[String] = None
    var shuffle = 200
    var broadcast = 67108864L
    var partitionBy = Seq.empty[String]
    val conf = scala.collection.mutable.LinkedHashMap.empty[String, String]
    val params = scala.collection.mutable.LinkedHashMap.empty[String, String]

    args.foreach { raw =>
      val arg = raw.trim
      val (key, value) = arg.indexOf('=') match {
        case -1 => (arg, "")
        case i  => (arg.substring(0, i), arg.substring(i + 1))
      }
      key match {
        case "--run-key"   => runKey = value
        case "--output"    => output = value
        case "--staging"   => staging = value
        case "--quarantine" => quarantine = Some(value)
        case "--report"    => report = Some(value)
        case "--master"    => master = Some(value)
        case "--shuffle-partitions" => shuffle = value.toInt
        case "--broadcast-threshold" => broadcast = value.toLong
        case "--partition-by" => partitionBy = value.split(",").map(_.trim).filter(_.nonEmpty).toSeq
        case "--input" =>
          // name:uri[:format]  — the URI itself contains ':' so split carefully.
          val firstColon = value.indexOf(':')
          require(firstColon > 0, s"malformed --input: $value")
          val name = value.substring(0, firstColon)
          val rest = value.substring(firstColon + 1)
          val fmtMatch = "(.*)::(parquet|csv|json)$".r
          rest match {
            case fmtMatch(uri, fmt) => inputs += (name -> uri); formats += (name -> fmt)
            case uri                => inputs += (name -> uri); formats += (name -> "parquet")
          }
        case "--conf" =>
          val i = value.indexOf('=')
          require(i > 0, s"malformed --conf: $value")
          conf += (value.substring(0, i) -> value.substring(i + 1))
        case "--param" =>
          val i = value.indexOf('=')
          require(i > 0, s"malformed --param: $value")
          params += (value.substring(0, i) -> value.substring(i + 1))
        case other => throw new IllegalArgumentException(s"unknown argument: $other")
      }
    }

    require(output.nonEmpty, "--output is required")
    if (staging.isEmpty) staging = output + ".staging"

    JobConfig(
      runKey = runKey,
      inputs = inputs.toMap,
      inputFormats = formats.toMap,
      outputPath = output,
      stagingPath = staging,
      quarantinePath = quarantine,
      reportPath = report,
      master = master,
      shufflePartitions = shuffle,
      broadcastThresholdBytes = broadcast,
      partitionBy = partitionBy,
      sparkConf = conf.toMap,
      params = params.toMap
    )
  }
}

/** Inputs addressed by logical name. Generated code never opens a path itself. */
final class Inputs private (private val frames: Map[String, DataFrame]) {
  def apply(name: String): DataFrame =
    frames.getOrElse(
      name,
      throw new ContractViolation(
        s"input '$name' was not provided; available: ${frames.keys.toSeq.sorted.mkString(", ")}"
      )
    )

  def get(name: String): Option[DataFrame] = frames.get(name)
  def available: Set[String] = frames.keySet
  def has(name: String): Boolean = frames.contains(name)
}

object Inputs {
  def read(spark: SparkSession, cfg: JobConfig): Inputs = {
    val frames = cfg.inputs.map { case (name, uri) =>
      val fmt = cfg.inputFormats.getOrElse(name, "parquet")
      val reader = spark.read.format(fmt)
      val df = fmt match {
        case "csv"  => reader.option("header", "true").option("mode", "PERMISSIVE")
                             .option("columnNameOfCorruptRecord", "_corrupt_record").load(uri)
        case "json" => reader.option("mode", "PERMISSIVE")
                             .option("columnNameOfCorruptRecord", "_corrupt_record").load(uri)
        case _      => reader.load(uri)
      }
      name -> df
    }
    new Inputs(frames)
  }

  /** Test helper: build Inputs directly from DataFrames. */
  def of(pairs: (String, DataFrame)*): Inputs = new Inputs(pairs.toMap)

  /**
   * The first `maxRows` rows of every input, for a sandbox dry run (gate 6, PRD 21.1).
   *
   * Not a random sample, deliberately: computing a sampling fraction needs a full count of
   * the input, which is a scan of the very data a dry run exists to avoid reading. The dry
   * run's job is to fail fast on anything that fails at all - a cast that throws, a column
   * that does not resolve, a plan that does not finish - not to estimate the full output.
   */
  def limited(in: Inputs, maxRows: Long): Inputs = {
    val n = math.max(1L, math.min(maxRows, Int.MaxValue.toLong)).toInt
    new Inputs(in.frames.map { case (name, df) => name -> df.limit(n) })
  }
}
