"""Figures 21-25 and 28: charts drawn from stored experiment and run outputs, at 600 DPI.

    python docs/research/figures/build_charts.py

Every plotted number is read from a stored artifact - `experiments/EXP-n/latest.json`,
`docs/research/analysis/trust_scores.json` or `business_snapshot.json` - and nothing is typed
in. The faculty's figure rules apply (`docs/faculty/FACULTY_MATERIALS_ANALYSIS.md`): no title or
caption inside the image (the manuscript sets the caption below it), axis labels in words with
units in parentheses, and multipart panels labelled (a), (b), (c) centred below each panel in
8 pt Times New Roman. `charts_provenance.json` records the file and the run of every experiment
each figure drew on, so a figure can be traced to the measurement behind it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS = ROOT / "experiments"
ANALYSIS = ROOT / "docs" / "research" / "analysis"
OUT = Path(__file__).resolve().parent / "png"
DPI = 600
WIDE, NARROW = 7.16, 3.5          # IEEE double- and single-column widths (inches)

# Okabe-Ito: distinguishable in colour-blind vision and still separable in grey.
COLOURS = {"B2": "#E69F00", "B3": "#56B4E9", "P": "#009E73", "ink": "#222222",
           "warn": "#D55E00", "muted": "#999999", "blue": "#0072B2", "pink": "#CC79A7"}
HATCH = {"B2": "//", "B3": "..", "P": ""}
CONFIGS = [("B2_pandas_only", "B2", "B2 pandas"), ("B3_spark_no_agents", "B3", "B3 Spark"),
           ("P_triad", "P", "P TRIAD")]

provenance: dict[str, Any] = {}


def style() -> None:
    for name in ("times.ttf", "timesbd.ttf", "timesi.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({
        "font.family": "Times New Roman", "font.size": 8, "axes.titlesize": 8,
        "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "lines.linewidth": 1.2, "savefig.dpi": DPI, "figure.dpi": 100,
        "axes.spines.top": False, "axes.spines.right": False, "hatch.linewidth": 0.5,
    })


def load(exp: str, figure: str) -> dict[str, Any]:
    """An experiment's latest output as {condition: {measurement: value}}, noted as a source."""
    path = EXPERIMENTS / exp / "latest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    provenance.setdefault(figure, []).append({
        "file": str(path.relative_to(ROOT)).replace("\\", "/"), "experiment": exp,
        "started_at": data.get("started_at"), "status": data.get("status"),
        "hypothesis_held": data.get("hypothesis_held")})
    trials = {}
    for trial in data.get("trials", []):
        values = {m["name"]: m["value"] for m in trial.get("measurements", [])}
        values["_ok"] = trial.get("ok")
        trials[trial["condition"]] = values
    return {"meta": data, "trials": trials}


def analysis(name: str, figure: str) -> dict[str, Any]:
    path = ANALYSIS / name
    provenance.setdefault(figure, []).append(
        {"file": str(path.relative_to(ROOT)).replace("\\", "/")})
    return json.loads(path.read_text(encoding="utf-8"))


def label(ax: Any, letter: str, offset: float = -30) -> None:
    """(a), (b) ... centred below the panel, as the template's multipart-figure rule asks."""
    ax.annotate(f"({letter})", xy=(0.5, 0), xycoords="axes fraction", xytext=(0, offset),
                textcoords="offset points", ha="center", va="top", fontsize=8)


def bar_values(ax: Any, bars: Any, fmt: str = "{:.2f}", pad: float = 1.5) -> None:
    for bar in bars:
        height = bar.get_height()
        ax.annotate(fmt.format(height), xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, pad), textcoords="offset points", ha="center", va="bottom",
                    fontsize=6.5)


def save(fig: Any, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0.03, facecolor="white")
    plt.close(fig)
    print(f"  {path.relative_to(ROOT)}")


# --- Fig. 21: performance benchmark (EXP-1) ------------------------------------------------


def fig21() -> None:
    t = load("EXP-1", "Fig 21")["trials"]
    present = [(c, key, name) for c, key, name in CONFIGS if c in t]
    fig, axes = plt.subplots(1, 2, figsize=(WIDE, 2.4), gridspec_kw={"wspace": 0.32})

    ax = axes[0]
    walls = [t[c].get("wall_clock_s") or 0 for c, _, _ in present]
    bars = ax.bar([n for *_, n in present], walls, width=0.55,
                  color=[COLOURS[k] for _, k, _ in present],
                  hatch=[HATCH[k] for _, k, _ in present], edgecolor=COLOURS["ink"], linewidth=0.5)
    bar_values(ax, bars, "{:.0f}")
    ax.set_ylabel("End-to-end wall clock (s)")
    label(ax, "a", -22)

    ax = axes[1]
    metrics = [("test_pr_auc", "PR-AUC"), ("test_roc_auc", "ROC-AUC")]
    width = 0.36
    for i, (key, name) in enumerate(metrics):
        xs = [j + (i - 0.5) * width for j in range(len(present))]
        vals = [t[c].get(key) or 0 for c, _, _ in present]
        bars = ax.bar(xs, vals, width=width, label=name, edgecolor=COLOURS["ink"], linewidth=0.5,
                      color=COLOURS["blue"] if i == 0 else COLOURS["muted"])
        bar_values(ax, bars, "{:.3f}")
    ax.set_xticks(range(len(present)), [n for *_, n in present])
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Score on the sealed test cutoff")
    ax.legend(frameon=False, loc="upper left", ncols=2)
    label(ax, "b", -22)
    save(fig, "Fig 21 Performance Benchmark Results")


# --- Fig. 22: scalability (EXP-2 ladder, EXP-3 parallelism) --------------------------------


def fig22() -> None:
    e2 = load("EXP-2", "Fig 22")["trials"]
    e3 = load("EXP-3", "Fig 22")["trials"]
    fig, axes = plt.subplots(2, 2, figsize=(WIDE, 4.6),
                             gridspec_kw={"wspace": 0.3, "hspace": 0.62})
    rungs = sorted((v["customers"], v) for k, v in e2.items()
                   if k.startswith("customers_") and "customers" in v)

    ax = axes[0][0]
    for key, colour, marker in (("b2", COLOURS["B2"], "o"), ("b3", COLOURS["B3"], "s")):
        ok = [(n, v[f"{key}_wall_clock_s"]) for n, v in rungs if v.get(f"{key}_completed")]
        failed = [(n, v.get(f"{key}_wall_clock_s") or 0) for n, v in rungs
                  if f"{key}_completed" in v and not v.get(f"{key}_completed")]
        if ok:
            ax.plot(*zip(*ok, strict=True), marker=marker, markersize=3.5, color=colour,
                    label=f"{key.upper()} completed")
        if failed:
            ax.scatter(*zip(*failed, strict=True), marker="x", s=36, color=COLOURS["warn"], zorder=5,
                       label=f"{key.upper()} failed")
    # P runs once, at the rung that decides the hypothesis. A P that stopped is drawn at the time
    # it had run, which is a lower bound on what finishing would have taken, and labelled so.
    for key, v in e2.items():
        if key.startswith("P_at_customers_") and isinstance(v.get("wall_clock_s"), (int, float)):
            done = bool(v.get("_ok"))
            ax.scatter([int(key.rsplit("_", 1)[1])], [v["wall_clock_s"]], s=30, zorder=6,
                       marker="D" if done else "^", color=COLOURS["P"],
                       label="P completed" if done else "P stopped (time so far)")
    ax.set_xscale("log")
    ax.set_xlabel("Customers in the synthetic dataset")
    ax.set_ylabel("Wall clock (s)")
    ax.legend(frameon=False)
    label(ax, "a", -28)

    ax = axes[0][1]
    series = (("b2_peak_rss_mb", "B2 Python process", COLOURS["B2"], "o"),
              ("b3_jvm_peak_rss_mb", "B3 Spark JVM", COLOURS["B3"], "s"))
    for key, name, colour, marker in series:
        pts = [(n, v[key]) for n, v in rungs if isinstance(v.get(key), (int, float))]
        if pts:
            ax.plot(*zip(*pts, strict=True), marker=marker, markersize=3.5, color=colour, label=name)
    # B3's Python driver shares the container with its JVM. The two peaks need not coincide,
    # so their sum is an upper bound on what B3 held at once, and is labelled as one.
    both = [(n, v["b3_jvm_peak_rss_mb"] + v["b3_peak_rss_mb"]) for n, v in rungs
            if isinstance(v.get("b3_jvm_peak_rss_mb"), (int, float))
            and isinstance(v.get("b3_peak_rss_mb"), (int, float))]
    if both:
        ax.plot(*zip(*both, strict=True), marker="s", markersize=2.5, color=COLOURS["B3"], linestyle="--",
                linewidth=0.9, label="B3 JVM + Python driver (sum of peaks)")
    ax.axhline(3000, color=COLOURS["warn"], linestyle="--", linewidth=0.8)
    ax.annotate("container limit (3 000 MiB)", xy=(0.02, 3000), xycoords=("axes fraction", "data"),
                xytext=(0, 2), textcoords="offset points", fontsize=6.5, color=COLOURS["warn"])
    ax.set_xscale("log")
    ax.set_xlabel("Customers in the synthetic dataset")
    ax.set_ylabel("Peak resident memory (MiB)")
    ax.legend(frameon=False, loc="center left")
    label(ax, "b", -28)

    arms = sorted((int(k.split("_")[1]), v) for k, v in e3.items() if k.startswith("local_"))
    cores = [c for c, _ in arms]
    ax = axes[1][0]
    ax.plot(cores, cores, linestyle=":", color=COLOURS["muted"], label="linear")
    ax.plot(cores, [v.get("raw_speed_up_vs_1_core") or 0 for _, v in arms], marker="s",
            markersize=4.5, color=COLOURS["blue"], linestyle="--", label="measured, wall clock")
    ax.plot(cores, [v.get("speed_up_vs_1_core") or 0 for _, v in arms], marker="o",
            markersize=2.8, color=COLOURS["P"], zorder=4, label="measured, work only")
    ax.set_xticks(cores, [f"local[{c}]" for c in cores])
    ax.set_xlabel("Spark parallelism (cores in one JVM)")
    ax.set_ylabel("Speed-up over one core (x)")
    ax.legend(frameon=False, loc="upper left")
    label(ax, "c", -28)

    ax = axes[1][1]
    width = 0.36
    xs = range(len(arms))
    wall = ax.bar([x - width / 2 for x in xs], [v.get("wall_clock_s") or 0 for _, v in arms],
                  width=width, color=COLOURS["blue"], edgecolor=COLOURS["ink"], linewidth=0.5,
                  label="wall clock")
    work = ax.bar([x + width / 2 for x in xs],
                  [v.get("executor_run_time_s") or 0 for _, v in arms], width=width,
                  color=COLOURS["muted"], edgecolor=COLOURS["ink"], linewidth=0.5,
                  label="sum of task run times")
    bar_values(ax, wall, "{:.0f}")
    bar_values(ax, work, "{:.0f}")
    ax.set_xticks(list(xs), [f"local[{c}]" for c in cores])
    ax.set_xlabel("Spark parallelism (cores in one JVM)")
    ax.set_ylabel("Time (s)")
    ax.legend(frameon=False, loc="upper left")
    label(ax, "d", -28)
    save(fig, "Fig 22 Scalability Analysis")


# --- Fig. 23: agent efficiency (EXP-8: where each fault stops, and what each gate costs) ---


def fig23() -> None:
    e8 = load("EXP-8", "Fig 23")["trials"]
    classes: dict[str, dict[str, int]] = {}
    for condition, v in e8.items():
        if "_" not in condition or condition.startswith("control"):
            continue
        if v.get("toolchain_failure"):
            continue   # a gate that failed for another reason says nothing about the fault
        name = condition.split("_", 1)[1].replace("_", " ")
        bucket = classes.setdefault(name, {"gate 2": 0, "gate 4": 0, "gate 5": 0, "other gate": 0,
                                           "not before execution": 0})
        gate = v.get("first_failed_gate")
        if gate in (5, "5") or v.get("fixture_gate_passed") is False:   # the fixtures stopped it
            bucket["gate 5"] += 1
        elif gate is None:
            bucket["not before execution"] += 1
        elif gate in (2, "2"):
            bucket["gate 2"] += 1
        elif gate in (4, "4"):
            bucket["gate 4"] += 1
        else:
            bucket["other gate"] += 1
    fig, axes = plt.subplots(1, 2, figsize=(WIDE, 2.5), gridspec_kw={"wspace": 0.35})

    ax = axes[0]
    # In the order the gates meet them: the static scan, the compiler, the fixtures, then nothing.
    names = sorted(classes, key=lambda n: (-classes[n]["gate 2"], -classes[n]["gate 4"],
                                           -classes[n]["gate 5"], n))
    left = [0] * len(names)
    shades = {"gate 2": COLOURS["P"], "gate 4": COLOURS["blue"], "gate 5": COLOURS["B3"],
              "other gate": COLOURS["pink"], "not before execution": COLOURS["warn"]}
    for outcome, colour in shades.items():
        widths = [classes[n][outcome] for n in names]
        if not any(widths):
            continue
        ax.barh(names, widths, left=left, color=colour, edgecolor=COLOURS["ink"], linewidth=0.5,
                label=f"stopped at {outcome}" if outcome.startswith("gate") or outcome == "other gate"
                else outcome)
        left = [a + b for a, b in zip(left, widths, strict=True)]
    ax.set_xlabel("Seeded faults (count)")
    ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncols=3,
              fontsize=6.5)
    ax.invert_yaxis()
    label(ax, "a", -24)

    # (b) What each gate costs: seconds from submission to the verdict on each seeded fault. A
    # fault no gate caught is drawn hollow at the time it took to pass all of them.
    ax = axes[1]
    for i, name in enumerate(names):
        # Time to the first verdict: the run also repeats a detected fault's gates once, to
        # measure the circuit breaker, and that second attempt is not the gate's cost.
        times = [(v.get("verdict_s", v.get("wall_clock_s")),
                  bool(v.get("detected_before_data", v.get("detected"))))
                 for c, v in e8.items()
                 if not c.startswith("control") and not v.get("toolchain_failure")
                 and c.split("_", 1)[-1].replace("_", " ") == name
                 and isinstance(v.get("verdict_s", v.get("wall_clock_s")), (int, float))]
        for j, (secs, caught) in enumerate(times):
            x = i + (j - (len(times) - 1) / 2) * 0.08
            ax.scatter([x], [secs], s=14, marker="o", linewidths=0.7,
                       facecolors=COLOURS["blue"] if caught else "none",
                       edgecolors=COLOURS["blue"] if caught else COLOURS["warn"], zorder=3)
    ax.scatter([], [], s=14, color=COLOURS["blue"], label="stopped by a gate")
    ax.scatter([], [], s=14, facecolors="none", edgecolors=COLOURS["warn"],
               label="passed every gate run")
    ax.set_yscale("log")
    ax.set_xticks(range(len(names)), names, rotation=15, ha="right", fontsize=6.5)
    ax.set_ylabel("Time to the gate verdict (s)")
    ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncols=2)
    label(ax, "b", -30)
    save(fig, "Fig 23 Agent Efficiency Comparison")


# --- Fig. 24: comparative radar (EXP-1) -----------------------------------------------------


def fig24() -> None:
    import math

    t = load("EXP-1", "Fig 24")["trials"]
    present = [(c, key, name) for c, key, name in CONFIGS if c in t]
    # Each axis is scaled so the best of the three configurations is 1. Higher is better on
    # every axis; wall clock, Brier score and manual steps are inverted to make it so.
    raw = {name: t[c] for c, _, name in present}
    axes_def = [
        ("PR-AUC", lambda v: v.get("test_pr_auc"), False),
        ("ROC-AUC", lambda v: v.get("test_roc_auc"), False),
        ("1 - Brier score", lambda v: None if v.get("test_brier") is None else 1 - v["test_brier"], False),
        ("speed\n(1 / wall clock)", lambda v: v.get("wall_clock_s"), True),
        ("unattended\n(1 / (1 + manual steps))",
         lambda v: 1 / (1 + (v.get("human_actions_excluding_approval") or 0)), False),
    ]
    scores: dict[str, list[float]] = {name: [] for name in raw}
    for _, get, invert in axes_def:
        values = {name: get(v) for name, v in raw.items()}
        known = {n: x for n, x in values.items() if isinstance(x, (int, float)) and x > 0}
        for name in raw:
            x = known.get(name)
            if x is None:
                scores[name].append(0.0)
            elif invert:
                scores[name].append(min(known.values()) / x)
            else:
                scores[name].append(x / max(known.values()))
    angles = [2 * math.pi * i / len(axes_def) for i in range(len(axes_def))]
    fig = plt.figure(figsize=(NARROW, 3.1))
    ax = fig.add_subplot(projection="polar")
    for (_, key, name) in present:
        vals = scores[name] + scores[name][:1]
        ax.plot(angles + angles[:1], vals, color=COLOURS[key], linewidth=1.1, label=name)
        ax.fill(angles + angles[:1], vals, color=COLOURS[key], alpha=0.12)
    ax.set_xticks(angles, [a for a, *_ in axes_def], fontsize=6.5)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0], ["0.25", "0.5", "0.75", "1"], fontsize=6)
    ax.set_ylim(0, 1.05)
    ax.spines["polar"].set_linewidth(0.5)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncols=3)
    save(fig, "Fig 24 Comparative Radar Chart")


# --- Fig. 25: reliability (EXP-4, -6, -8, -9, -10; trust scores) --------------------------


def spark_faults_landed(e6: dict[str, dict]) -> dict[str, dict]:
    """EXP-6's arms whose fault landed: a local-mode kill read as a lost executor, a cluster
    executor killed under a running task (Spark's own log names the task it lost), or an
    out-of-memory. Each carries `_recovered`: the job's output came back, and for the cluster
    arm the very job that lost the task absorbed it (DEC-46, DEC-48)."""
    out = {}
    for name, v in e6.items():
        kill = v.get("container_killed") and v.get("first_error_class") == "EXECUTOR_LOST"
        cluster = v.get("executor_killed") and v.get("spark_lost_task_lines")
        oom = "EXECUTOR_OOM" in (v.get("first_error_class"), v.get("feature_first_error_class"))
        if kill or cluster or oom:
            recovered = v.get("recovered") and (
                v.get("spark_absorbed_in_the_same_job") if cluster else True)
            out[name] = {**v, "_recovered": bool(recovered)}
    return out


def fig25() -> None:
    rows: list[tuple[str, int, int]] = []

    e4 = load("EXP-4", "Fig 25")["trials"]
    faults = {k: v for k, v in e4.items() if k != "baseline_clean"}
    rows.append(("schema faults stopped or handled (EXP-4)",
                 sum(1 for v in faults.values() if v.get("_ok")), len(faults)))
    e6 = load("EXP-6", "Fig 25")["trials"]
    # Only a fault that landed is a failure to recover from (EXP-6 counts no other); an arm whose
    # fault did not land, or that injected none, is not drawn as a recovery.
    landed = spark_faults_landed(e6)
    if landed:
        rows.append(("Spark faults that landed, recovered (EXP-6)",
                     sum(1 for v in landed.values() if v.get("_recovered")), len(landed)))
    e8 = load("EXP-8", "Fig 25")["trials"]
    seeded = {k: v for k, v in e8.items()
              if not k.startswith("control") and not v.get("toolchain_failure")}
    rows.append(("generated-code faults stopped before reading data (EXP-8)",
                 sum(1 for v in seeded.values()
                     if v.get("detected_before_data", v.get("detected"))), len(seeded)))
    e9 = load("EXP-9", "Fig 25")["trials"]
    leaks = {k: v for k, v in e9.items() if k.startswith("leak")}
    rows.append(("planted leaks blocked (EXP-9)",
                 sum(1 for v in leaks.values() if v.get("blocked_by_any_defence")), len(leaks)))
    e10 = load("EXP-10", "Fig 25")["trials"]
    attempts = {k: v for k, v in e10.items() if k != "audit_and_alerts"}
    rows.append(("governance bypass attempts refused (EXP-10)",
                 sum(1 for v in attempts.values() if v.get("_ok")), len(attempts)))

    fig, axes = plt.subplots(1, 2, figsize=(WIDE, 2.8),
                             gridspec_kw={"wspace": 0.25, "width_ratios": [1.15, 1]})
    ax = axes[0]
    names = [r[0] for r in rows]
    shares = [r[1] / r[2] if r[2] else 0 for r in rows]
    bars = ax.barh(names, shares, color=COLOURS["P"], edgecolor=COLOURS["ink"], linewidth=0.5)
    for bar, (_, k, n) in zip(bars, rows, strict=True):
        ax.annotate(f"{k} of {n}", xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(2, 0), textcoords="offset points", va="center", fontsize=6.5)
    ax.set_xlim(0, 1.18)
    ax.set_xlabel("Share of injected cases (fraction)")
    ax.tick_params(axis="y", labelsize=6.5)
    ax.invert_yaxis()
    label(ax, "a", -24)

    ax = axes[1]
    trust = analysis("trust_scores.json", "Fig 25")
    runs = [r for r in trust.get("runs", []) if r.get("trust_score") is not None]
    keys = ("chain", "gates", "calib", "claims", "approval", "lineage")
    width = 0.8 / max(1, len(runs))
    for i, run in enumerate(runs):
        comp = run["components"]
        xs = [j + (i - (len(runs) - 1) / 2) * width for j in range(len(keys))]
        vals = [comp.get(k) if comp.get(k) is not None else 0 for k in keys]
        ax.bar(xs, vals, width=width, edgecolor=COLOURS["ink"], linewidth=0.4,
               color=[COLOURS["P"], COLOURS["blue"], COLOURS["B2"], COLOURS["pink"]][i % 4],
               label=f"{run['run']} (TS {run['trust_score']:.2f})")
        for x, k in zip(xs, keys, strict=True):
            if comp.get(k) is None:
                ax.annotate("n/a", xy=(x, 0.02), ha="center", va="bottom", fontsize=5.5,
                            rotation=90)
    ax.set_xticks(range(len(keys)), keys, fontsize=6.5, rotation=25, ha="right")
    ax.set_ylim(0, 1.75)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_ylabel("Trust-score component (0 to 1)")
    ax.legend(frameon=False, loc="upper center", fontsize=6)
    label(ax, "b", -30)
    save(fig, "Fig 25 Reliability Assessment Model")


# --- Fig. 28: business impact (the published champion) -------------------------------------


def fig28() -> None:
    snap = analysis("business_snapshot.json", "Fig 28")
    fig, axes = plt.subplots(1, 3, figsize=(WIDE, 2.4), gridspec_kw={"wspace": 0.45})

    ax = axes[0]
    gain = snap["cumulative_gain"]
    ks = [0.0] + [g["k"] for g in gain]
    captured = [0.0] + [g["cumulative_gain"] for g in gain]
    ax.plot([k * 100 for k in ks], [c * 100 for c in captured], color=COLOURS["P"],
            label="model ranking")
    ax.plot([0, 100], [0, 100], linestyle=":", color=COLOURS["muted"], label="random order")
    ax.set_xlabel("Customers contacted, highest risk first (%)")
    ax.set_ylabel("Churners reached (%)")
    ax.legend(frameon=False, loc="lower right")
    label(ax, "a", -26)

    ax = axes[1]
    review = snap.get("segment_threshold_review") or {}
    order = {"under 6 months": 0, "6-12 months": 1, "1-2 years": 2, "over 2 years": 3}
    segs = sorted((s for s in review.get("segments", [])
                   if s.get("recall_at_global_threshold") is not None),
                  key=lambda s: order.get(s["segment"].split("=", 1)[-1], 9))
    names = [s["segment"].split("=", 1)[-1] for s in segs]
    width = 0.38
    xs = range(len(segs))
    one = ax.bar([x - width / 2 for x in xs], [s["recall_at_global_threshold"] for s in segs],
                 width=width, color=COLOURS["warn"], edgecolor=COLOURS["ink"], linewidth=0.5,
                 label="one threshold")
    own = ax.bar([x + width / 2 for x in xs],
                 [s.get("recall_at_segment_threshold") or 0 for s in segs], width=width,
                 color=COLOURS["blue"], edgecolor=COLOURS["ink"], linewidth=0.5,
                 label="each segment's own")
    bar_values(ax, one, "{:.2f}", 1)
    bar_values(ax, own, "{:.2f}", 1)
    ax.set_xticks(list(xs), names, rotation=20, ha="right", fontsize=6.5)
    ax.set_ylim(0, 0.75)
    ax.set_ylabel("Churners flagged, by tenure (recall)")
    ax.legend(frameon=False, loc="upper left", fontsize=6.5)
    label(ax, "b", -38)

    ax = axes[2]
    order = ["high", "medium", "low"]
    bands = snap["risk_bands_official"]
    counts = [bands.get(b, 0) for b in order]
    bars = ax.bar(order, counts, color=[COLOURS["warn"], COLOURS["B2"], COLOURS["B3"]],
                  edgecolor=COLOURS["ink"], linewidth=0.5)
    bar_values(ax, bars, "{:.0f}")
    ax.set_xlabel("Risk band")
    ax.set_ylabel("Customers scored (count)")
    label(ax, "c", -26)
    save(fig, "Fig 28 Business Impact Dashboard")


def main() -> None:
    style()
    print("writing charts at 600 DPI:")
    for build in (fig21, fig22, fig23, fig24, fig25, fig28):
        try:
            build()
        except (FileNotFoundError, KeyError) as exc:
            print(f"  {build.__name__}: skipped - {type(exc).__name__}: {exc}")
    (Path(__file__).resolve().parent / "charts_provenance.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
