package triad.jobs

import org.apache.spark.sql.{DataFrame, SparkSession}
import triad.core.CleaningRules.CleaningResult
import triad.core.{CleaningRules, Inputs, JobConfig, OutputWriter, TriadJob}

/**
 * Cleaning job template (stage P3).
 *
 * The quarantine write and the row-accounting check live outside the slot, so a generated
 * cleaning plan cannot delete rows or skip the accounting identity (FR-018, FR-023).
 */
object CleanJob extends TriadJob {

  override def name: String = "clean-and-conform"

  override def outputGrain: Seq[String] = Seq.empty // set per dataset via --param grain=

  override def transform(spark: SparkSession, in: Inputs, cfg: JobConfig): DataFrame = {
    val raw = in("source")
    val inputRows = raw.count()

    // BEGIN GENERATED SLOT: cleaning_plan
    // The plan: conform declared types, domain normalisation, then quarantine rules, then
    // de-duplication. A column the contract declares numeric that arrives as text is converted
    // before any rule compares it, and what does not convert is quarantined (FR-019).
    val (typed, castRules) = CleaningRules.conformNumeric(
      raw, Seq("price" -> "decimal(18,2)", "discount" -> "decimal(18,2)", "quantity" -> "int"))
    val domainHandled =
      if (typed.columns.contains("quantity")) CleaningRules.cancellationsToReturns(typed) else typed
    val normalised = CleaningRules.normaliseText(
      domainHandled,
      Seq("country", "payment_method", "channel", "order_status")
    )
    val rules = castRules ++ CleaningRules.standardTransactionRules(normalised)
    val dedupKeys = cfg.paramStr("dedup_keys", "order_id,line_id")
      .split(",").map(_.trim).filter(_.nonEmpty).toSeq
      .filter(normalised.columns.contains)
    val dedupOrder = cfg.paramStr("dedup_order_by", "").split(",").map(_.trim)
      .filter(_.nonEmpty).toSeq.filter(normalised.columns.contains)
    // END GENERATED SLOT: cleaning_plan

    val (kept, quarantined, perRule) = CleaningRules.applyQuarantineRules(normalised, rules)
    val quarantinedRows = OutputWriter.writeQuarantine(quarantined, cfg)
    val (dedupedWithRaw, removedDuplicates) = CleaningRules.deduplicate(kept, dedupKeys, dedupOrder)
    // The original text of a conformed column is evidence for the quarantine record, not data:
    // it stays with the quarantined rows and leaves the clean output.
    val deduped = dedupedWithRaw.drop(dedupedWithRaw.columns.filter(_.endsWith("__raw")): _*)
    val outputRows = deduped.count()

    val result = CleaningResult(
      clean = deduped,
      quarantine = quarantined,
      inputRows = inputRows,
      outputRows = outputRows,
      quarantinedRows = quarantinedRows,
      removedDuplicates = removedDuplicates,
      perRule = perRule
    )
    result.requireBalanced()   // FR-023: a mismatch fails the stage, never continues
    // `this`, never the object's own name: a generated copy is renamed, so a self-reference
    // by name would not compile (caught by gate 4 the first time it was written that way).
    this.lastResult = Some(result)
    deduped
  }

  /** Exposed so the fixture tests can assert on the counts without re-running the job. */
  @volatile var lastResult: Option[CleaningResult] = None

  override def jobMetrics: Map[String, String] =
    this.lastResult.map(_.metrics).getOrElse(Map.empty)
}
