package triad.jobs

import org.apache.spark.sql.functions.{col, to_date}
import org.apache.spark.sql.{DataFrame, SparkSession}
import triad.core.{ChurnTransforms, Inputs, JobConfig, TriadJob}

/**
 * Label job template (FR-030). Labels are produced by a separate job from a separate join,
 * so no label-window column can reach the feature table (hygiene rule H3).
 */
object LabelJob extends TriadJob {

  override def name: String = "churn-labels"

  override def requiredOutputColumns: Seq[String] =
    Seq("customer_id", "snapshot_date", "churned_90d")

  override def outputGrain: Seq[String] = Seq("customer_id", "snapshot_date")

  override def transform(spark: SparkSession, in: Inputs, cfg: JobConfig): DataFrame = {
    // BEGIN GENERATED SLOT: transform
    val daily = ChurnTransforms.customerDaily(in("transactions"), None)
    val expanded = ChurnTransforms.expandToSnapshots(
      daily,
      cfg.maxWindowDays,
      cfg.firstCutoff,
      cfg.lastCutoff
    )
    val eligible = ChurnTransforms.eligibleCustomers(expanded)
    val orderDays = daily.select(col("customer_id"), col("day"))
    ChurnTransforms.labels(eligible, orderDays, cfg.horizonDays, cfg.dataEnd)
    // END GENERATED SLOT: transform
  }
}
