"""EXP-8's seeded faults against the template they mutate.

`check_anchors` existed and nothing ran it, so when the CleanJob slot changed on 08-10-2026
twelve of the twenty anchors went stale unseen: EXP-8 would have crashed at its second fault,
and WC-3's anchor matched a type declaration instead of the cancellation test it was written
for. Every fault must find its anchor exactly once and change the slot.
"""

from __future__ import annotations

from agents.codegen.mutations import MUTATIONS, check_anchors, slot_body_of
from agents.codegen.templates import TEMPLATE_ROOT


def _source() -> str:
    return (TEMPLATE_ROOT / "CleanJob.scala").read_text(encoding="utf-8")


def test_every_fault_anchors_exactly_once_in_the_current_template() -> None:
    assert check_anchors(_source()) == []


def test_every_fault_changes_the_slot() -> None:
    body = slot_body_of(_source(), "cleaning_plan")
    unchanged = [m.fault_id for m in MUTATIONS if m.apply(body) == body]
    assert unchanged == []


def test_twenty_faults_five_per_class() -> None:
    from collections import Counter

    assert Counter(m.fault_class for m in MUTATIONS) == {
        "type_mismatch": 5, "missing_import": 5, "wrong_column": 5, "forbidden_api": 5,
    }
