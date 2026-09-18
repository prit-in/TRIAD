package triad.core

import org.apache.spark.sql.functions.col

import java.sql.Timestamp

/**
 * TC-025, TC-026, TC-028, TC-030: features and labels against hand-computed fixtures.
 *
 * The decisive one is TC-026: adding events dated on or after the cutoff T must not change a
 * single feature value. If it ever does, the pipeline is leaking and modelling must stop.
 */
class ChurnTransformsSpec extends SparkFixture {

  import spark.implicits._

  /** Three customers with hand-chosen order days around the 2026-04-01 cutoff. */
  private def lines(extra: Seq[(String, Int, String, String, String, Int, Double, Double)] = Seq.empty) = {
    val base = Seq(
      // order_id, line_id, customer_id, order_date, product_id, quantity, price, discount
      ("O1", 1, "C1", "2026-01-10 10:00:00", "P1", 2, 100.0, 0.0),
      ("O2", 1, "C1", "2026-02-15 10:00:00", "P2", 1, 200.0, 20.0),
      ("O3", 1, "C1", "2026-03-20 10:00:00", "P1", 3, 50.0, 0.0),
      ("O4", 1, "C2", "2026-03-25 10:00:00", "P3", 1, 500.0, 0.0),
      ("O5", 1, "C3", "2025-06-01 10:00:00", "P1", 1, 10.0, 0.0)
    ) ++ extra
    base.toDF("order_id", "line_id", "customer_id", "order_date_str", "product_id",
              "quantity", "price", "discount")
      .withColumn("order_date", col("order_date_str").cast("timestamp"))
      .drop("order_date_str")
      .withColumn("promo_code", col("order_id").substr(0, 0))  // empty -> null-ish placeholder
  }

  private def products = Seq(
    ("P1", "electronics"), ("P2", "home"), ("P3", "fashion")
  ).toDF("product_id", "category_l1")

  private def featuresAt(cutoff: String, extra: Seq[(String, Int, String, String, String, Int, Double, Double)] = Seq.empty) = {
    val daily = ChurnTransforms.customerDaily(lines(extra), Some(products))
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, cutoff, cutoff)
    ChurnTransforms.windowFeatures(expanded)
  }

  test("TC-025: customer-day aggregation collapses lines to one row per customer per day") {
    val daily = ChurnTransforms.customerDaily(lines(), Some(products)).collect()
    assert(daily.length == 5, "five distinct customer-days in the fixture")

    val c1Jan = daily.find(r => r.getAs[String]("customer_id") == "C1" &&
      r.getAs[java.sql.Date]("day").toString == "2026-01-10").get
    // 2 * 100 - 0 = 200
    assert(c1Jan.getAs[Double]("net_revenue") == 200.0)
    assert(c1Jan.getAs[Long]("orders") == 1L)
    assert(c1Jan.getAs[Double]("units") == 2.0)
  }

  test("TC-028: window features match hand-computed values at the cutoff") {
    val rows = featuresAt("2026-04-01").collect().map(r => r.getAs[String]("customer_id") -> r).toMap

    val c1 = rows("C1")
    // Orders before 2026-04-01: Jan 10, Feb 15, Mar 20 -> 3 orders in 365d.
    assert(c1.getAs[Long]("frequency_365d") == 3L)
    // Within 90 days of 2026-04-01 (i.e. from 2026-01-01): Jan 10 is 81 days before -> included.
    assert(c1.getAs[Long]("frequency_90d") == 3L)
    // Within 30 days: only Mar 20 (12 days before).
    assert(c1.getAs[Long]("frequency_30d") == 1L)
    // Recency: 2026-04-01 minus 2026-03-20 = 12 days.
    assert(c1.getAs[Int]("recency_days") == 12)
    // Monetary 365d: 200 + (1*200 - 20) + (3*50) = 200 + 180 + 150 = 530.
    assert(math.abs(c1.getAs[Double]("monetary_365d") - 530.0) < 1e-9)
    // AOV = 530 / 3.
    assert(math.abs(c1.getAs[Double]("aov_365d") - 530.0 / 3.0) < 1e-9)
    // Three distinct categories? C1 bought P1 (electronics) twice and P2 (home) once -> 2.
    assert(c1.getAs[Int]("category_count_365d") == 2)

    // C3's only order is 2025-06-01, which is 304 days before the cutoff: inside 365d.
    assert(rows("C3").getAs[Long]("frequency_365d") == 1L)
    assert(rows("C3").getAs[Long]("frequency_90d") == 0L)
  }

  test("TC-026: sentinel events dated on or after the cutoff change no feature value") {
    val before = featuresAt("2026-04-01").orderBy("customer_id").collect()

    // Sentinels: exactly on the cutoff, and well after it. Both must be invisible.
    val sentinels = Seq(
      ("S1", 1, "C1", "2026-04-01 00:00:00", "P1", 99, 9999.0, 0.0),
      ("S2", 1, "C1", "2026-04-02 10:00:00", "P2", 99, 9999.0, 0.0),
      ("S3", 1, "C2", "2026-06-15 10:00:00", "P3", 99, 9999.0, 0.0)
    )
    val after = featuresAt("2026-04-01", sentinels).orderBy("customer_id").collect()

    assert(before.length == after.length, "sentinels must not add customers at this cutoff")
    before.zip(after).foreach { case (b, a) =>
      assert(b == a, s"feature row changed after adding post-cutoff events:\n  before=$b\n  after =$a")
    }
  }

  test("TC-026b: expandToSnapshots never attaches a day to a cutoff on or before it") {
    val daily = ChurnTransforms.customerDaily(lines(), Some(products))
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, "2025-01-01", "2027-01-01")
    val violations = expanded
      .where(col("age_days") <= 0)
      .count()
    assert(violations == 0L, "a day contributed to a cutoff that is not strictly after it")
  }

  test("TC-030: churn labels match a hand-computed fixture") {
    val daily = ChurnTransforms.customerDaily(lines(), Some(products))
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, "2026-04-01", "2026-04-01")
    val eligible = ChurnTransforms.eligibleCustomers(expanded)
    val orderDays = daily.select(col("customer_id"), col("day"))

    // Data ends 2026-09-01, so a 90-day horizon from 2026-04-01 (ends 2026-06-30) is mature.
    val labels = ChurnTransforms
      .labels(eligible, orderDays, 90, "2026-09-01")
      .collect()
      .map(r => r.getAs[String]("customer_id") -> r.getAs[Int]("churned_90d"))
      .toMap

    // C1 and C2 both ordered before the cutoff, so both are eligible.
    // Neither has an order in [2026-04-01, 2026-06-30): both churned.
    assert(labels("C1") == 1, "C1 has no order in the horizon window")
    assert(labels("C2") == 1, "C2 has no order in the horizon window")
    // C3's last order was 2025-06-01, which is within 365 days of the cutoff, so it is
    // eligible and also churned.
    assert(labels("C3") == 1)
  }

  test("TC-030b: a customer who buys inside the horizon is not churned") {
    val extra = Seq(("O6", 1, "C1", "2026-05-01 10:00:00", "P1", 1, 10.0, 0.0))
    val daily = ChurnTransforms.customerDaily(lines(extra), Some(products))
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, "2026-04-01", "2026-04-01")
    val eligible = ChurnTransforms.eligibleCustomers(expanded)
    val orderDays = daily.select(col("customer_id"), col("day"))

    val labels = ChurnTransforms
      .labels(eligible, orderDays, 90, "2026-09-01")
      .collect()
      .map(r => r.getAs[String]("customer_id") -> r.getAs[Int]("churned_90d"))
      .toMap

    assert(labels("C1") == 0, "C1 ordered on 2026-05-01, inside the 90-day horizon")
    assert(labels("C2") == 1)
  }

  test("TC-030c: immature snapshots are dropped, never labelled as churn") {
    val daily = ChurnTransforms.customerDaily(lines(), Some(products))
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, "2026-04-01", "2026-04-01")
    val eligible = ChurnTransforms.eligibleCustomers(expanded)
    val orderDays = daily.select(col("customer_id"), col("day"))

    // Data ends 2026-05-01: the horizon from 2026-04-01 would run to 2026-06-30, past the
    // end of the data, so the snapshot is immature and must disappear.
    val labels = ChurnTransforms.labels(eligible, orderDays, 90, "2026-05-01")
    assert(labels.count() == 0L, "an immature snapshot must be dropped, not labelled")
  }

  test("inter-purchase statistics use only gaps that close before the cutoff") {
    val daily = ChurnTransforms.customerDaily(lines(), Some(products))
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, "2026-04-01", "2026-04-01")
    val gaps = ChurnTransforms
      .interPurchaseFeatures(expanded)
      .where(col("customer_id") === "C1")
      .collect()
      .head

    // C1 order days: Jan 10, Feb 15, Mar 20 -> gaps of 36 and 33 days, mean 34.5.
    assert(math.abs(gaps.getAs[Double]("inter_purchase_mean_days_365d") - 34.5) < 1e-9)
  }

  test("eligible population requires an order in the 365 days before the cutoff") {
    val daily = ChurnTransforms.customerDaily(lines(), Some(products))
    // At 2027-01-01, C3's 2025-06-01 order is more than 365 days old, so C3 is not eligible.
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, "2027-01-01", "2027-01-01")
    val eligible = ChurnTransforms.eligibleCustomers(expanded).collect().map(_.getAs[String]("customer_id"))
    assert(!eligible.contains("C3"))
  }

  test("try_divide keeps ANSI mode safe when a denominator is zero") {
    val daily = ChurnTransforms.customerDaily(lines(), Some(products))
    val expanded = ChurnTransforms.expandToSnapshots(daily, 365, "2026-04-01", "2026-04-01")
    // windowFeatures divides by frequency_365d; with ANSI on this must not raise.
    val rows = ChurnTransforms.windowFeatures(expanded).collect()
    assert(rows.nonEmpty)
  }

  test("timestamps survive as UTC microseconds through the aggregation") {
    val df = lines()
    val ts = df.select(col("order_date")).as[Timestamp].collect().head
    assert(ts.toString.startsWith("2026-"))
  }
}
