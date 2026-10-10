"""Tables 10 and 11, generated from the stored experiment outputs - never typed.

    python docs/research/tables/build_tables.py

Writes `table10_results_summary.md` and `table11_comparative_performance.md` beside this file.
Every cell is read from `experiments/EXP-n/latest.json`; a measurement an experiment did not
record is written as "not measured", never filled in. The manuscript includes these files at
assembly, so a re-run of an experiment changes its table row and nothing else needs editing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
VERDICT = {True: "held", False: "did not hold", None: "no verdict"}


def load(exp: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    data = json.loads((ROOT / "experiments" / exp / "latest.json").read_text(encoding="utf-8"))
    trials = {t["condition"]: {m["name"]: m["value"] for m in t.get("measurements", [])}
              | {"_ok": t.get("ok")} for t in data.get("trials", [])}
    return data, trials


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        text = f"{value:,.{digits}f}"
        # Trailing zeros only after a decimal point: "540" must stay 540.
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


def secs(value: Any) -> str:
    return "not measured" if not isinstance(value, (int, float)) else f"{value:,.0f} s"


def share(trials: dict[str, dict[str, Any]], keep: Any, test: Any) -> str:
    chosen = [t for name, t in trials.items() if keep(name)]
    return f"{sum(1 for t in chosen if test(t))} of {len(chosen)}"


def key_result(exp: str, trials: dict[str, dict[str, Any]]) -> str:
    """The one or two measured facts each experiment exists to produce."""
    t = trials
    if exp == "EXP-1":
        p = t.get("P_triad", {})
        b2, b3 = t.get("B2_pandas_only", {}), t.get("B3_spark_no_agents", {})
        return (f"wall clock P {secs(p.get('wall_clock_s'))}, B3 {secs(b3.get('wall_clock_s'))}, "
                f"B2 {secs(b2.get('wall_clock_s'))}; test PR-AUC P {num(p.get('test_pr_auc'))}; "
                f"manual steps {num(p.get('human_actions_excluding_approval'))}")
    if exp == "EXP-2":
        rungs = sorted((v["customers"], v) for k, v in t.items() if k.startswith("customers_"))
        failed = next(((n, v) for n, v in rungs
                       if not v.get("b2_completed") and v.get("b3_completed")), None)
        if failed:
            return f"B2 failed and B3 completed at {failed[0]:,} customers"
        b3_first = next((n for n, v in rungs
                         if v.get("b2_completed") and v.get("b3_completed") is False), None)
        if b3_first:
            return f"B3 failed while B2 completed at {b3_first:,} customers"
        both_failed = next((n for n, v in rungs
                            if v.get("b2_completed") is False and v.get("b3_completed") is False),
                           None)
        if both_failed:
            return f"both failed at {both_failed:,} customers"
        both = [n for n, v in rungs if v.get("b2_completed") and v.get("b3_completed")]
        return f"no crossover; both completed up to {both[-1]:,} customers" if both else "no rung completed"
    if exp == "EXP-3":
        four = t.get("local_4_cores", {})
        return (f"speed-up 1 to 2 cores {num(t.get('local_2_cores', {}).get('speed_up_vs_1_core'))}, "
                f"2 to 4 cores {num(four.get('speed_up_2_to_4_cores'))}; task time "
                f"{secs(t.get('local_1_cores', {}).get('executor_run_time_s'))} on 1 core, "
                f"{secs(four.get('executor_run_time_s'))} on 4")
    if exp == "EXP-4":
        return "faults stopped or handled: " + share(t, lambda n: n != "baseline_clean",
                                                      lambda v: v.get("_ok"))
    if exp == "EXP-5":
        rates = [(n, v.get("test_pr_auc"), v.get("quarantined_rows")) for n, v in t.items()]
        return "; ".join(f"{n}: PR-AUC {num(pr)}, quarantined {num(q)}" for n, pr, q in rates)
    if exp == "EXP-6":
        # Only an arm whose fault landed counts (DEC-42): a kill read as a lost executor, or an
        # out-of-memory. The memory arm's spill is reported in Section 15.4, not here.
        def landed(n):
            v = t[n]
            return bool((v.get("container_killed") and v.get("first_error_class") == "EXECUTOR_LOST")
                        or "EXECUTOR_OOM" in (v.get("first_error_class"),
                                              v.get("feature_first_error_class")))
        return (f"faults that landed and recovered {share(t, landed, lambda v: v.get('recovered'))}"
                f"; identical to the clean run "
                f"{share(t, landed, lambda v: v.get('identical_to_clean_run'))}")
    if exp == "EXP-7":
        stable = t.get("stable_no_drift", {})
        caught = share(t, lambda n: n.startswith(("covariate", "concept")),
                       lambda v: v.get("retraining_should_start"))
        return (f"drift caught {caught}; false-alarm rate {num(stable.get('false_alarm_rate'))} "
                f"over {num(stable.get('months_replayed'))} stable months")
    if exp == "EXP-8":
        def seeded(n):
            return not n.startswith("control") and not t[n].get("toolchain_failure")
        return ("stopped before reading data: "
                + share(t, seeded, lambda v: v.get("detected_before_data", v.get("detected")))
                + "; by the static gates alone: " + share(t, seeded, lambda v: v.get("detected")))
    if exp == "EXP-9":
        return "planted leaks blocked: " + share(t, lambda n: n.startswith("leak"),
                                                 lambda v: v.get("blocked_by_any_defence"))
    if exp == "EXP-10":
        return "bypass attempts refused: " + share(t, lambda n: n != "audit_and_alerts",
                                                   lambda v: v.get("_ok"))
    return ""


def table10() -> str:
    rows = ["| Experiment | Hypothesis | Status | Verdict | Key measured result |",
            "| --- | --- | --- | --- | --- |"]
    for n in range(1, 11):
        exp = f"EXP-{n}"
        data, trials = load(exp)
        rows.append(f"| {exp} {data.get('title', '')} | {data.get('hypothesis', '')} | "
                    f"{data.get('status')} | {VERDICT[data.get('hypothesis_held')]} | "
                    f"{key_result(exp, trials)} |")
    return "**Table 10. Experimental Results Summary**\n\n" + "\n".join(rows) + "\n"


def table11() -> str:
    _, t = load("EXP-1")
    rows = ["| Measure | B2 pandas | B3 Spark | P TRIAD |", "| --- | --- | --- | --- |"]
    configs = ["B2_pandas_only", "B3_spark_no_agents", "P_triad"]
    measures = [("wall_clock_s", "Wall clock (s)", 1), ("test_pr_auc", "Test PR-AUC", 3),
                ("test_roc_auc", "Test ROC-AUC", 3), ("test_brier", "Test Brier score", 3),
                ("peak_rss_mb", "Peak memory (MiB)", 0), ("source_lines", "Hand-written lines", 0),
                ("human_actions_excluding_approval", "Manual steps", 0),
                ("llm_calls", "LLM calls", 0), ("decision_records", "Decision records", 0),
                ("audit_entries", "Audit entries", 0), ("spark_jobs", "Sandboxed Spark jobs", 0)]
    for key, label, digits in measures:
        cells = [num(t.get(c, {}).get(key), digits) for c in configs]
        rows.append(f"| {label} | " + " | ".join(cells) + " |")
    like = t.get("like_for_like_quality_on_one_feature_table", {})
    # The parenthesis after the caption is set as the table's note at assembly.
    note = (f"like-for-like, every shortlist refitted on P's feature table with the same split: "
            f"test PR-AUC {num(like.get('baseline_shortlist_test_pr_auc'))}")
    return (f"**Table 11. Comparative Performance Analysis** *({note})*\n\n"
            + "\n".join(rows) + "\n")


def main() -> None:
    for name, build in (("table10_results_summary.md", table10),
                        ("table11_comparative_performance.md", table11)):
        (HERE / name).write_text(build(), encoding="utf-8")
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
