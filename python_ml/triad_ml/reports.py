"""M14 Reporting: fact sheets, claim checking and rendering (PRD section 27, FR-071).

The rule the whole design turns on: **the narrative may not contain a digit that did not come
from a fact placeholder.** The agent writes `{{fact:high_risk_count}}`; the renderer fills it
from the fact sheet; the claim checker then re-reads the rendered text and fails the report if
any number appears that the fact sheet cannot account for.

Two failed checks fall back to a template-only narrative, which contains no free text at all.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from triad_core.ids import utcnow

PLACEHOLDER = re.compile(r"\{\{fact:([A-Za-z0-9_.]+)\}\}")
# Any digit run, including decimals, percentages and thousands separators.
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
# Whole dates, removed before tokenising so their parts are never flagged individually.
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}-\d{2}-\d{4}\b|\b\d{2}/\d{2}/\d{4}\b")
CAUSAL_WORDS = re.compile(
    r"\b(causes?|caused|causing|drives?|driving|drove|leads? to|led to|because of|"
    r"results? in|makes? customers?|due to)\b",
    re.I,
)

PROVENANCE = ("Calculated", "Model output", "AI interpretation", "Assumption")


class ClaimCheckFailed(Exception):
    pass


@dataclass
class Fact:
    id: str
    value: Any
    unit: str = ""
    definition: str = ""
    provenance: str = "Calculated"

    def rendered(self) -> str:
        v = self.value
        if isinstance(v, float):
            text = f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"
        elif isinstance(v, int):
            text = f"{v:,}"
        else:
            text = str(v)
        return f"{text}{self.unit}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "value": self.value,
            "unit": self.unit,
            "definition": self.definition,
            "provenance": self.provenance,
            "rendered": self.rendered(),
        }


@dataclass
class FactSheet:
    """Every number the report is allowed to mention (PRD 27.4 step 1)."""

    facts: dict[str, Fact] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: utcnow().isoformat())

    def add(
        self, fact_id: str, value: Any, *, unit: str = "", definition: str = "",
        provenance: str = "Calculated",
    ) -> Fact:
        fact = Fact(fact_id, value, unit, definition, provenance)
        self.facts[fact_id] = fact
        return fact

    def add_metrics(self, metrics: dict[str, Any], prefix: str = "") -> None:
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and value == value:  # skip NaN
                self.add(f"{prefix}{key}", value, definition=f"metric {key}")

    def get(self, fact_id: str) -> Fact:
        if fact_id not in self.facts:
            raise ClaimCheckFailed(f"unknown fact id: {fact_id}")
        return self.facts[fact_id]

    def allowed_numbers(self) -> set[str]:
        """Every textual form a fact may legitimately take in the rendered narrative."""
        allowed: set[str] = set()
        for fact in self.facts.values():
            allowed.add(fact.rendered())
            allowed.add(fact.rendered().rstrip("%"))
            v = fact.value
            if isinstance(v, (int, float)):
                allowed.add(f"{v:,}" if isinstance(v, int) else f"{v}")
                allowed.add(str(v))
                for places in (0, 1, 2, 3, 4):
                    allowed.add(f"{float(v):.{places}f}")
                    allowed.add(f"{float(v):,.{places}f}")
                    allowed.add(f"{float(v) * 100:.{places}f}")
                    allowed.add(f"{float(v) * 100:.{places}f}%")
        return {a for a in allowed if a}

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "facts": {k: v.to_dict() for k, v in self.facts.items()},
        }


@dataclass
class ClaimCheckResult:
    passed: bool
    unverified_numbers: list[str] = field(default_factory=list)
    causal_phrases: list[str] = field(default_factory=list)
    unknown_placeholders: list[str] = field(default_factory=list)
    facts_verified: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "unverified_numbers": self.unverified_numbers,
            "causal_phrases": self.causal_phrases,
            "unknown_placeholders": self.unknown_placeholders,
            "facts_verified": self.facts_verified,
        }


def fill_placeholders(narrative: str, sheet: FactSheet) -> tuple[str, list[str], list[str]]:
    """Replace `{{fact:id}}` with the stored value. Unknown ids are reported, not guessed."""
    used: list[str] = []
    unknown: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        fact_id = match.group(1)
        if fact_id not in sheet.facts:
            unknown.append(fact_id)
            return f"[UNKNOWN FACT {fact_id}]"
        used.append(fact_id)
        return sheet.facts[fact_id].rendered()

    return PLACEHOLDER.sub(_sub, narrative), used, unknown


def check_claims(
    rendered: str, sheet: FactSheet, *, used_ids: list[str] | None = None,
    unknown_ids: list[str] | None = None, allow_years: bool = True,
) -> ClaimCheckResult:
    """Fail the report if a number appears that the fact sheet cannot account for."""
    allowed = sheet.allowed_numbers()
    unverified: list[str] = []

    # Strip HTML tags and attributes first: chart coordinates are not narrative claims.
    text = re.sub(r"<[^>]+>", " ", rendered)
    # Then remove whole dates, before tokenising numbers. Checking a date per token would
    # split 2026-09-18 into "2026", "09" and "18" and flag the last two as unverified.
    text = DATE.sub(" ", text)

    for match in NUMBER.finditer(text):
        token = match.group(0)
        if token in allowed or token.replace(",", "") in allowed:
            continue
        if allow_years and re.fullmatch(r"(19|20)\d{2}", token):
            continue
        unverified.append(token)

    causal = [m.group(0) for m in CAUSAL_WORDS.finditer(text)]
    return ClaimCheckResult(
        passed=not unverified and not causal and not (unknown_ids or []),
        unverified_numbers=sorted(set(unverified)),
        causal_phrases=sorted({c.lower() for c in causal}),
        unknown_placeholders=list(unknown_ids or []),
        facts_verified=len(set(used_ids or [])),
    )


def template_narrative(sheet: FactSheet, section: str) -> str:
    """The deterministic fallback after two failed claim checks (PRD 27.4 step 7).

    It contains no free text: every sentence is a fact id and its stored value.
    """
    lines = [f"{section} (template-only narrative: the drafted version failed claim checking)."]
    for fact in sheet.facts.values():
        definition = f" - {fact.definition}" if fact.definition else ""
        lines.append(f"- {fact.id}: {{{{fact:{fact.id}}}}} ({fact.provenance}){definition}")
    return "\n".join(lines)


# --- rendering -----------------------------------------------------------------

HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>
 body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 2rem auto;
        max-width: 60rem; line-height: 1.55; color: #16181d; }}
 h1, h2 {{ line-height: 1.2; }}
 table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
 th, td {{ border: 1px solid #d6d9e0; padding: .45rem .6rem; text-align: left; }}
 th {{ background: #f4f5f8; }}
 .badge {{ display: inline-block; font-size: .72rem; padding: .1rem .45rem; border-radius: .6rem;
          border: 1px solid #b9c0cc; background: #f4f5f8; margin-left: .35rem; }}
 .badge.model {{ background: #eef4ff; border-color: #9bb8ef; }}
 .badge.ai {{ background: #fff6e8; border-color: #e5b96a; }}
 .badge.assumption {{ background: #f2f0ff; border-color: #b3a7e8; }}
 .meta {{ color: #55606e; font-size: .85rem; }}
 .fail {{ background: #fdecec; border: 1px solid #e0a0a0; padding: .6rem; }}
</style></head>
<body>
<h1>{title}</h1>
<p class="meta">Run {run_key} &middot; generated {generated_at} &middot;
   claim check: <strong>{claim_status}</strong> ({facts_verified} facts verified)</p>
{body}
<hr>
<p class="meta">Provenance labels: Calculated = computed from stored data;
 Model output = a prediction or explanation value; AI interpretation = narrative drafted by
 the Reporting Agent from the fact sheet; Assumption = a stated assumption.
 Explanations describe associations, not causes.</p>
</body></html>
"""


def provenance_badge(label: str) -> str:
    cls = {"Model output": "model", "AI interpretation": "ai", "Assumption": "assumption"}.get(
        label, ""
    )
    return f'<span class="badge {cls}">{html.escape(label)}</span>'


def render_html(
    *, title: str, run_key: str, sections: list[dict[str, Any]], sheet: FactSheet,
    claim: ClaimCheckResult,
) -> str:
    parts: list[str] = []
    for section in sections:
        parts.append(f"<h2>{html.escape(section['heading'])}</h2>")
        if section.get("provenance"):
            parts.append(f"<p>{provenance_badge(section['provenance'])}</p>")
        body = section.get("html") or html.escape(section.get("text", "")).replace("\n", "<br>")
        parts.append(f"<div>{body}</div>")
        if section.get("table"):
            parts.append(render_table(section["table"]))
    if not claim.passed:
        parts.append(
            '<div class="fail"><strong>Claim check failed.</strong> '
            f"Unverified numbers: {html.escape(str(claim.unverified_numbers))}; "
            f"causal wording: {html.escape(str(claim.causal_phrases))}.</div>"
        )
    return HTML_SHELL.format(
        title=html.escape(title),
        run_key=html.escape(run_key),
        generated_at=utcnow().strftime("%d-%m-%Y %H:%M UTC"),
        claim_status="passed" if claim.passed else "FAILED",
        facts_verified=claim.facts_verified,
        body="\n".join(parts),
    )


def render_table(table: dict[str, Any]) -> str:
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


@dataclass
class Report:
    title: str
    run_key: str
    report_type: str
    html: str
    claim: ClaimCheckResult
    fact_sheet: FactSheet
    fallback_used: bool = False

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.html, encoding="utf-8")
        return p


def build_report(
    *, title: str, run_key: str, report_type: str, sections: list[dict[str, Any]],
    sheet: FactSheet, max_attempts: int = 2,
) -> Report:
    """Draft, fill, check; on repeated failure fall back to the template-only narrative.

    `sections` carry narrative with `{{fact:id}}` placeholders. A section may also provide a
    `table`, which is rendered from stored values and never passes through the narrative.
    """
    attempt_sections = sections
    claim = ClaimCheckResult(passed=False)
    fallback = False

    for attempt in range(1, max_attempts + 1):
        filled: list[dict[str, Any]] = []
        used_all: list[str] = []
        unknown_all: list[str] = []
        for section in attempt_sections:
            text, used, unknown = fill_placeholders(section.get("text", ""), sheet)
            used_all += used
            unknown_all += unknown
            filled.append({**section, "text": text})

        rendered_text = "\n".join(s.get("text", "") for s in filled)
        claim = check_claims(rendered_text, sheet, used_ids=used_all, unknown_ids=unknown_all)
        if claim.passed:
            html_out = render_html(
                title=title, run_key=run_key, sections=filled, sheet=sheet, claim=claim
            )
            return Report(title, run_key, report_type, html_out, claim, sheet, fallback)

        if attempt < max_attempts:
            continue

        # Two failures: replace every narrative with the deterministic template.
        fallback = True
        attempt_sections = [
            {**s, "text": template_narrative(sheet, s["heading"]),
             "provenance": "Calculated"}
            for s in sections
        ]
        filled = []
        used_all, unknown_all = [], []
        for section in attempt_sections:
            text, used, unknown = fill_placeholders(section.get("text", ""), sheet)
            used_all += used
            unknown_all += unknown
            filled.append({**section, "text": text})
        rendered_text = "\n".join(s.get("text", "") for s in filled)
        claim = check_claims(rendered_text, sheet, used_ids=used_all, unknown_ids=unknown_all)
        html_out = render_html(
            title=title, run_key=run_key, sections=filled, sheet=sheet, claim=claim
        )
        return Report(title, run_key, report_type, html_out, claim, sheet, fallback)

    raise ClaimCheckFailed("report could not be produced")
