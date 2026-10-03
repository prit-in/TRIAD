package triad.core

import org.apache.spark.sql.{DataFrame, Row}
import org.apache.spark.sql.functions.col

/**
 * FR-025 and PRD section 16: the feature catalogue, group by group, against `ChurnFixtures`.
 *
 * Until this suite existed the feature job read transactions and products and nothing else.
 * `sessionFeatures` and `ticketFeatures` were written and never called, there was no returns
 * feature at all, `tenure_days` could not exceed 365 because it was measured inside the
 * 365-day window, and a cancellation counted as a purchase. FR-025 was marked met.
 */
class FeatureCatalogueSpec extends SparkFixture with ChurnFixtures {

  /** The composition the feature template's slot performs, built from the same functions. */
  private def featuresAt(
      withPostCutoff: Boolean = true,
      withReturns: Boolean = true,
      withSessions: Boolean = true,
      withTickets: Boolean = true
  ): DataFrame = {
    val lines = cleanLines(withPostCutoff)
    def expand(events: DataFrame) = ChurnTransforms.expandToSnapshots(events, 365, Cutoff, Cutoff)
    val expanded = expand(ChurnTransforms.customerDaily(lines, Some(products)))
    val groups = Seq(
      Some(ChurnTransforms.interPurchaseFeatures(expanded)),
      ChurnTransforms.paymentMethodDaily(lines).map(d => ChurnTransforms.preferredPaymentMethod(expand(d))),
      ChurnTransforms
        .returnEventsDaily(lines, if (withReturns) Some(returnsTable(withPostCutoff)) else None)
        .map(d => ChurnTransforms.returnFeatures(expand(d))),
      if (withSessions)
        Some(ChurnTransforms.sessionFeatures(expand(ChurnTransforms.sessionEvents(sessions(withPostCutoff)))))
      else None,
      if (withTickets)
        Some(ChurnTransforms.ticketFeatures(expand(ChurnTransforms.ticketEvents(tickets(withPostCutoff)))))
      else None
    ).flatten
    ChurnTransforms.derivedFeatures(
      ChurnTransforms.joinGroups(ChurnTransforms.windowFeatures(expanded), groups)
    )
  }

  private def byCustomer(df: DataFrame): Map[String, Row] =
    df.collect().map(r => r.getAs[String]("customer_id") -> r).toMap

  test("FR-025: every implemented catalogue feature matches the hand-checked fixture at T") {
    val rows = byCustomer(featuresAt())
    assert(rows.keySet == Set("C1", "C2", "C3"))
    assertFeatures(rows("C1"), Expected.c1, "C1")
    assertFeatures(rows("C2"), Expected.c2, "C2")
    assertFeatures(rows("C3"), Expected.c3, "C3")
  }

  test("a cancellation is a return, not a purchase: it moves no RFM value") {
    val c2 = byCustomer(featuresAt())("C2")
    assert(c2.getAs[Int]("recency_days") == 7, "the 03-28 cancellation must not be activity")
    assert(c2.getAs[Long]("frequency_365d") == 1L)
    assert(math.abs(c2.getAs[Double]("monetary_365d") - 500.0) < 1e-9)
    assert(c2.getAs[Long]("return_count_365d") == 1L)
  }

  test("tenure and the lifetime features reach history older than the 365-day window") {
    val c1 = byCustomer(featuresAt())("C1")
    assert(c1.getAs[Int]("tenure_days") == 781, "tenure was capped at the window before")
    assert(c1.getAs[Long]("order_count_lifetime") == 4L)
    assert(math.abs(c1.getAs[Double]("clv_proxy_lifetime") - 630.0) < 1e-9)
    // ...while nothing windowed sees the 2024 order.
    assert(c1.getAs[Long]("frequency_365d") == 3L)
  }

  test("TC-026, every group: events on or after T change no feature value") {
    val ordered = Seq("customer_id")
    val without = featuresAt(withPostCutoff = false).orderBy(ordered.map(col): _*).collect()
    val withAll = featuresAt(withPostCutoff = true).orderBy(ordered.map(col): _*).collect()
    assert(without.length == withAll.length, "post-cutoff events added or removed a customer")
    without.zip(withAll).foreach { case (before, after) =>
      before.schema.fieldNames.foreach { name =>
        assert(
          before.getAs[Any](name) == after.getAs[Any](name),
          s"${before.getAs[String]("customer_id")}.$name changed when events after T were added: " +
            s"${before.getAs[Any](name)} -> ${after.getAs[Any](name)}"
        )
      }
    }
  }

  test("a ticket resolved after T is open at T and contributes neither its time nor its score") {
    val c1 = byCustomer(featuresAt())("C1")
    assert(c1.getAs[Long]("open_tickets_at_cutoff") == 1L)
    assert(math.abs(c1.getAs[Double]("avg_resolution_hours_365d") - 24.0) < 1e-9)
    assert(math.abs(c1.getAs[Double]("avg_csat_365d") - 4.0) < 1e-9, "the 5 was given after T")
  }

  test("a group whose source is missing contributes no columns, so 'unavailable' is not 'zero'") {
    val bare = featuresAt(withReturns = false, withSessions = false, withTickets = false)
    val columns = bare.columns.toSet
    // Returns still exist through the cancellation lines P3 marked; sessions and tickets do not.
    assert(columns.contains("return_count_365d"))
    Seq("sessions_90d", "days_since_last_session", "tickets_365d", "open_tickets_at_cutoff",
        "cart_conversion_90d").foreach { c =>
      assert(!columns.contains(c), s"$c exists although its source table was not supplied")
    }
  }

  test("labels: a cancellation inside the horizon is not a completed order") {
    val lines = cleanLines()
    val daily = ChurnTransforms.customerDaily(lines, None)
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, Cutoff, Cutoff)
    val labels = ChurnTransforms
      .labels(ChurnTransforms.eligibleCustomers(expanded), ChurnTransforms.purchaseDays(daily), 90, DataEnd)
      .collect()
      .map(r => r.getAs[String]("customer_id") -> r.getAs[Int]("churned_90d"))
      .toMap
    assert(labels == Expected.labels, s"C2's only horizon activity is a cancellation: $labels")
  }
}
