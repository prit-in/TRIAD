package triad.jobs

import org.apache.spark.sql.functions.col
import org.apache.spark.sql.{DataFrame, SparkSession}
import triad.core.CleaningRules.CleaningResult
import triad.core.{CleaningRules, ContractViolation, Inputs, JobConfig, OutputWriter, TriadJob}

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
    // The plan: domain normalisation, then quarantine rules, then de-duplication.
    val domainHandled =
      if (raw.columns.contains("quantity")) CleaningRules.cancellationsToReturns(raw) else raw
    val normalised = CleaningRules.normaliseText(
      domainHandled,
      Seq("country", "payment_method", "channel", "order_status")
    )
    val rules = CleaningRules.standardTransactionRules(normalised)
    val dedupKeys = cfg.paramStr("dedup_keys", "order_id,line_id")
      .split(",").map(_.trim).filter(_.nonEmpty).toSeq
      .filter(normalised.columns.contains)
    val dedupOrder = cfg.paramStr("dedup_order_by", "").split(",").map(_.trim)
      .filter(_.nonEmpty).toSeq.filter(normalised.columns.contains)
    // END GENERATED SLOT: cleaning_plan

    val (kept, quarantined, perRule) = CleaningRules.applyQuarantineRules(normalised, rules)
    val quarantinedRows = OutputWriter.writeQuarantine(quarantined, cfg)
    val (deduped, removedDuplicates) = CleaningRules.deduplicate(kept, dedupKeys, dedupOrder)
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
    CleanJob.lastResult = Some(result)
    deduped
  }

  /** Exposed so the fixture tests can assert on the counts without re-running the job. */
  @volatile var lastResult: Option[CleaningResult] = None
}
