package triad.core

import org.apache.spark.sql.functions.col
import triad.core.CleaningRules.{CleaningResult, QuarantineRule}

/**
 * TC-016..TC-023: cleaning never loses a row silently.
 *
 * The identity `input = output + quarantined + removed_duplicates` is checked on every run
 * (FR-023); a mismatch fails the stage rather than continuing.
 */
class CleaningRulesSpec extends SparkFixture {

  import spark.implicits._

  private def fixture = Seq(
    ("O1", 1, "C1", 2, 100.0),
    ("O2", 1, null, 1, 50.0),      // null customer -> quarantined
    ("O3", 1, "C2", 1, -10.0),     // negative price -> quarantined
    ("O4", 1, "C3", 0, 20.0),      // zero quantity -> quarantined
    ("O1", 1, "C1", 2, 100.0),     // exact duplicate of O1
    ("O5", 1, "C4", 3, 30.0)
  ).toDF("order_id", "line_id", "customer_id", "quantity", "price")

  test("TC-018: failing rows are quarantined with a reason code, never dropped") {
    val rules = CleaningRules.standardTransactionRules(fixture)
    val (clean, quarantine, counts) = CleaningRules.applyQuarantineRules(fixture, rules)

    assert(quarantine.count() == 3L)
    val reasons = quarantine.select(col(CleaningRules.ReasonColumn)).as[String].collect().toSet
    assert(reasons == Set("NO_CUSTOMER_KEY", "NON_POSITIVE_PRICE", "ZERO_QUANTITY"))
    assert(counts("null_customer") == 1L)
    assert(counts("non_positive_price") == 1L)
    assert(counts("zero_quantity") == 1L)
    assert(clean.count() == 3L)
  }

  test("TC-017: de-duplication is deterministic and counted") {
    val rules = CleaningRules.standardTransactionRules(fixture)
    val (clean, _, _) = CleaningRules.applyQuarantineRules(fixture, rules)
    val (deduped, removed) = CleaningRules.deduplicate(clean, Seq("order_id", "line_id"), Seq("order_id"))

    assert(removed == 1L, "the duplicated O1 line must be counted, not silently dropped")
    assert(deduped.count() == 2L)

    // Re-running produces identical output (FR-017).
    val (again, removedAgain) = CleaningRules.deduplicate(clean, Seq("order_id", "line_id"), Seq("order_id"))
    assert(removedAgain == removed)
    assert(again.collect().toSet == deduped.collect().toSet)
  }

  test("TC-023: row accounting balances, and a mismatch raises") {
    val rules = CleaningRules.standardTransactionRules(fixture)
    val input = fixture.count()
    val (clean, quarantine, perRule) = CleaningRules.applyQuarantineRules(fixture, rules)
    val (deduped, removed) = CleaningRules.deduplicate(clean, Seq("order_id", "line_id"), Seq("order_id"))

    val result = CleaningResult(deduped, quarantine, input, deduped.count(), quarantine.count(), removed, perRule)
    assert(result.balanced, s"accounting: $input != ${result.outputRows} + ${result.quarantinedRows} + ${result.removedDuplicates}")
    result.requireBalanced()
    assert(result.metrics("row_accounting_balanced") == "true")

    val broken = result.copy(outputRows = result.outputRows - 1)
    assert(!broken.balanced)
    assertThrows[ContractViolation](broken.requireBalanced())
  }

  test("TC-019: try_cast quarantines unparseable values instead of failing under ANSI mode") {
    val raw = Seq(("1", "12"), ("2", "not-a-number"), ("3", "7")).toDF("id", "amount_raw")
    val (casted, rule) = CleaningRules.safeCast(raw, "amount_raw", "INT")
    val (clean, quarantine, counts) = CleaningRules.applyQuarantineRules(casted, Seq(rule))

    assert(quarantine.count() == 1L)
    assert(counts("cast_amount_raw") == 1L)
    val promoted = CleaningRules.promoteCast(clean, "amount_raw")
    assert(promoted.select("amount_raw").as[Int].collect().toSet == Set(12, 7))
  }

  test("domain rule: a cancellation line becomes a return, not an error") {
    val lines = Seq(("O1", 2, 10.0), ("CO2", -3, 20.0)).toDF("order_id", "quantity", "price")
    val handled = CleaningRules.cancellationsToReturns(lines)
    val rows = handled.collect().map(r => r.getAs[String]("order_id") -> r).toMap

    assert(rows("CO2").getAs[Boolean]("is_return"))
    assert(rows("CO2").getAs[Int]("quantity") == 3, "quantity is made positive on the return")
    assert(!rows("O1").getAs[Boolean]("is_return"))
  }

  test("TC-020: text normalisation trims and collapses whitespace") {
    val df = Seq("  united   kingdom ", "india").toDF("country")
    val out = CleaningRules.normaliseText(df, Seq("country")).as[String].collect().toSet
    assert(out == Set("united kingdom", "india"))
  }

  test("TC-016: median imputation adds an indicator column") {
    val df = Seq(Some(10.0), None, Some(20.0), Some(30.0)).toDF("v")
    val out = CleaningRules.imputeMedian(df, "v")
    assert(out.columns.contains("v_was_null"))
    assert(out.where(col("v").isNull).count() == 0L)
    assert(out.where(col("v_was_null") === 1).count() == 1L)
  }

  test("no quarantine path configured means a rejecting job fails instead of dropping rows") {
    val cfg = JobConfig.parse(Array("--output=file:///tmp/out"))
    val rejects = Seq("x").toDF("v")
    assertThrows[ContractViolation](OutputWriter.writeQuarantine(rejects, cfg))
  }
}
