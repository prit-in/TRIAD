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
 *  - a fact that only becomes known after a cutoff (a ticket's resolution, a session's end)
 *    is compared against the cutoff itself after expansion, never pre-aggregated by day,
 *  - labels are computed in a separate job from a separate join.
 *
 * Every feature here has an entry in `configs/feature_catalog.yaml` (rule H7), which also
 * records the four catalogue features this data cannot support and why.
 */
object ChurnTransforms {

  private val Keys = Seq("customer_id", "snapshot_date")

  /** Lines a cleaning job marked as returns. Absent column means no line is a return. */
  private def isReturn(lines: DataFrame): Column =
    if (lines.columns.contains("is_return")) coalesce(col("is_return"), lit(false))
    else lit(false)

  /**
   * Line grain -> customer-day grain, **completed purchases only**.
   *
   * P3 turns a cancellation line into a return: `is_return = true` and a positive quantity.
   * This function used to ignore the flag, so every cancellation counted as an order, added
   * its value to revenue, made its day look like purchasing activity for recency, and -
   * through the label job's order days - labelled a customer whose only activity in the
   * horizon was a cancellation as retained. The generator cancels 2% of lines in every
   * dataset, so every run was affected. Returns are counted by `returnEventsDaily` instead.
   *
   * Running totals and the first order day ride along for the lifetime features, which need
   * history the 365-day expansion does not keep. Each is leakage-safe at any cutoff T: the
   * running total read at the last order day before T counts only days up to that day, and
   * the first order day is at most that same day.
   */
  def customerDaily(lines: DataFrame, products: Option[DataFrame]): DataFrame = {
    val purchases = lines.where(!isReturn(lines))
    val withCategory = products match {
      case Some(p) =>
        purchases.join(
          broadcast(p.select(col("product_id"), col("category_l1"))),
          Seq("product_id"),
          "left"
        )
      case None => purchases.withColumn("category_l1", lit(null).cast("string"))
    }
    val promo =
      if (withCategory.columns.contains("promo_code")) col("promo_code").isNotNull else lit(false)

    val daily = withCategory
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
        // Distinct orders with a code, not lines: "share of orders using a promotion code".
        // Counting lines made the share exceed 1 for any multi-line promoted order.
        countDistinct(when(promo, col("order_id"))).as("promo_orders"),
        // Kept beside promo_orders: the deployed champion was trained on promo_lines_365d, and
        // AP-8 scores the champion on this run's feature table.
        sum(when(promo, 1).otherwise(0)).as("promo_lines"),
        count(lit(1)).as("lines"),
        collect_set(col("category_l1")).as("categories")
      )

    val toDay = Window
      .partitionBy(col("customer_id"))
      .orderBy(col("day"))
      .rowsBetween(Window.unboundedPreceding, Window.currentRow)
    daily
      .withColumn("cum_orders", sum(col("orders")).over(toDay))
      .withColumn("cum_net_revenue", sum(col("net_revenue")).over(toDay))
      .withColumn("first_order_day", min(col("day")).over(Window.partitionBy(col("customer_id"))))
  }

  /**
   * Each day feeds every month-start cutoff T with `day < T` and `T - day <= maxWindowDays`.
   * Requires maxWindowDays >= 31 so the generated month sequence is never empty. Works on any
   * frame with `customer_id` and `day` - purchases, returns, sessions or tickets - and keeps
   * every other column, which is what lets a cutoff-relative check run after expansion.
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
        // Lifetime, not windowed: `max(age_days)` over the expansion could never exceed the
        // 365-day window, so a customer of three years had the tenure of one of one year.
        max(col("first_order_day")).as("first_order_day"),
        max_by(col("cum_orders"), col("day")).as("order_count_lifetime"),
        max_by(col("cum_net_revenue"), col("day")).as("clv_proxy_lifetime"),
        sum(within(30, col("orders"))).as("frequency_30d"),
        sum(within(90, col("orders"))).as("frequency_90d"),
        sum(within(180, col("orders"))).as("frequency_180d"),
        sum(col("orders")).as("frequency_365d"),
        sum(within(90, col("net_revenue"))).as("monetary_90d"),
        sum(col("net_revenue")).as("monetary_365d"),
        sum(col("units")).as("units_365d"),
        sum(col("discounted_lines")).as("discounted_lines_365d"),
        sum(col("promo_orders")).as("promo_orders_365d"),
        sum(col("promo_lines")).as("promo_lines_365d"),
        sum(col("lines")).as("lines_365d"),
        sum(when(month(col("day")).isin(10, 11, 12), 1).otherwise(0)).as("holiday_orders"),
        size(array_distinct(array_compact(flatten(collect_list(col("categories"))))))
          .as("category_count_365d")
      )
      .withColumn("tenure_days", datediff(col("snapshot_date"), col("first_order_day")))
      .drop("first_order_day")
      .withColumn("aov_365d", expr("try_divide(monetary_365d, frequency_365d)"))
      .withColumn("items_per_order_365d", expr("try_divide(units_365d, frequency_365d)"))
      .withColumn("discount_share_365d", expr("try_divide(discounted_lines_365d, lines_365d)"))
      .withColumn("promo_order_share_365d", expr("try_divide(promo_orders_365d, frequency_365d)"))
      .withColumn(
        "trend_90_vs_prior_90",
        col("frequency_90d") - (col("frequency_180d") - col("frequency_90d"))
      )
      .withColumn("snapshot_month", month(col("snapshot_date")))
      .withColumn("holiday_season_buyer", when(col("holiday_orders") > 0, 1).otherwise(0))
      .drop("holiday_orders")

  /**
   * Inter-purchase statistics: gaps between consecutive order days, counted only for gaps
   * that close strictly before the cutoff. Order days are purchase days, so a return no longer
   * shortens a gap it is not part of.
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

  /** Purchase lines per customer, day and payment method, for `preferredPaymentMethod`. */
  def paymentMethodDaily(lines: DataFrame): Option[DataFrame] =
    if (!lines.columns.contains("payment_method")) None
    else
      Some(
        lines
          .where(!isReturn(lines) && col("payment_method").isNotNull)
          .withColumn("day", to_date(col("order_date")))
          .groupBy(col("customer_id"), col("day"), col("payment_method"))
          .agg(count(lit(1)).as("method_lines"))
      )

  /** The most used payment method in the window; ties broken by name, so it is deterministic. */
  def preferredPaymentMethod(expandedPayments: DataFrame): DataFrame = {
    val ranked = Window
      .partitionBy(col("customer_id"), col("snapshot_date"))
      .orderBy(col("method_lines").desc, col("payment_method").asc)
    expandedPayments
      .groupBy(col("customer_id"), col("snapshot_date"), col("payment_method"))
      .agg(sum(col("method_lines")).as("method_lines"))
      .withColumn("rank", row_number().over(ranked))
      .where(col("rank") === 1)
      .select(col("customer_id"), col("snapshot_date"),
              col("payment_method").as("preferred_payment_method"))
  }

  /**
   * Return events per customer and day, **dated by the return**, from both places a return
   * is recorded: cancellation lines P3 marked `is_return`, and the returns table. Dating by
   * the return is the catalogue's leakage guard - a return made on or after T is excluded
   * even when the order it reverses was earlier. None when neither source exists, so the
   * group is marked unavailable rather than reported as zero returns.
   */
  def returnEventsDaily(lines: DataFrame, returns: Option[DataFrame]): Option[DataFrame] = {
    val cancellations =
      if (!lines.columns.contains("is_return")) None
      else
        Some(
          lines
            .where(isReturn(lines))
            .select(
              col("customer_id"),
              to_date(col("order_date")).as("day"),
              abs(col("quantity").cast("double")).as("returned_units")
            )
        )
    val returned = returns.map(
      _.select(
        col("customer_id"),
        to_date(col("return_date")).as("day"),
        abs(col("quantity").cast("double")).as("returned_units")
      )
    )
    Seq(cancellations, returned).flatten.reduceOption(_ unionByName _).map(
      _.where(col("customer_id").isNotNull && col("day").isNotNull)
        .groupBy(col("customer_id"), col("day"))
        .agg(count(lit(1)).as("return_lines"), sum(col("returned_units")).as("returned_units"))
    )
  }

  /** `return_count_365d` and the returned units `return_rate_365d` is computed from. */
  def returnFeatures(expandedReturns: DataFrame): DataFrame =
    expandedReturns
      .groupBy(col("customer_id"), col("snapshot_date"))
      .agg(
        sum(col("return_lines")).as("return_count_365d"),
        sum(col("returned_units")).as("returned_units_365d")
      )

  /**
   * Web sessions at session grain, dated by the session's start. The end day is kept so the
   * length of a session is counted only once the session has ended before the cutoff - "a
   * session that started before T but ended after it" has a start that is known at T and a
   * length that is not.
   */
  def sessionEvents(sessions: DataFrame): DataFrame = {
    val app = if (sessions.columns.contains("channel")) col("channel") === "app" else lit(false)
    sessions
      .where(col("customer_id").isNotNull && col("session_start").isNotNull)
      .select(
        col("customer_id"),
        to_date(col("session_start")).as("day"),
        to_date(col("session_end")).as("end_day"),
        lit(1).as("sessions"),
        coalesce(col("product_views").cast("long"), lit(0L)).as("product_views"),
        coalesce(col("add_to_cart_count").cast("long"), lit(0L)).as("add_to_cart"),
        when(app, 1).otherwise(0).as("app_sessions"),
        ((unix_timestamp(col("session_end")) - unix_timestamp(col("session_start"))) / 60.0)
          .as("session_minutes")
      )
  }

  /** Engagement features from expanded sessions (PRD 16.1, group "Engagement"). */
  def sessionFeatures(expandedSessions: DataFrame): DataFrame = {
    val ended = col("end_day").isNotNull && col("end_day") < col("snapshot_date")
    expandedSessions
      .groupBy(col("customer_id"), col("snapshot_date"))
      .agg(
        min(col("age_days")).as("days_since_last_session"),
        sum(within(30, col("sessions"))).as("sessions_30d"),
        sum(within(90, col("sessions"))).as("sessions_90d"),
        sum(within(90, col("product_views"))).as("product_views_90d"),
        sum(within(90, col("add_to_cart"))).as("add_to_cart_90d"),
        sum(within(90, col("app_sessions"))).as("app_sessions_90d"),
        sum(within(90, when(ended, col("session_minutes")).otherwise(lit(0.0))))
          .as("ended_session_minutes_90d"),
        sum(within(90, when(ended, lit(1)).otherwise(lit(0)))).as("ended_sessions_90d")
      )
      .withColumn("app_session_share_90d", expr("try_divide(app_sessions_90d, sessions_90d)"))
      .withColumn(
        "avg_session_minutes_90d",
        expr("try_divide(ended_session_minutes_90d, ended_sessions_90d)")
      )
      .drop("app_sessions_90d", "ended_session_minutes_90d", "ended_sessions_90d")
  }

  /** Support tickets at ticket grain, dated by creation. Resolution stays a timestamp. */
  def ticketEvents(tickets: DataFrame): DataFrame =
    tickets
      .where(col("customer_id").isNotNull && col("created_at").isNotNull)
      .select(
        col("customer_id"),
        to_date(col("created_at")).as("day"),
        col("created_at").cast("timestamp").as("created_at"),
        col("resolved_at").cast("timestamp").as("resolved_at"),
        when(col("category").isin("delivery", "refund", "quality"), 1).otherwise(0)
          .as("complaint"),
        col("csat_score").cast("double").as("csat_score")
      )

  /**
   * Customer-service features from expanded tickets (PRD 16.1, group "Customer service").
   *
   * Whether a ticket was resolved depends on the cutoff, so it is decided here and not when
   * the tickets are read: a ticket created before T and resolved after T is **open** at T,
   * and neither its resolution time nor its satisfaction score - both recorded at resolution
   * - may count towards T's features. Reading them earlier would leak the future.
   */
  def ticketFeatures(expandedTickets: DataFrame): DataFrame = {
    val resolvedBeforeT =
      col("resolved_at").isNotNull && col("resolved_at") < col("snapshot_date").cast("timestamp")
    expandedTickets
      .groupBy(col("customer_id"), col("snapshot_date"))
      .agg(
        sum(within(90, lit(1))).as("tickets_90d"),
        count(lit(1)).as("tickets_365d"),
        sum(within(90, col("complaint"))).as("complaint_tickets_90d"),
        sum(when(resolvedBeforeT, 0).otherwise(1)).as("open_tickets_at_cutoff"),
        avg(
          when(
            resolvedBeforeT,
            (unix_timestamp(col("resolved_at")) - unix_timestamp(col("created_at"))) / 3600.0
          )
        ).as("avg_resolution_hours_365d"),
        avg(when(resolvedBeforeT, col("csat_score"))).as("avg_csat_365d")
      )
  }

  /** Counts that mean zero when a group's source exists but the customer has no rows in it. */
  private val ZeroWhenAbsent = Seq(
    "return_count_365d", "returned_units_365d",
    "sessions_30d", "sessions_90d", "product_views_90d", "add_to_cart_90d",
    "tickets_90d", "tickets_365d", "complaint_tickets_90d", "open_tickets_at_cutoff"
  )

  /**
   * Left-joins each available feature group onto the purchase base, then fills the counts.
   *
   * The base defines the population - customers with a completed order in the year before
   * T - so a group can only add columns, never rows. A customer with no returns, sessions or
   * tickets in the window gets 0 for those counts rather than null, because the source exists
   * and the count is genuinely zero; a group whose source is missing contributes no columns at
   * all, which is how "unavailable" stays distinguishable from "none".
   */
  def joinGroups(base: DataFrame, groups: Seq[DataFrame]): DataFrame = {
    val joined = groups.foldLeft(base)((acc, group) => acc.join(group, Keys, "left"))
    val present = ZeroWhenAbsent.filter(joined.columns.contains)
    if (present.isEmpty) joined else joined.na.fill(0L, present)
  }

  /** Derived ratios that depend on several groups; applied after the joins. */
  def derivedFeatures(features: DataFrame): DataFrame = {
    def has(c: String) = features.columns.contains(c)
    val steps: Seq[(Boolean, DataFrame => DataFrame)] = Seq(
      has("inter_purchase_mean_days_365d") -> (_.withColumn(
        "overdue_ratio",
        expr("try_divide(recency_days, inter_purchase_mean_days_365d)")
      )),
      has("add_to_cart_90d") -> (_.withColumn(
        "cart_conversion_90d",
        expr("try_divide(frequency_90d, add_to_cart_90d)")
      )),
      // "Returned units divided by bought units" (PRD 16.1).
      has("returned_units_365d") -> (_.withColumn(
        "return_rate_365d",
        expr("try_divide(returned_units_365d, units_365d)")
      ))
    )
    steps.foldLeft(features) { case (acc, (applies, step)) => if (applies) step(acc) else acc }
  }

  /** Days with a completed order: what "activity" means for eligibility and for labels. */
  def purchaseDays(daily: DataFrame): DataFrame =
    daily.where(col("orders") > 0).select(col("customer_id"), col("day"))

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
