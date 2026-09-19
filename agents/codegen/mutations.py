"""Seeded code faults for EXP-8, applied between generation and validation.

PRD section 33.1 lists this as "a test-only mutation hook between generation and validation,
disabled in normal runs". It is disabled by being *unreferenced by the pipeline*: nothing in
`agents/graph/` imports this module, so there is no flag to leave switched on by accident and
no branch in the production path that a mutation could reach. The only callers are EXP-8 and
its tests.

The twenty faults are grouped into the four classes the PRD names, five each, because the
experiment's question is which class each gate catches - not how many mutations fail overall.
That grouping is what makes the result readable: a repair rate averaged over faults that
three different gates catch says nothing about any of them.

One property to keep in mind when reading EXP-8: a `wrong_column` fault compiles perfectly.
Column names are strings until the job runs, so no static gate can see anything wrong. That
is not a hole in the gates, it is what a static gate is; it is the reason gates 6, 7 and 9
exist, and the reason the PRD expects semantic faults to escalate more often.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Mutation:
    """One seeded fault: what it is, which class it belongs to, and how to apply it."""

    fault_id: str
    fault_class: str
    description: str
    find: str
    replace: str
    expected_gate: int
    note: str = ""

    def apply(self, slot_body: str) -> str:
        """Apply the fault, or raise if the anchor is not in the slot.

        Raising matters: a mutation that silently applied nothing would be recorded as an
        undetected fault, which would read as a hole in the gates when it is a broken
        experiment.
        """
        if self.find not in slot_body:
            raise ValueError(
                f"mutation {self.fault_id} found no anchor {self.find!r} in the slot; "
                "the template has changed and this fault needs updating"
            )
        return slot_body.replace(self.find, self.replace, 1)


# The anchors below are all inside CleanJob's `cleaning_plan` slot. Keeping every fault in one
# slot of one template means the twenty conditions differ only in the fault, which is what
# the experiment controls for.
_ANCHOR_DEDUP = 'val dedupKeys = cfg.paramStr("dedup_keys", "order_id,line_id")'
_ANCHOR_RULES = "val rules = CleaningRules.standardTransactionRules(normalised)"
_ANCHOR_NORMALISE = "val normalised = CleaningRules.normaliseText("
_ANCHOR_DOMAIN = 'if (raw.columns.contains("quantity"))'

MUTATIONS: tuple[Mutation, ...] = (
    # --- type mismatch: the compiler is the gate ------------------------------------------
    Mutation(
        "TM-1", "type_mismatch", "assign a Seq[String] to an Int",
        _ANCHOR_DEDUP, 'val dedupKeys: Int = cfg.paramStr("dedup_keys", "order_id,line_id")',
        expected_gate=4,
    ),
    Mutation(
        "TM-2", "type_mismatch", "add a String to a DataFrame",
        _ANCHOR_RULES, 'val rules = CleaningRules.standardTransactionRules(normalised) + 1',
        expected_gate=4,
    ),
    Mutation(
        "TM-3", "type_mismatch", "pass a String where a Seq[String] is required",
        'Seq("country", "payment_method", "channel", "order_status")',
        '"country"',
        expected_gate=4,
    ),
    Mutation(
        "TM-4", "type_mismatch", "call a Seq method on a DataFrame",
        _ANCHOR_RULES,
        "val rules = CleaningRules.standardTransactionRules(normalised).mkString(\",\")",
        expected_gate=4,
    ),
    Mutation(
        "TM-5", "type_mismatch", "return Unit where a DataFrame is expected",
        _ANCHOR_DOMAIN + " CleaningRules.cancellationsToReturns(raw) else raw",
        _ANCHOR_DOMAIN + " println(raw) else raw",
        expected_gate=4,
    ),

    # --- missing import: the compiler, or the declared-import check -----------------------
    Mutation(
        "MI-1", "missing_import", "use `col` without importing functions",
        _ANCHOR_RULES,
        'val rules = CleaningRules.standardTransactionRules(normalised.filter(col("x") > 0))',
        expected_gate=4,
    ),
    Mutation(
        "MI-2", "missing_import", "use `lit` without importing functions",
        _ANCHOR_RULES,
        "val rules = CleaningRules.standardTransactionRules("
        'normalised.withColumn("z", lit(1)))',
        expected_gate=4,
    ),
    Mutation(
        "MI-3", "missing_import", "reference an unimported type",
        _ANCHOR_DEDUP,
        'val schema: StructType = null; val dedupKeys = cfg.paramStr("dedup_keys", '
        '"order_id,line_id")',
        expected_gate=4,
    ),
    Mutation(
        "MI-4", "missing_import", "use a Java class that is not imported",
        _ANCHOR_DEDUP,
        'val when = Instant.now(); val dedupKeys = cfg.paramStr("dedup_keys", '
        '"order_id,line_id")',
        expected_gate=4,
    ),
    Mutation(
        "MI-5", "missing_import", "call an unimported helper object",
        _ANCHOR_RULES,
        "val rules = QuarantineHelper.rulesFor(normalised)",
        expected_gate=4,
    ),

    # --- wrong column: no static gate can see this ----------------------------------------
    # Column names are strings until the job runs. These compile, pass the static scan and the
    # dependency check, and are caught at execution or by the output contract. The experiment
    # records that rather than treating it as a miss.
    Mutation(
        "WC-1", "wrong_column", "de-duplicate on a column that does not exist",
        '"dedup_keys", "order_id,line_id"', '"dedup_keys", "ordr_id,line_id"',
        expected_gate=9, note="compiles; caught at execution or by the contract",
    ),
    Mutation(
        "WC-2", "wrong_column", "normalise a column that does not exist",
        '"country", "payment_method", "channel", "order_status"',
        '"countryy", "payment_method", "channel", "order_status"',
        expected_gate=9, note="compiles; the filter on `columns.contains` may absorb it",
    ),
    Mutation(
        "WC-3", "wrong_column", "test the wrong column for the cancellation rule",
        '"quantity"', '"qty"',
        expected_gate=9, note="compiles; changes behaviour silently",
    ),
    Mutation(
        "WC-4", "wrong_column", "order de-duplication by a column that does not exist",
        '"dedup_order_by", ""', '"dedup_order_by", "not_a_column"',
        expected_gate=9, note="compiles; the contains filter drops it",
    ),
    Mutation(
        "WC-5", "wrong_column", "filter on a misspelled column",
        _ANCHOR_RULES,
        'val rules = CleaningRules.standardTransactionRules(normalised.filter('
        'normalised("custmer_id").isNotNull))',
        expected_gate=9, note="compiles; fails when Spark resolves the name",
    ),

    # --- forbidden API: the static scan, before anything is compiled or run ---------------
    Mutation(
        "FA-1", "forbidden_api", "collect() the event data onto the driver",
        _ANCHOR_RULES,
        "val all = normalised.collect()\n    val rules = "
        "CleaningRules.standardTransactionRules(normalised)",
        expected_gate=2,
    ),
    Mutation(
        "FA-2", "forbidden_api", "write output from inside the slot",
        _ANCHOR_RULES,
        'normalised.write.parquet("/tmp/out")\n    val rules = '
        "CleaningRules.standardTransactionRules(normalised)",
        expected_gate=2,
    ),
    Mutation(
        "FA-3", "forbidden_api", "build a second SparkSession",
        _ANCHOR_RULES,
        "val other = SparkSession.builder.getOrCreate()\n    val rules = "
        "CleaningRules.standardTransactionRules(normalised)",
        expected_gate=2,
    ),
    Mutation(
        "FA-4", "forbidden_api", "kill the driver with System.exit",
        _ANCHOR_RULES,
        "if (inputRows == 0L) System.exit(1)\n    val rules = "
        "CleaningRules.standardTransactionRules(normalised)",
        expected_gate=2,
    ),
    Mutation(
        "FA-5", "forbidden_api", "reach the network from a sandbox job",
        _ANCHOR_RULES,
        'val u = new java.net.URL("http://example.invalid")\n    val rules = '
        "CleaningRules.standardTransactionRules(normalised)",
        expected_gate=2,
    ),
)

FAULT_CLASSES = ("type_mismatch", "missing_import", "wrong_column", "forbidden_api")


def by_class(fault_class: str) -> tuple[Mutation, ...]:
    return tuple(m for m in MUTATIONS if m.fault_class == fault_class)


def slot_body_of(template_source: str, slot_id: str) -> str:
    """The current text of one slot, so a mutation anchors against what is really there."""
    match = re.search(
        rf"// BEGIN GENERATED SLOT: {re.escape(slot_id)}\n(.*?)\n\s*// END GENERATED SLOT: "
        rf"{re.escape(slot_id)}",
        template_source,
        flags=re.S,
    )
    if match is None:
        raise ValueError(f"slot {slot_id!r} is not in this template")
    return match.group(1)


def check_anchors(template_source: str, slot_id: str = "cleaning_plan") -> list[str]:
    """Which mutations no longer match the template. Run by a test, so drift is loud.

    A fault whose anchor has gone stale applies nothing and is then recorded as undetected -
    it would read as a gap in the gates when the gates never saw a fault at all.
    """
    body = slot_body_of(template_source, slot_id)
    return [m.fault_id for m in MUTATIONS if m.find not in body]
