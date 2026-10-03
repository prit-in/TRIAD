package triad.jobs

import org.apache.spark.sql.{DataFrame, SparkSession}
import triad.core.{ChurnTransforms, Inputs, JobConfig, TriadJob}

/**
 * Feature job template (PRD sections 16, 21.2).
 *
 * Everything outside the slot markers is fixed: the Code Generation Agent may only replace
 * the lines between BEGIN/END GENERATED SLOT, and any diff outside them fails validation
 * gate 2 (FR-038). The default slot body below is the tested implementation used in
 * template-only mode (ADR-0003).
 *
 * The purchase base is always computed. Returns, engagement, customer service and the
 * preferred payment method are computed when their source exists and omitted when it does
 * not; which was which is published as job metrics, so "unavailable" is recorded rather than
 * inferred from a missing column (FR-025: "features compute when sources exist and are marked
 * unavailable otherwise").
 */
object FeatureJob extends TriadJob {

  override def name: String = "churn-features"

  override def requiredOutputColumns: Seq[String] =
    Seq("customer_id", "snapshot_date", "recency_days", "frequency_90d", "monetary_365d")

  override def outputGrain: Seq[String] = Seq("customer_id", "snapshot_date")

  @volatile var availability: Map[String, String] = Map.empty

  override def jobMetrics: Map[String, String] = this.availability

  override def transform(spark: SparkSession, in: Inputs, cfg: JobConfig): DataFrame = {
    val txColumns = in("transactions").columns.toSet
    def state(available: Boolean): String = if (available) "available" else "unavailable"
    this.availability = Map(
      "feature_group_purchase" -> "available",
      "feature_group_returns" -> state(in.has("returns") || txColumns.contains("is_return")),
      "feature_group_engagement" -> state(in.has("web_sessions")),
      "feature_group_customer_service" -> state(in.has("support_tickets")),
      "feature_group_payment" -> state(txColumns.contains("payment_method"))
    )

    // BEGIN GENERATED SLOT: transform
    val transactions = in("transactions")
    def expand(events: DataFrame): DataFrame =
      ChurnTransforms.expandToSnapshots(events, cfg.maxWindowDays, cfg.firstCutoff, cfg.lastCutoff)

    val expanded = expand(ChurnTransforms.customerDaily(transactions, in.get("products")))
    val groups = Seq(
      Some(ChurnTransforms.interPurchaseFeatures(expanded)),
      ChurnTransforms.paymentMethodDaily(transactions)
        .map(days => ChurnTransforms.preferredPaymentMethod(expand(days))),
      ChurnTransforms.returnEventsDaily(transactions, in.get("returns"))
        .map(days => ChurnTransforms.returnFeatures(expand(days))),
      in.get("web_sessions")
        .map(s => ChurnTransforms.sessionFeatures(expand(ChurnTransforms.sessionEvents(s)))),
      in.get("support_tickets")
        .map(t => ChurnTransforms.ticketFeatures(expand(ChurnTransforms.ticketEvents(t))))
    ).flatten
    ChurnTransforms.derivedFeatures(
      ChurnTransforms.joinGroups(ChurnTransforms.windowFeatures(expanded), groups)
    )
    // END GENERATED SLOT: transform
  }
}
