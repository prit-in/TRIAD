package triad.jobs

import org.apache.spark.sql.{DataFrame, SparkSession}
import triad.core.{ChurnTransforms, Inputs, JobConfig, TriadJob}

/**
 * Label job template (FR-030). Labels are produced by a separate job from a separate join,
 * so no label-window column can reach the feature table (hygiene rule H3).
 *
 * The order days are purchase days. A cancellation is a return, not activity: before
 * `customerDaily` excluded returns, a customer whose only activity in the horizon was a
 * cancelled order was labelled retained.
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
    val orderDays = ChurnTransforms.purchaseDays(daily)
    ChurnTransforms.labels(eligible, orderDays, cfg.horizonDays, cfg.dataEnd)
    // END GENERATED SLOT: transform
  }
}
