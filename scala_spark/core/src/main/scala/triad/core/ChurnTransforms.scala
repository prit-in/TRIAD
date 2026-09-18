package triad.core

import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._
import org.apache.spark.sql.{Column, DataFrame}

/**
 * Point-in-time churn transformations (PRD sections 15.4, 16).
 *
 * These are the tested building blocks a generated slot composes. Keeping them in `core`
 * means the compiler checks them once, fixtures pin their behaviour (TC-026, TC-030), and a
 * generated artifact only decides *which* ones to call with *which* windows.
 *
 * Leakage rules enforced structurally, not by convention:
 *  - `expandToSnapshots` only ever attaches a day to cutoffs strictly after that day,
 *  - every window aggregate reads `age_days` produced by that expansion,
 *  - labels are computed in a separate job from a separate join.
 */
object ChurnTransforms {

  /** Line grain -> customer-day grain. Cancellations have already become returns in P3. */
  def customerDaily(lines: DataFrame, products: Option[DataFrame]): DataFrame = {
    val withCategory = products match {
      case Some(p) =>
        lines.join(
          broadcast(p.select(col("product_id"), col("category_l1"))),
          Seq("product_id"),
          "left"
        )
      case None => lines.withColumn("category_l1", lit(null).cast("string"))
    }

    withCategory
      .withColumn("day", to_date(col("order_date")))
      .withColumn(
        "net_revenue",
        col("quantity").cast("double") * col("price").cast("double")
          - coalesce(col("discount").cast("double"), lit(0.0))
      )
      .groupBy(col("customer_id"), col("day"))
      .agg(
        countDistinct(col("order_id")).as("orders"),
        sum(col("net_revenue")).as("net_revenue"),
        sum(col("quantity").cast("double")).as("units"),
        sum(when(coalesce(col("discount").cast("double"), lit(0.0)) > 0, 1).otherwise(0))
          .as("discounted_lines"),
        sum(when(col("promo_code").isNotNull, 1).otherwise(0)).as("promo_lines"),
        count(lit(1)).as("lines"),
        collect_set(col("category_l1")).as("categories")
      )
  }

  /**
   * Each order day feeds every month-start cutoff T with `day < T` and `T - day <= maxWindowDays`.
   * Requires maxWindowDays >= 31 so the generated month sequence is never empty.
   */
  def expandToSnapshots(
      daily: DataFrame,
      maxWindowDays: Int,
      first: String,
      last: String
  ): DataFrame = {
    require(maxWindowDays >= 31, s"maxWindowDays must be at least 31, got $maxWindowDays")
    daily
      .withColumn(
        "snapshot_date",
        explode(
          sequence(
            add_months(trunc(col("day"), "month"), 1),
            trunc(date_add(col("day"), maxWindowDays), "month"),
            expr("INTERVAL 1 MONTH")
          )
        )
      )
      .where(col("snapshot_date").between(lit(first).cast("date"), lit(last).cast("date")))
      .withColumn("age_days", datediff(col("snapshot_date"), col("day")))
      // Belt and braces: a day may never contribute to a cutoff on or before it (H1).
      .where(col("age_days") > 0 && col("age_days") <= maxWindowDays)
  }

  private def within(days: Int, value: Column): Column =
    when(col("age_days") <= days, value).otherwise(lit(0))

  /** RFM, purchase-behaviour and temporal features at each cutoff. */
  def windowFeatures(expanded: DataFrame): DataFrame =
    expanded
      .groupBy(col("customer_id"), col("snapshot_date"))
      .agg(
        min(col("age_days")).as("recency_days"),
        max(col("age_days")).as("tenure_days"),
        sum(within(30, col("orders"))).as("frequency_30d"),
        sum(within(90, col("orders"))).as("frequency_90d"),
        sum(within(180, col("orders"))).as("frequency_180d"),
        sum(col("orders")).as("frequency_365d"),
        sum(within(90, col("net_revenue"))).as("monetary_90d"),
        sum(col("net_revenue")).as("monetary_365d"),
        sum(col("units")).as("units_365d"),
        sum(col("discounted_lines")).as("discounted_lines_365d"),
        sum(col("promo_lines")).as("promo_lines_365d"),
        sum(col("lines")).as("lines_365d"),
        sum(when(month(col("day")).isin(10, 11, 12), 1).otherwise(0)).as("holiday_orders"),
        size(array_distinct(array_compact(flatten(collect_list(col("categories"))))))
          .as("category_count_365d")
      )
      .withColumn("aov_365d", expr("try_divide(monetary_365d, frequency_365d)"))
      .withColumn("items_per_order_365d", expr("try_divide(units_365d, frequency_365d)"))
      .withColumn("discount_share_365d", expr("try_divide(discounted_lines_365d, lines_365d)"))
      .withColumn("promo_order_share_365d", expr("try_divide(promo_lines_365d, frequency_365d)"))
      .withColumn(
        "trend_90_vs_prior_90",
        col("frequency_90d") - (col("frequency_180d") - col("frequency_90d"))
      )
      .withColumn("snapshot_month", month(col("snapshot_date")))
      .withColumn("holiday_season_buyer", when(col("holiday_orders") > 0, 1).otherwise(0))
      .drop("holiday_orders")

  /**
   * Inter-purchase statistics: gaps between consecutive order days, counted only for gaps
   * that close strictly before the cutoff.
   */
  def interPurchaseFeatures(expanded: DataFrame): DataFrame = {
    val w = Window.partitionBy(col("customer_id"), col("snapshot_date")).orderBy(col("day"))
    expanded
      .withColumn("prev_day", lag(col("day"), 1).over(w))
      .withColumn("gap_days", datediff(col("day"), col("prev_day")))
      .groupBy(col("customer_id"), col("snapshot_date"))
      .agg(
        avg(col("gap_days")).as("inter_purchase_mean_days_365d"),
        stddev_samp(col("gap_days")).as("inter_purchase_std_days_365d")
      )
  }

  /** Engagement features from sessions, expanded to the same cutoffs. */
  def sessionFeatures(expandedSessions: DataFrame): DataFrame =
    expandedSessions
      .groupBy(col("customer_id"), col("snapshot_date"))
      .agg(
        min(col("age_days")).as("days_since_last_session"),
        sum(within(30, col("sessions"))).as("sessions_30d"),
        sum(within(90, col("sessions"))).as("sessions_90d"),
        sum(within(90, col("product_views"))).as("product_views_90d"),
        sum(within(90, col("add_to_cart"))).as("add_to_cart_90d"),
        sum(within(90, col("app_sessions"))).as("app_sessions_90d"),
        sum(within(90, col("session_minutes"))).as("session_minutes_90d")
      )
      .withColumn("app_session_share_90d", expr("try_divide(app_sessions_90d, sessions_90d)"))
      .withColumn("avg_session_minutes_90d", expr("try_divide(session_minutes_90d, sessions_90d)"))
      .drop("app_sessions_90d", "session_minutes_90d")

  /** Customer-service features from tickets, expanded to the same cutoffs. */
  def ticketFeatures(expandedTickets: DataFrame): DataFrame =
    expandedTickets
      .groupBy(col("customer_id"), col("snapshot_date"))
      .agg(
        sum(within(90, col("tickets"))).as("tickets_90d"),
        sum(col("tickets")).as("tickets_365d"),
        sum(within(90, col("complaint_tickets"))).as("complaint_tickets_90d"),
        sum(col("open_at_cutoff")).as("open_tickets_at_cutoff"),
        sum(col("resolution_hours")).as("resolution_hours_sum"),
        sum(col("resolved_tickets")).as("resolved_tickets"),
        sum(col("csat_sum")).as("csat_sum"),
        sum(col("csat_count")).as("csat_count")
      )
      .withColumn("avg_resolution_hours_365d", expr("try_divide(resolution_hours_sum, resolved_tickets)"))
      .withColumn("avg_csat_365d", expr("try_divide(csat_sum, csat_count)"))
      .drop("resolution_hours_sum", "resolved_tickets", "csat_sum", "csat_count")

  /** Derived ratios that depend on several groups; applied after the joins. */
  def derivedFeatures(features: DataFrame): DataFrame = {
    val withOverdue =
      if (features.columns.contains("inter_purchase_mean_days_365d"))
        features.withColumn(
          "overdue_ratio",
          expr("try_divide(recency_days, inter_purchase_mean_days_365d)")
        )
      else features
    if (withOverdue.columns.contains("add_to_cart_90d"))
      withOverdue.withColumn("cart_conversion_90d", expr("try_divide(frequency_90d, add_to_cart_90d)"))
    else withOverdue
  }

  /**
   * churned_90d = 1 when an eligible customer has no completed order in [T, T + horizon).
   * Snapshots whose horizon passes the end of the data are dropped as immature.
   */
  def labels(
      eligible: DataFrame,
      orderDays: DataFrame,
      horizonDays: Int,
      dataEnd: String
  ): DataFrame = {
    val e = eligible.select(col("customer_id"), col("snapshot_date")).alias("e")
    val o = orderDays.select(col("customer_id"), col("day")).alias("o")
    e.join(
        o,
        col("e.customer_id") === col("o.customer_id")
          && col("o.day") >= col("e.snapshot_date")
          && col("o.day") < date_add(col("e.snapshot_date"), horizonDays),
        "left"
      )
      .groupBy(col("e.customer_id").as("customer_id"), col("e.snapshot_date").as("snapshot_date"))
      .agg(max(when(col("o.day").isNotNull, 1).otherwise(0)).as("has_future_order"))
      .where(date_add(col("snapshot_date"), horizonDays) <= lit(dataEnd).cast("date"))
      .withColumn("churned_90d", lit(1) - col("has_future_order"))
      .drop("has_future_order")
  }

  /**
   * Eligible population: at least one completed order in the 365 days before T
   * (PRD section 17.1). Derived from the same expansion, so it is point-in-time by construction.
   */
  def eligibleCustomers(expanded: DataFrame): DataFrame =
    expanded
      .where(col("age_days") <= 365 && col("orders") > 0)
      .select(col("customer_id"), col("snapshot_date"))
      .distinct()
}
