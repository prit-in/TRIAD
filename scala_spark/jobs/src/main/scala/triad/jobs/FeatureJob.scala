package triad.jobs

import org.apache.spark.sql.{DataFrame, SparkSession}
import triad.core.{ChurnTransforms, Inputs, JobConfig, TriadJob}

/**
 * Feature job template (PRD section 21.2).
 *
 * Everything outside the slot markers is fixed: the Code Generation Agent may only replace
 * the lines between BEGIN/END GENERATED SLOT, and any diff outside them fails validation
 * gate 2 (FR-038). The default slot body below is the tested implementation used in
 * template-only mode (ADR-0003).
 */
object FeatureJob extends TriadJob {

  override def name: String = "churn-features"

  override def requiredOutputColumns: Seq[String] =
    Seq("customer_id", "snapshot_date", "recency_days", "frequency_90d", "monetary_365d")

  override def outputGrain: Seq[String] = Seq("customer_id", "snapshot_date")

  override def transform(spark: SparkSession, in: Inputs, cfg: JobConfig): DataFrame = {
    // BEGIN GENERATED SLOT: transform
    val daily = ChurnTransforms.customerDaily(in("transactions"), in.get("products"))
    val expanded = ChurnTransforms.expandToSnapshots(
      daily,
      cfg.maxWindowDays,
      cfg.firstCutoff,
      cfg.lastCutoff
    )
    val base = ChurnTransforms.windowFeatures(expanded)
    val withGaps = base.join(
      ChurnTransforms.interPurchaseFeatures(expanded),
      Seq("customer_id", "snapshot_date"),
      "left"
    )
    ChurnTransforms.derivedFeatures(withGaps)
    // END GENERATED SLOT: transform
  }
}
