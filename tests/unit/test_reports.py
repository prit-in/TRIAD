"""TC-071 / AT-15 / ATC-16: no unverified number reaches a published report."""

from __future__ import annotations

from python_ml.triad_ml.reports import (
    FactSheet,
    build_report,
    check_claims,
    fill_placeholders,
    template_narrative,
)


def _sheet() -> FactSheet:
    sheet = FactSheet()
    sheet.add("customers_scored", 1_900_000, definition="rows in the official prediction set")
    sheet.add("high_risk_count", 285_000, definition="customers in the high-risk band")
    sheet.add("high_risk_share", 0.15, definition="share of scores in the high-risk band",
              provenance="Model output")
    sheet.add("test_pr_auc", 0.66, definition="test PR-AUC of the selected model",
              provenance="Model output")
    return sheet


def test_placeholders_are_filled_from_the_fact_sheet() -> None:
    sheet = _sheet()
    text, used, unknown = fill_placeholders(
        "We scored {{fact:customers_scored}} customers.", sheet
    )
    assert "1,900,000" in text
    assert used == ["customers_scored"]
    assert unknown == []


def test_an_unknown_placeholder_is_reported_not_guessed() -> None:
    sheet = _sheet()
    text, _, unknown = fill_placeholders("Revenue was {{fact:invented_number}}.", sheet)
    assert unknown == ["invented_number"]
    assert "UNKNOWN FACT" in text


def test_a_number_that_is_not_in_the_fact_sheet_fails_the_check() -> None:
    """AT-15: a planted wrong number blocks publication."""
    sheet = _sheet()
    result = check_claims("We scored 1,900,000 customers and saved 42,000 of them.", sheet)
    assert result.passed is False
    assert "42,000" in result.unverified_numbers


def test_numbers_that_come_from_facts_pass() -> None:
    sheet = _sheet()
    text, used, unknown = fill_placeholders(
        "{{fact:customers_scored}} scored; {{fact:high_risk_count}} are high risk "
        "({{fact:high_risk_share}} of the base). Test PR-AUC {{fact:test_pr_auc}}.",
        sheet,
    )
    result = check_claims(text, sheet, used_ids=used, unknown_ids=unknown)
    assert result.passed, result.to_dict()
    assert result.facts_verified == 4


def test_causal_wording_is_flagged() -> None:
    """Explanations are associations; causal verbs must not appear next to them."""
    sheet = _sheet()
    result = check_claims("Fewer app sessions causes churn.", sheet)
    assert result.passed is False
    assert "causes" in result.causal_phrases

    for phrase in ("drives", "leads to", "results in", "due to"):
        assert check_claims(f"Late delivery {phrase} churn.", sheet).causal_phrases


def test_associative_wording_passes() -> None:
    sheet = _sheet()
    result = check_claims(
        "High scores are most associated with being overdue against the usual purchase gap.",
        sheet,
    )
    assert result.passed, result.to_dict()


def test_dates_and_years_are_not_treated_as_claims() -> None:
    sheet = _sheet()
    assert check_claims("Scored on 2026-09-18, covering 2025 and 2026.", sheet).passed


def test_two_failed_checks_fall_back_to_a_template_narrative() -> None:
    """ATC-16 / PRD 27.4 step 7."""
    sheet = _sheet()
    bad_sections = [
        {
            "heading": "Churn risk this month",
            "text": "We scored 999,999 customers and 12,345 are at risk because of delays.",
            "provenance": "AI interpretation",
        }
    ]
    report = build_report(
        title="Business report", run_key="run-x", report_type="business",
        sections=bad_sections, sheet=sheet,
    )
    assert report.fallback_used is True
    assert report.claim.passed is True, "the fallback must itself pass the claim check"
    assert "999,999" not in report.html
    assert "1,900,000" in report.html, "the template narrative uses stored facts"


def test_a_clean_narrative_publishes_without_the_fallback() -> None:
    sheet = _sheet()
    sections = [
        {
            "heading": "Churn risk this month",
            "text": "{{fact:customers_scored}} customers were scored. "
                    "{{fact:high_risk_count}} are in the high-risk band.",
            "provenance": "AI interpretation",
        },
        {
            "heading": "Model confidence",
            "text": "Test PR-AUC is {{fact:test_pr_auc}}.",
            "provenance": "Model output",
            "table": {"headers": ["Metric", "Value"], "rows": [["PR-AUC", "0.66"]]},
        },
    ]
    report = build_report(
        title="Business report", run_key="run-y", report_type="business",
        sections=sections, sheet=sheet,
    )
    assert report.fallback_used is False
    assert report.claim.passed is True
    assert "claim check: <strong>passed</strong>" in report.html
    assert "Model output" in report.html and "AI interpretation" in report.html


def test_the_report_states_that_explanations_are_associations() -> None:
    sheet = _sheet()
    report = build_report(
        title="t", run_key="r", report_type="business",
        sections=[{"heading": "h", "text": "{{fact:test_pr_auc}}"}], sheet=sheet,
    )
    assert "associations, not causes" in report.html


def test_template_narrative_contains_only_fact_placeholders() -> None:
    sheet = _sheet()
    text = template_narrative(sheet, "Summary")
    import re

    # Every digit run in the template must belong to a placeholder, not free text.
    without_placeholders = re.sub(r"\{\{fact:[A-Za-z0-9_.]+\}\}", "", text)
    assert not re.search(r"\d", without_placeholders)


# --- the model card (PRD 17.10) ------------------------------------------------


def _card():
    from python_ml.triad_ml.registry import ModelCard

    return ModelCard(
        model_name="churn_classifier",
        version="14",
        algorithm="random_forest",
        intended_use="Ranks customers by churn probability so a retention list can be ordered.",
        limits=["Not for pricing, credit, employment, insurance or housing decisions."],
        churn_definition="no completed order in [T, T+90)",
        horizon_days=90,
        training_cutoffs=["2025-01-01", "2025-02-01"],
        feature_set_version="fsv=1",
        candidate_comparison=[
            {"family": "random_forest", "valid_pr_auc": 0.717, "outcome": "selected"},
            {"family": "logreg", "valid_pr_auc": 0.690, "outcome": "screened out"},
        ],
        metrics={"pr_auc": 0.7801, "roc_auc": 0.8227},
        calibration={"method": "isotonic", "ece": 0.0511, "threshold": 0.42},
        segment_results=[{"segment": "country=DE", "values": {"recall": 0.51}}],
        top_drivers=[{"feature": "recency_days", "value": 0.31}],
        driver_method="permutation importance",
        known_limitations=["Test split is one cutoff; one cutoff is one season."],
    )


def test_a_model_card_carries_every_section_an_approver_is_promised() -> None:
    """PRD 17.10 names the contents, and the governance policy names the card as evidence."""
    markdown = _card().to_markdown()

    for heading in (
        "# Model card: churn_classifier version 14",
        "## Intended use and limits",
        "## Churn definition",
        "## Candidate comparison",
        "## Metrics",
        "## Calibration",
        "## Segment results",
        "## Known limitations",
    ):
        assert heading in markdown, heading

    # Both screened families, not only the winner: a comparison with one row is not one.
    assert "random_forest" in markdown and "logreg" in markdown
    # The method is named beside the drivers, because permutation importance and SHAP are not
    # the same claim.
    assert "permutation importance" in markdown
    assert "associations, not causes" in markdown


def test_the_pipeline_actually_writes_the_card_it_points_an_approver_at() -> None:
    """`ModelCard` and `write_model_card` existed with no caller at all.

    Meanwhile `model_versions.model_card_uri` was set to `reports/<run_key>/model_card.md` and
    the governance policy lists `model_card` among the evidence required to promote a model, so
    an approver following that link got a path to a file nothing had ever written. This asserts
    the wiring rather than the rendering: the rendering was never the part that was missing.
    """
    from pathlib import Path as _Path

    source = _Path("agents/graph/churn.py").read_text(encoding="utf-8")
    assert "write_model_card(" in source
    assert "model_card.md" in source


def test_a_written_card_is_readable_markdown(tmp_path) -> None:
    from python_ml.triad_ml.registry import write_model_card

    path = write_model_card(_card(), tmp_path / "model_card.md")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Model card:")
    assert "0.7801" in text
