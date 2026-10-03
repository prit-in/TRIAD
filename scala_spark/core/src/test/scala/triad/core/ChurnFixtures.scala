package triad.core

import org.apache.spark.sql.DataFrame
import org.apache.spark.sql.functions.col

/** One clean transaction line, as P3 leaves it: a cancellation has `is_return` and quantity > 0. */
final case class FixtureLine(
    order_id: String,
    line_id: Int,
    customer_id: String,
    order_date: String,
    product_id: String,
    quantity: Int,
    price: Double,
    discount: Double,
    promo_code: Option[String],
    payment_method: String,
    is_return: Boolean
)

final case class FixtureReturn(customer_id: String, order_id: String, return_date: String, quantity: Int)

final case class FixtureSession(
    customer_id: String,
    session_start: String,
    session_end: String,
    channel: String,
    product_views: Int,
    add_to_cart_count: Int
)

final case class FixtureTicket(
    customer_id: String,
    created_at: String,
    resolved_at: Option[String],
    category: String,
    csat_score: Option[Double]
)

/**
 * A hand-checked fixture for the whole feature catalogue at the cutoff T = 2026-04-01.
 *
 * Every expected value in `Expected` was worked out by hand and cross-checked by a separate
 * date-arithmetic calculation outside Spark; none was produced by the code under test.
 *
 * Each group has at least one event that must NOT count, placed where a plausible bug would
 * count it:
 *  - C2 cancels an order on 2026-03-28, after their last real order (03-25): counting it as a
 *    purchase would move recency from 7 to 4 and double their revenue;
 *  - C1 returns a unit on 2026-04-05, of an order placed before T: dating returns by the order
 *    rather than the return would count it;
 *  - C1's session starting 2026-03-31 23:50 ends after midnight, on T: its start is known at T
 *    and its length is not;
 *  - C1's ticket created 2026-03-20 is resolved on 2026-04-03: open at T, and neither its
 *    resolution time nor its satisfaction score may count;
 *  - C1's first order, 2024-02-10, is older than the 365-day window: it must reach the lifetime
 *    features and tenure, and nothing windowed.
 *
 * Shared by `ChurnTransformsSpec` (the building blocks) and `GeneratedJobsFixtureSpec` in the
 * jobs module (gate 5, the generated composition), so both are held to the same numbers.
 */
trait ChurnFixtures { self: SparkFixture =>

  import spark.implicits._

  val Cutoff = "2026-04-01"
  val DataEnd = "2026-09-01"

  /** Events dated on or after T, held separately so the sentinel test can add or remove them. */
  val postCutoffLines: Seq[FixtureLine] = Seq(
    FixtureLine("O9", 1, "C1", "2026-04-01 00:00:00", "P2", 9, 999.0, 0.0, None, "wallet", false),
    // A cancellation inside C2's label horizon: not a completed order, so C2 stays churned.
    FixtureLine("CO8", 1, "C2", "2026-05-10 10:00:00", "P3", 1, 500.0, 0.0, None, "card", true)
  )

  def cleanLines(withPostCutoff: Boolean = true): DataFrame = {
    val base = Seq(
      FixtureLine("O0", 1, "C1", "2024-02-10 10:00:00", "P1", 1, 100.0, 0.0, None, "card", false),
      FixtureLine("O1", 1, "C1", "2026-01-10 10:00:00", "P1", 2, 100.0, 0.0, Some("SPRING"), "card", false),
      FixtureLine("O2", 1, "C1", "2026-02-15 10:00:00", "P2", 1, 200.0, 20.0, None, "card", false),
      FixtureLine("O3", 1, "C1", "2026-03-20 10:00:00", "P1", 3, 50.0, 0.0, None, "wallet", false),
      FixtureLine("O4", 1, "C2", "2026-03-25 10:00:00", "P3", 1, 500.0, 0.0, None, "upi", false),
      FixtureLine("CO9", 1, "C2", "2026-03-28 10:00:00", "P3", 2, 500.0, 0.0, None, "card", true),
      FixtureLine("O5", 1, "C3", "2025-06-01 10:00:00", "P1", 1, 10.0, 0.0, None, "card", false)
    ) ++ (if (withPostCutoff) postCutoffLines else Seq.empty)
    base.toDF().withColumn("order_date", col("order_date").cast("timestamp"))
  }

  def products: DataFrame =
    Seq(("P1", "electronics"), ("P2", "home"), ("P3", "fashion")).toDF("product_id", "category_l1")

  def returnsTable(withPostCutoff: Boolean = true): DataFrame = {
    val rows = Seq(FixtureReturn("C1", "O1", "2026-02-01", 1)) ++
      (if (withPostCutoff) Seq(FixtureReturn("C1", "O3", "2026-04-05", 1)) else Seq.empty)
    rows.toDF().withColumn("return_date", col("return_date").cast("timestamp"))
  }

  def sessions(withPostCutoff: Boolean = true): DataFrame = {
    val rows = Seq(
      FixtureSession("C1", "2026-03-30 10:00:00", "2026-03-30 10:30:00", "app", 4, 1),
      FixtureSession("C1", "2026-03-31 23:50:00", "2026-04-01 00:20:00", "web", 2, 0)
    ) ++ (if (withPostCutoff)
            Seq(FixtureSession("C1", "2026-04-02 10:00:00", "2026-04-02 10:10:00", "web", 9, 9))
          else Seq.empty)
    rows.toDF()
      .withColumn("session_start", col("session_start").cast("timestamp"))
      .withColumn("session_end", col("session_end").cast("timestamp"))
  }

  def tickets(withPostCutoff: Boolean = true): DataFrame = {
    val rows = Seq(
      FixtureTicket("C1", "2026-03-01 10:00:00", Some("2026-03-02 10:00:00"), "delivery", Some(4.0)),
      FixtureTicket("C1", "2026-03-20 10:00:00", Some("2026-04-03 10:00:00"), "billing", Some(5.0))
    ) ++ (if (withPostCutoff)
            Seq(FixtureTicket("C1", "2026-04-02 10:00:00", None, "refund", None))
          else Seq.empty)
    rows.toDF()
      .withColumn("created_at", col("created_at").cast("timestamp"))
      .withColumn("resolved_at", col("resolved_at").cast("timestamp"))
  }

  /** The values the fixture is held to, per customer. `None` means the value must be null. */
  object Expected {
    val c1: Map[String, Any] = Map(
      "recency_days" -> 12,
      "tenure_days" -> 781,                 // first order 2024-02-10, outside the window
      "order_count_lifetime" -> 4L,         // O0, O1, O2, O3
      "clv_proxy_lifetime" -> 630.0,        // 100 + 200 + 180 + 150
      "frequency_30d" -> 1L,
      "frequency_90d" -> 3L,
      "frequency_365d" -> 3L,
      "monetary_365d" -> 530.0,             // O0 is outside the window
      "units_365d" -> 6.0,
      "items_per_order_365d" -> 2.0,
      "discount_share_365d" -> 1.0 / 3.0,   // O2 of three lines
      "promo_order_share_365d" -> 1.0 / 3.0,// O1 of three orders
      "category_count_365d" -> 2,           // electronics, home
      "inter_purchase_mean_days_365d" -> 34.5,
      "preferred_payment_method" -> "card", // two card lines to one wallet
      "return_count_365d" -> 1L,            // 2026-02-01 only; 2026-04-05 is after T
      "return_rate_365d" -> 1.0 / 6.0,
      "sessions_30d" -> 2L,
      "sessions_90d" -> 2L,
      "product_views_90d" -> 6L,
      "add_to_cart_90d" -> 1L,
      "app_session_share_90d" -> 0.5,
      "avg_session_minutes_90d" -> 30.0,    // only the session that ended before T
      "days_since_last_session" -> 1,
      "cart_conversion_90d" -> 3.0,
      "tickets_90d" -> 2L,
      "tickets_365d" -> 2L,
      "complaint_tickets_90d" -> 1L,        // delivery; billing is not a complaint
      "open_tickets_at_cutoff" -> 1L,       // resolved 2026-04-03, after T
      "avg_resolution_hours_365d" -> 24.0,  // the ticket resolved before T only
      "avg_csat_365d" -> 4.0
    )

    val c2: Map[String, Any] = Map(
      "recency_days" -> 7,                  // 4 if the 03-28 cancellation counted
      "frequency_365d" -> 1L,
      "monetary_365d" -> 500.0,             // 1500 if it counted
      "order_count_lifetime" -> 1L,
      "tenure_days" -> 7,
      "preferred_payment_method" -> "upi",  // the cancelled line was the card one
      "return_count_365d" -> 1L,            // the cancellation, counted as a return
      "return_rate_365d" -> 2.0,            // two units returned, one bought
      "sessions_90d" -> 0L,                 // a source exists, so zero rather than null
      "tickets_365d" -> 0L,
      "open_tickets_at_cutoff" -> 0L,
      "days_since_last_session" -> None,
      "avg_session_minutes_90d" -> None,
      "cart_conversion_90d" -> None         // try_divide by zero add-to-cart events
    )

    val c3: Map[String, Any] = Map(
      "recency_days" -> 304,
      "tenure_days" -> 304,
      "frequency_365d" -> 1L,
      "frequency_90d" -> 0L,
      "monetary_365d" -> 10.0,
      "return_count_365d" -> 0L,
      "return_rate_365d" -> 0.0
    )

    /**
     * Labels at T, horizon [2026-04-01, 2026-06-30). C1 completes O9 on T itself, so C1 is
     * retained. C2's only horizon activity is the CO8 cancellation, which is not a completed
     * order, so C2 is churned - the case that was labelled retained before returns were
     * excluded. C3 does nothing and is churned.
     */
    val labels: Map[String, Int] = Map("C1" -> 0, "C2" -> 1, "C3" -> 1)
  }

  /** Compare one feature row against an expectation map, with a tolerance for doubles. */
  def assertFeatures(row: org.apache.spark.sql.Row, expected: Map[String, Any], who: String): Unit =
    expected.foreach { case (name, want) =>
      val idx = row.fieldIndex(name)
      (want, if (row.isNullAt(idx)) None else Some(row.get(idx))) match {
        case (None, got) =>
          assert(got.isEmpty, s"$who.$name should be null, got $got")
        case (w: Double, Some(g: Number)) =>
          assert(math.abs(g.doubleValue - w) < 1e-9, s"$who.$name: expected $w, got $g")
        case (w: Number, Some(g: Number)) =>
          assert(g.longValue == w.longValue, s"$who.$name: expected $w, got $g")
        case (w, Some(g)) =>
          assert(g == w, s"$who.$name: expected $w, got $g")
        case (w, None) =>
          assert(false, s"$who.$name: expected $w, got null")
      }
    }
}
