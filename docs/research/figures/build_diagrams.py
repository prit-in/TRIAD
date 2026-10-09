"""Write every diagram figure as an editable draw.io file: `drawio/Fig NN <Title>.drawio`.

    python docs/research/figures/build_diagrams.py

Charts of measured data are not here - they are drawn from the stored experiment outputs by
`build_charts.py`, so no measured number is ever retyped into a drawing. The numbers that do
appear below are counts of things in the code (rules, gates, actions, tables), each checked
against the repository when this file was written.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from drawio import Diagram

OUT = Path(__file__).resolve().parent / "drawio"
W = 880


def row(d: Diagram, prefix: str, labels: list[str], y: int, *, kind: str = "neutral",
        x0: int = 20, x1: int = W - 20, h: int = 56, gap: int = 32, size: int = 16,
        dashed: bool = False, bold: bool = False) -> list[str]:
    n = len(labels)
    w = int((x1 - x0 - gap * (n - 1)) / n)
    ids = []
    for i, label in enumerate(labels):
        ids.append(d.box(f"{prefix}{i}", label, x0 + i * (w + gap), y, w, h, kind=kind,
                         size=size, dashed=dashed, bold=bold))
    return ids


def chain(d: Diagram, ids: list[str], *, dashed: bool = False, labels: list[str] | None = None) -> None:
    for i in range(len(ids) - 1):
        d.edge(ids[i], ids[i + 1], (labels or [""] * len(ids))[i], dashed=dashed)


# --- Section 1 ------------------------------------------------------------------------------
def fig01() -> Diagram:
    d = Diagram("Fig 01 Evolution of Data Science Programming", W, 300)
    # Breaks chosen by hand: left to wrap, two labels touched their borders and one ran to
    # four lines with single words stranded.
    ids = row(d, "s", ["Single-machine\nscientific stack\n(Python, NumPy, SciPy)",
                       "Distributed engines\n(MapReduce, Spark,\nSQL)",
                       "Machine learning\noperations (pipelines,\nregistries, monitoring)",
                       "Governed agentic\ndata science (agents\npropose, rules decide)"],
              60, kind="white", h=86, size=15)
    chain(d, ids)
    row(d, "b", ["Bottleneck: one\nmachine's memory", "Bottleneck: people\nand pipelines",
                 "Bottleneck: laborious\ndiscipline", "Open question: what\nmay an agent decide?"],
        176, kind="neutral", h=56, size=14)
    d.text("t", "Each stage moves the binding constraint; none removes the earlier stages.",
           20, 250, W - 40, 30, italic=True, size=14)
    return d


def fig02() -> Diagram:
    d = Diagram("Fig 02 Agentic Analytics Ecosystem Overview", W, 430)
    d.group("ga", "Agents (propose and compose)", 20, 10, W - 40, 80, kind="agent")
    # Seven boxes a row leave little width: narrower gaps, 13 px text, and breaks set by hand,
    # because the automatic wrap pushed several labels against their borders.
    row(d, "a", ["Discovery", "Profiling", "Cleaning", "Spark\nplanning", "Code\ngeneration",
                 "Model\nselection", "Reporting"], 40, kind="agent", h=44, size=13, x0=30, x1=W - 30,
        gap=16)
    flow = row(d, "f", ["Versioned\nsources", "Governed\ningestion", "Generated\nScala jobs\nin sandbox",
                        "Point-in-time\nfeatures,\nlabels", "Python\nmodels: train,\ncalibrate",
                        "Approval by\na second\nperson", "Champion\nand scored\ncustomers"],
               150, kind="compute", h=80, size=13, gap=16)
    chain(d, flow)
    d.group("gg", "Governance (decides)", 20, 270, W - 40, 140, kind="govern")
    row(d, "g", ["Routing rules\nLR-01 to LR-10", "Nine validation\ngates", "Acceptance policy\nAP-1 to AP-8",
                 "Separation\nof duties", "Hash-chained\naudit per run"],
        310, kind="govern", h=70, size=14, x0=30, x1=W - 30)
    # Each edge runs in an empty gap, not along a row: the slot code drops below the agents'
    # group before turning, and each governance edge rises in the gap above its own group.
    d.edge("a4", "f2", "slot code", dashed=True, exit="bottom", entry="top",
           points=[(d.cx("a4"), 120), (d.cx("f2"), 120)])
    for g, f in (("g1", "f2"), ("g2", "f4"), ("g3", "f5")):
        d.edge(g, f, "", dashed=True, exit="top", entry="bottom",
               points=[(d.cx(g), 250), (d.cx(f), 250)])
    return d


# --- Sections 2 to 4 --------------------------------------------------------------------------
def fig03() -> Diagram:
    d = Diagram("Fig 03 Challenges in Modern Data Science Platforms", W, 400)
    d.box("c", "Modern data\nscience platform", 350, 160, 180, 80, kind="neutral", bold=True)
    items = [
        # Names and counts as Section 2 states them.
        ("w", "Workflow complexity\nfive or more components,\nthree languages", 20, 20),
        ("m", "Code maintenance\nhand-written and generated jobs", 600, 20),
        ("s", "Scalability\nwhich engine, and who decides", 20, 300),
        ("h", "Heterogeneous environments\nversions across runtimes", 600, 300),
        ("a", "Analytics automation without\naccountability: who decided a\nmodel is fit to use?", 310, 300),
    ]
    for cid, label, x, y in items:
        d.box(cid, label, x, y, 260, 76, kind="alert" if cid == "a" else "white", size=14)
        d.edge("c", cid)
    return d


def fig04() -> Diagram:
    # Two groups rather than an edge from the root to every topic: eight edges to two rows had to
    # cross the first row to reach the second, and the export showed lines through the labels.
    d = Diagram("Fig 04 Literature Taxonomy", W, 450)
    d.box("r", "Agentic data science programming", 290, 10, 300, 50, kind="neutral", bold=True)
    # Section numbers as the manuscript sets them: Roman sections, lettered subsections.
    d.group("g1", "Engineering foundations (Sections III-A to III-D)", 20, 90, W - 40, 110,
            kind="compute", size=14)
    row(d, "a", ["Python analytics", "Scala, Spark, distributed processing",
                 "Configuration tuning", "MLOps and engineering of ML systems"],
        126, kind="compute", h=60, size=14, x0=32, x1=W - 32, gap=20)
    d.group("g2", "Automation and assurance (Sections III-E to III-H)", 20, 230, W - 40, 110,
            kind="agent", size=14)
    row(d, "b", ["Data quality and provenance", "AutoML and hyperparameter optimization",
                 "LLM agents and generated code", "Explainability,\ntrust, and fairness"],
        266, kind="agent", h=60, size=14, x0=32, x1=W - 32, gap=20)
    d.box("app", "Application: customer churn prediction (Section III-I)", 220, 380, 440, 50,
          kind="data", size=14)
    d.edge("r", "g1", exit="bottom", entry="top")
    d.edge("g1", "g2", exit="bottom", entry="top")
    d.edge("g2", "app", exit="bottom", entry="top", dashed=True)
    return d


def fig05() -> Diagram:
    d = Diagram("Fig 05 Requirements Engineering Framework", W, 360)
    ids = row(d, "s", ["Product requirements\n84 functional\n48 non-functional",
                       "Tests\nunit, AT, ATC, ST, TC",
                       "Traceability matrix\nstatus per requirement",
                       "Evidence\ntests, recorded runs,\nexperiment outputs"],
              60, kind="white", h=96, size=14)
    chain(d, ids)
    d.box("au", "Audit: re-read the code, not earlier documents;\ndowngrade any claim whose evidence is missing",
          200, 220, 480, 70, kind="govern", size=14)
    # Evidence feeds the audit from the right; the audit's correction rises straight into the
    # matrix above it. Sharing one track, the two read as a single line.
    d.edge(ids[3], "au", "", exit="bottom", entry="right",
           points=[(d.cx(ids[3]), d.cy("au"))])
    d.edge("au", ids[2], "corrects", exit=(d.at("au", d.cx(ids[2])), 0), entry="bottom")
    # The matrix's claim, not universal coverage: some requirements are recorded as not met
    # or not run, and Section 4 says so.
    d.text("t", "Every requirement is traced to its evidence, or recorded as not met or not run.",
           20, 310, W - 40, 30, italic=True, size=14)
    return d


# --- Section 5 ------------------------------------------------------------------------------
def fig06() -> Diagram:
    # The four layers are 30 units apart, so an edge label can sit in a gap rather than on a
    # group's border, which is where the 15-unit gaps put the "queue" and "submit" labels.
    d = Diagram("Fig 06 Agentic Data Science Programming Architecture Integrating Python Scala and Apache Spark", W, 660)
    d.group("gp", "Presentation and service", 20, 10, 690, 90, kind="person")
    d.box("ui", "Dashboard\nReact, 11 pages", 40, 40, 200, 50, kind="person", size=14)
    d.box("api", "API (Python, FastAPI)\n41 endpoints, roles", 260, 40, 220, 50, kind="person", size=14)
    d.box("ppl", "People: engineers,\napprovers, analysts", 500, 40, 190, 50, kind="person", size=14)
    d.group("go", "Orchestration (Python)", 20, 130, 690, 150, kind="agent")
    d.box("wk", "Worker\nstate machine, queue, leases", 40, 165, 210, 56, kind="agent", size=14)
    # Routing beside the worker that consults it: with the scheduler between them, their edge
    # ran through the scheduler's box.
    d.box("rt", "Routing rules\nLR-01 to LR-10", 270, 165, 200, 56, kind="agent", size=14)
    d.box("sc", "Scheduler\nRT-1 to RT-9, landing folders", 490, 165, 200, 56, kind="agent", size=14)
    d.box("cg", "Code generation\ntemplates, 9 gates", 40, 230, 210, 40, kind="agent", size=14)
    d.box("tr", "Tool registry: allowlist, schema, policy", 270, 230, 420, 40, kind="agent", size=14)
    d.group("gc", "Compute", 20, 310, 690, 160, kind="compute")
    d.box("py", "Python ML\nscikit-learn, XGBoost, Optuna\ncalibration, SHAP", 40, 345, 210, 110, kind="compute", size=14)
    d.box("sr", "Sandbox runner\nonly holder of the\ncontainer socket", 270, 345, 160, 110, kind="govern", size=14)
    d.box("sp", "Scala jobs on Apache Spark\nCleanJob, FeatureJob, LabelJob\none container each, no network",
          450, 345, 240, 110, kind="compute", size=14)
    d.group("gs", "State", 20, 500, 690, 140, kind="data")
    d.box("pg", "PostgreSQL\n21 tables: runs, decisions,\napprovals, audit, lineage", 40, 535, 220, 90, kind="data", shape="cylinder", size=14)
    # MLflow under Python ML's side and Parquet under the Spark jobs: each writer then reaches
    # its store without crossing the other's path.
    d.box("ml", "MLflow\nexperiments,\nmodel registry", 280, 535, 200, 90, kind="data", shape="cylinder", size=14)
    d.box("fs", "Versioned Parquet\nraw, clean, features,\nlabels, quarantine", 500, 535, 190, 90, kind="data", shape="cylinder", size=14)
    d.group("gg", "Governance", 725, 10, 135, 630, kind="govern")
    # Approvals level with the people who decide them, the policy engine level with the tool
    # registry that consults it: the two dashed edges then run side by side instead of crossing.
    d.box("ap", "Approvals\nseparation\nof duties", 735, 40, 115, 90, kind="govern", size=14)
    d.box("pe", "Policy engine\n23 actions,\nfail-closed", 735, 205, 115, 90, kind="govern", size=14)
    d.box("au", "Audit\nhash chain\nper run", 735, 390, 115, 90, kind="govern", size=14)
    # Each labelled edge turns in the gap between two layers, clear of the dashboard and of the
    # orchestration group's title.
    d.edge("api", "wk", "queue", exit=(d.at("api", 300), 1), entry=(d.at("wk", 230), 0),
           points=[(300, 115), (230, 115)])
    d.edge("wk", "rt")
    d.edge("cg", "tr")
    d.edge("tr", "sr", "submit", exit="bottom", entry="top",
           points=[(d.cx("tr"), 295), (d.cx("sr"), 295)])
    d.edge("sr", "sp")
    # The job writes to staging and the platform publishes (Section 5): an edge labelled
    # "publish" from the job said the opposite, so the edge is unlabelled and the text says it.
    d.edge("sp", "fs", exit=(d.at("sp", d.cx("fs")), 1), entry="top")
    d.edge("py", "ml", exit=(d.at("py", 200), 1), entry="top",
           points=[(200, 485), (d.cx("ml"), 485)])
    # Unlabelled: in the 45-unit gutter a label covered the line and both borders.
    d.edge("ppl", "ap", "", dashed=True, exit="right", entry="left",
           points=[(708, d.cy("ppl")), (708, d.cy("ap"))])
    d.edge("tr", "pe", "", dashed=True, exit="right", entry="left",
           points=[(708, d.cy("tr")), (708, d.cy("pe"))])
    return d


def fig07() -> Diagram:
    d = Diagram("Fig 07 Multi-Layer Analytics Stack", W, 440)
    layers = [
        ("l1", "Presentation: dashboard with provenance on every number", "person", 20),
        ("l2", "Service: 41 endpoints, roles, problem details, idempotent runs", "person", 100),
        ("l3", "Orchestration: state machine, queue, scheduler, routing, code generation", "agent", 180),
        ("l4", "Compute: Scala jobs on Spark in containers; Python learning", "compute", 260),
        ("l5", "State: PostgreSQL, versioned Parquet, MLflow", "data", 340),
    ]
    for cid, label, kind, y in layers:
        d.box(cid, label, 20, y, 690, 64, kind=kind, size=16)
    for (a, *_), (b, *_) in pairwise(layers):
        d.edge(a, b)
    d.box("gov", "Governance\n\npolicy\napprovals\naudit chain\nmodel integrity", 730, 20, 130, 384,
          kind="govern", size=15)
    return d


# --- Section 6 ------------------------------------------------------------------------------
def fig08() -> Diagram:
    d = Diagram("Fig 08 Agentic Programming Architecture", W, 500)
    d.box("orc", "Orchestrator (deterministic): decides what happens next, never content",
          20, 10, W - 40, 50, kind="agent", bold=True, size=15)
    groups = [("Data agents", ["Discovery", "Profiling", "Cleaning"]),
              ("Build agents", ["Spark planning", "Code generation", "Execution"]),
              ("Model agents", ["Model selection", "Training", "Evaluation", "Optimization"]),
              ("Assurance agents", ["Verification", "Governance", "Reporting"])]
    x = 20
    widths = [190, 190, 210, 190]
    for gi, ((title, members), w) in enumerate(zip(groups, widths, strict=True)):
        d.group(f"g{gi}", title, x, 80, w, 250, kind="agent")
        for mi, m in enumerate(members):
            d.box(f"a{gi}{mi}", m, x + 12, 115 + mi * 52, w - 24, 42, kind="white", size=14)
        d.edge("orc", f"g{gi}")
        x += w + 16
    # The registry stops short of the right edge, leaving a corridor for the provider's edge,
    # which then crosses below the agent groups and rises in the gap beside the build agents.
    d.box("reg", "Tool registry: permitted agents, argument schema,\npolicy action, audit entry",
          20, 356, 540, 44, kind="govern", size=14)
    d.box("gates", "Nine validation gates", 20, 426, 260, 50, kind="govern", size=14)
    d.box("sbx", "Sandbox runner and containers", 300, 426, 260, 50, kind="compute", size=14)
    d.box("llm", "LLM provider (optional, not configured\nhere): cleaning plans, slot code, narratives",
          600, 426, 260, 50, kind="white", dashed=True, size=12)
    # Gates first, then the registry's submit, then the sandbox - the order the code runs them
    # (Fig. 6): the gates run before any registry call, and spark.submit_job reaches the sandbox.
    d.edge("gates", "reg", exit="top", entry=(d.at("reg", d.cx("gates")), 1))
    d.edge("reg", "sbx", exit=(d.at("reg", d.cx("sbx")), 1), entry="top")
    gap = (d.geom["g1"][0] + d.geom["g1"][2] + d.geom["g2"][0]) / 2
    d.edge("llm", "a11", "slot bodies", dashed=True, exit="top", entry="right",
           points=[(d.cx("llm"), 343), (gap, 343), (gap, d.cy("a11"))])
    return d


def fig09() -> Diagram:
    d = Diagram("Fig 09 Multi-Agent Collaboration Workflow", W, 470)
    top = row(d, "t", ["Discovery:\nregister, contract", "Profiling:\nquality score", "Cleaning:\nplan from library",
                       "Spark planning:\nroute, size", "Code generation:\nfill the slot"],
              30, kind="agent", h=70, size=14)
    chain(d, top)
    mid = row(d, "m", ["Verification:\ngates 1-5", "Execution:\ndry run, gate 6", "Execution:\nfull run, gates 7-9",
                       "Training:\nfive families", "Model selection:\nAP-1 to AP-8"],
              190, kind="compute", h=70, size=14)
    d.edge(top[4], mid[0], exit="bottom", entry="top")
    chain(d, mid)
    low = row(d, "l", ["Governance:\napproval by\nanother person", "Deployment:\nalias, smoke test,\nrollback",
                       "Reporting:\nclaim-checked"], 350, kind="govern", h=80, size=14, x0=200, x1=W - 20)
    d.edge(mid[4], low[0], "propose", exit="bottom", entry="top")
    chain(d, low)
    # Exactly under "Verification", so the refused edge is one straight line.
    vx, _, vw, _ = d.geom[mid[0]]
    d.box("esc", "Escalation\nwith evidence", vx, 360, vw, 60, kind="alert", size=14)
    d.edge(mid[0], "esc", "refused", dashed=True, exit="bottom")
    return d


# --- Section 7 ------------------------------------------------------------------------------
def fig10() -> Diagram:
    # A U: ingestion left to right, then the data path back right to left beneath it. Each
    # outcome sits next to the step that produces it, so every outcome edge is one straight line
    # - the earlier layout sent three of them across the whole middle row.
    d = Diagram("Fig 10 Autonomous Data Engineering Pipeline", W, 450)
    # In the order register_version runs: checksum (a duplicate stops here) and contract
    # first, then the write-once copy and the version record.
    ids = row(d, "a", ["Landing folder\nor upload", "Checksum and\ncontract check", "Write-once raw,\nregister version",
                       "Profile\nquality score Q", "Cleaning job\n(library rules)"],
              100, kind="data", h=70, size=14)
    chain(d, ids)
    ids2 = row(d, "b", ["Features and\nmatured labels", "Snapshot expansion\n(days before T)",
                        "Customer-day\naggregate", "Publish clean\n(atomic, manifest)", "Policy check:\nrows affected"],
               240, kind="compute", h=70, size=14)
    d.edge(ids[4], ids2[4], exit="bottom", entry="top")
    for right, left in pairwise(reversed(ids2)):
        d.edge(right, left, exit="left", entry="right")
    d.box("ref", "Refused at ingestion:\nmissing required column", d.cx(ids[1]) - 120, 10, 240, 56,
          kind="alert", size=14)
    d.box("q", "Quarantine with reason codes\n(nothing is deleted)", W - 280, 10, 260, 56,
          kind="alert", size=14)
    d.box("acc", "Cleaning's row accounting:\nin = out + quarantined + duplicates", W - 320, 370, 300, 60,
          kind="govern", size=14)
    d.edge(ids[1], "ref", dashed=True, exit="top", entry=(d.at("ref", d.cx(ids[1])), 1))
    d.edge(ids[4], "q", dashed=True, exit="top", entry=(d.at("q", d.cx(ids[4])), 1))
    # The cleaning job balances its own accounting, which then feeds the policy check: the
    # arrow points into the check (Section 7), not out of it.
    d.edge("acc", ids2[4], dashed=True, exit=(d.at("acc", d.cx(ids2[4])), 0), entry="bottom")
    return d


# --- Section 8 ------------------------------------------------------------------------------
def fig11() -> Diagram:
    d = Diagram("Fig 11 Intelligent Analytics Workflow", W, 420)
    ids = row(d, "a", ["Features and\nlabels", "Hygiene on names\nH2, H3, H7", "Temporal split\nwith 90-day gaps",
                       "Hygiene on training\nrows: H4", "Five candidate\nfamilies"], 30, kind="compute", h=70, size=14)
    chain(d, ids)
    # The stages in the order the code runs them (Algorithm 3): the test cutoff is scored once
    # (D) before the champion comparison (E), and E decides all three outcomes.
    ids2 = row(d, "b", ["Stage A: screen\n(incl. leakage alarm)", "Stage B: tune\nfinalists (Optuna)",
                        "Stage C: calibrate,\nAP-4 to AP-6", "Stage D: score the\ntest set once",
                        "Stage E: AP-8,\nbeat the champion?"],
               190, kind="agent", h=70, size=14)
    d.edge(ids[4], ids2[0], exit="bottom", entry="top")
    chain(d, ids2)
    cols = [d.geom[b] for b in ids2[2:]]
    for cid, label, (x, _, w, _) in zip(("c0", "c1", "c2"),
                                        ["Escalate with a\nreason code", "Keep the\nchampion",
                                         "Promote: request\napproval"], cols, strict=True):
        d.box(cid, label, x, 340, w, 56, kind="govern", size=14)
    e = ids2[4]
    # One exit per outcome across E's lower edge, each on its own lane, so no two cross.
    d.edge(e, "c0", "a rule failed", dashed=True, exit=(0.15, 1), entry="top",
           points=[(d.geom[e][0] + 0.15 * d.geom[e][2], 285), (d.cx("c0"), 285)])
    d.edge(e, "c1", "AP-8 failed", dashed=True, exit=(0.45, 1), entry="top",
           points=[(d.geom[e][0] + 0.45 * d.geom[e][2], 312), (d.cx("c1"), 312)])
    d.edge(e, "c2", "proposed", dashed=True, exit=(0.8, 1), entry=(0.8, 0))   # c2 is under E
    return d


def fig12() -> Diagram:
    d = Diagram("Fig 12 AutoML and Feature Engineering Architecture", W, 420)
    d.group("gf", "Feature engineering", 20, 10, 400, 390, kind="compute")
    d.box("cat", "Feature catalogue: 42 definitions\n38 implemented, 2 evaluation-only,\n2 unavailable",
          40, 45, 360, 80, kind="data", size=14)
    d.box("fj", "Feature job (Scala template)\nfive groups, point-in-time", 40, 145, 360, 60, kind="compute", size=14)
    d.box("av", "Availability per group\n(optional sources)", 40, 225, 360, 56, kind="white", size=14)
    d.box("hy", "Hygiene: H2, H3, H7 before the split;\nH4 on training rows", 40, 300, 360, 80, kind="govern", size=14)
    chain(d, ["cat", "fj", "av", "hy"])
    d.group("gm", "Automated model selection", 440, 10, 420, 390, kind="agent")
    d.box("cr", "Candidate registry: dummy, logistic,\nrandom forest, histogram GB, XGBoost",
          460, 45, 380, 70, kind="agent", size=14)
    d.box("op", "Optuna tuning of finalists\n(within the run's budget)", 460, 135, 380, 56, kind="agent", size=14)
    d.box("ca", "Calibration: isotonic (500+ rows)\nor sigmoid; remedy chain", 460, 210, 380, 60, kind="agent", size=14)
    d.box("ac", "Acceptance policy AP-1 to AP-8", 460, 290, 380, 40, kind="govern", size=14)
    d.box("rg", "Registry: version, hash, model card", 460, 345, 380, 40, kind="data", size=14)
    chain(d, ["cr", "op", "ca", "ac", "rg"])
    # Up the gutter between the two groups and into the registry from the side: straight
    # across, it ran up through three of the selection boxes.
    d.edge("hy", "cr", exit="right", entry="left",
           points=[(430, d.cy("hy")), (430, d.cy("cr"))])
    return d


# --- Section 9 ------------------------------------------------------------------------------
def fig13() -> Diagram:
    d = Diagram("Fig 13 Distributed Spark Architecture", W, 470)
    ids = row(d, "a", ["Worker", "Routing rules\n(LR-07: local mode\nbelow 1 GB)", "Tool registry\nspark.submit_job",
                       "Sandbox runner\n(token, image\nallowlist)"], 30, kind="agent", h=80, size=14)
    chain(d, ids)
    # A short group title: the long one ran under the submit edge's drop into the driver.
    d.group("gc", "Job container", 20, 150, W - 40, 170, kind="compute", size=14)
    d.box("drv", "spark-submit, local mode\ndriver and executor in one JVM", 40, 195, 300, 70, kind="compute", size=14)
    d.box("job", "Generated Scala job\non the TriadJob core library", 360, 195, 260, 70, kind="compute", size=14)
    d.box("mnt", "uid 1000, no capabilities,\nread-only root, no network,\ncgroup limits; inputs and\nJARs read-only, run folder\nwritable",
          640, 180, 200, 125, kind="white", size=14)
    d.edge(ids[3], "drv", exit="bottom", entry="top",
           points=[(d.cx(ids[3]), 130), (d.cx("drv"), 130)])
    d.edge("drv", "job")
    ids3 = row(d, "c", ["Staging Parquet\n(run folder)", "Contract check\n(gate 7)", "Atomic publish\nmanifest and hash"],
               350, kind="data", h=70, size=14, x1=620)
    d.edge("job", ids3[0], exit="bottom")
    chain(d, ids3)
    d.box("cl", "Optional standalone cluster\n(master and worker)", 640, 350, 220, 70, kind="white", dashed=True, size=14)
    return d


# --- Section 10 -----------------------------------------------------------------------------
def fig14() -> Diagram:
    # Four columns, left to right: the model, the explanation methods, what is produced, and
    # the person who reads it. Every edge runs in a gutter between columns, and the model's own
    # metrics go straight through the gap left between the methods.
    d = Diagram("Fig 14 Explainability Framework", W, 400)
    d.box("m", "Selected\nmodel", 20, 170, 130, 60, kind="compute", bold=True)
    d.box("x0", "TreeSHAP\n(tree ensembles)", 190, 20, 190, 56, kind="agent", size=14)
    d.box("x1", "Coefficient × input\n(logistic regression)", 190, 95, 190, 56, kind="agent", size=14)
    # A fallback, not a third method that always runs: drawn dashed, as Section 10.1 describes it.
    d.box("x2", "Permutation importance\n(fallback, global only)", 190, 255, 190, 56, kind="agent",
          size=14, dashed=True)
    d.box("o0", "Per-customer reasons,\nmethod named", 415, 57, 210, 56, kind="data", size=14)
    d.box("o1", "Evaluation record:\ncurves, intervals, segments", 415, 172, 210, 56, kind="data", size=14)
    d.box("o2", "Model card: data, metrics,\ndrivers, limitations", 415, 255, 210, 56, kind="data", size=14)
    d.box("o3", "Claim-checked\nreport", 415, 330, 210, 50, kind="data", size=14)
    d.box("ppl", "Reviewer or\napprover", 670, 160, 190, 80, kind="person", size=14)
    d.box("dr", "Decision records: rule,\nevidence, rejected\nalternatives", 670, 290, 190, 90, kind="govern", size=14)
    for x in ("x0", "x1", "x2"):          # fan out through the gutter at x = 170
        d.edge("m", x, exit="right", entry="left", dashed=x == "x2",
               points=[(170, d.cy("m")), (170, d.cy(x))])
    for x in ("x0", "x1"):                # both per-customer methods produce the reasons
        d.edge(x, "o0", exit="right", entry="left",
               points=[(398, d.cy(x)), (398, d.cy("o0"))])
    d.edge("m", "o1", exit="right", entry="left")                # through the gap at y = 200
    d.edge("x2", "o2", exit="right", entry="left", dashed=True)
    d.edge("o1", "o2", exit="bottom", entry="top")
    d.edge("o2", "o3", exit="bottom", entry="top")
    # What the reviewer reads, each in its own lane of the right-hand gutter.
    d.edge("o0", "ppl", dashed=True, exit="right", entry=(0, 0.2),
           points=[(645, d.cy("o0")), (645, 176)])
    d.edge("o1", "ppl", dashed=True, exit="right", entry=(0, 0.5))
    d.edge("o2", "ppl", dashed=True, exit="right", entry=(0, 0.8),
           points=[(645, d.cy("o2")), (645, 224)])
    d.edge("o3", "ppl", dashed=True, exit="right", entry="bottom",
           points=[(655, d.cy("o3")), (655, 270), (d.cx("ppl"), 270)])
    d.edge("dr", "ppl", dashed=True, exit=(0.8, 0), entry=(0.8, 1))
    return d


def fig15() -> Diagram:
    d = Diagram("Fig 15 Trustworthy Analytics Model", W, 430)
    comps = [("Audit chain verifies", "chain"), ("All nine gates passed", "gates"),
             ("Calibrated within the limit", "calib"), ("Report claim check passed", "claims"),
             ("Approved by another person", "approval"),
             ("Input versions and code hashes recorded", "lineage")]   # as trust_score.py reads it
    for i, (label, key) in enumerate(comps):
        y = 20 + i * 64
        # The symbols set as equation (6) sets them: italic s, roman subscript.
        d.box(f"c{i}", f"{label}\n<i>s</i><sub>{key}</sub>", 20, y, 300, 54, kind="govern",
              size=14, raw=True)
    d.box("ts", "Trust score, equation (6)\nTS = mean of the components\nthat apply to the run",
          420, 150, 240, 110, kind="agent", bold=True, size=15)
    for i in range(len(comps)):
        d.edge(f"c{i}", "ts")
    # No hard breaks: in a box this narrow they combined with wrapping to strand single words.
    d.box("note", "Equal weights: a stated design choice, not a validated result. Fairness is "
          "reported per segment, not folded into the score.", 690, 140, 170, 130, kind="white", size=13)
    return d


# --- Section 11 -----------------------------------------------------------------------------
def fig16() -> Diagram:
    d = Diagram("Fig 16 Agent Orchestration Workflow", W, 490)
    happy = row(d, "s", ["RECEIVED", "PROFILED", "PLANNED", "FEATURES_READY", "TRAINING", "EVALUATED"],
                40, kind="agent", h=56, size=12, gap=20)   # FEATURES_READY on one line
    chain(d, happy)
    # A 60-unit gap: at 32 the "approved" label covered its edge and arrowhead entirely.
    end = row(d, "e", ["APPROVAL_REQUIRED\n(waits for a person)", "PUBLISHED", "SELECTED\n(champion kept)"],
              170, kind="govern", h=64, size=13, x0=180, x1=W - 20, gap=60)
    ev = happy[5]
    # Two exits from EVALUATED, each on its own line: "keep" drops straight into SELECTED, and
    # "propose" turns left in the gap above the outcome row.
    d.edge(ev, end[2], "keep", exit=(d.at(ev, d.cx(end[2])), 1), entry="top")
    d.edge(ev, end[0], "propose", exit=(0.05, 1), entry="top",
           points=[(d.geom[ev][0] + 0.05 * d.geom[ev][2], 132), (d.cx(end[0]), 132)])
    d.edge(end[0], end[1], "approved")
    fail = row(d, "f", ["REJECTED_INPUT", "ESCALATED\n(resumable)", "FAILED", "CANCELLED"],
               330, kind="alert", h=60, size=13)
    d.edge(happy[0], fail[0], "contract refused", dashed=True,
           exit=(d.at(happy[0], d.cx(happy[0])), 1), entry=(d.at(fail[0], d.cx(happy[0])), 0))
    # Escalation, failure, and cancellation can happen from any state, not from one; edges
    # from a single state said otherwise.
    d.text("any", "From any state: ESCALATED (resumable by a person), FAILED, or CANCELLED.",
           240, 280, W - 260, 30, size=13)
    d.text("t", "Every transition is committed with its audit entry; a dead worker's lease expires and the task is re-queued.",
           20, 420, W - 40, 40, italic=True, size=14)
    return d


def fig17() -> Diagram:
    d = Diagram("Fig 17 Autonomous Decision Lifecycle", W, 410)
    pos = [("o", "Observe\na measured signal", 60, 40), ("c", "Classify\nfixed taxonomy", 330, 40),
           ("d", "Decide\nfirst matching rule", 600, 40), ("r", "Record\nrule, evidence,\nalternatives", 600, 200),
           ("a", "Act\nwithin bounds", 330, 200), ("v", "Verify\nagainst a contract", 60, 200)]
    for cid, label, x, y in pos:
        d.box(cid, label, x, y, 210, 76, kind="agent", size=14)
    chain(d, ["o", "c", "d", "r", "a", "v"])
    d.edge("v", "o", "next signal")
    # Far enough below "Act" for the dashed line to show on both sides of its label.
    d.box("e", "Escalate to a person\nwith the evidence", 330, 340, 210, 56, kind="alert", size=14)
    d.edge("a", "e", "bounds or budget exhausted", dashed=True, exit="bottom")
    return d


# --- Section 12 -----------------------------------------------------------------------------
def fig18() -> Diagram:
    d = Diagram("Fig 18 Security and Governance Architecture", W, 500)
    ids = row(d, "u", ["People", "Authentication\nArgon2id, signed tokens", "Authorization\nsix roles, every endpoint",
                       "Policy engine\n23 actions, fail-closed", "Approvals\nseparation of duties"],
              30, kind="govern", h=76, size=14)
    chain(d, ids)
    ids2 = row(d, "w", ["Worker", "Tool registry\nallowlist, schema", "Sandbox runner\nsocket, token,\nimage allowlist",
                        "Job container\nno network, uid 1000,\nread-only, limits"], 170, kind="compute", h=80, size=14)
    chain(d, ids2)
    # Down into the gap between the rows and along it: routed automatically it ran through
    # the authentication and authorization boxes.
    d.edge(ids[3], ids2[1], "governed action", dashed=True, exit="bottom", entry="top",
           points=[(d.cx(ids[3]), 138), (d.cx(ids2[1]), 138)])
    row(d, "x", ["Untrusted content:\nfenced and scanned", "Generated code:\nslots and nine gates",
                 "Model integrity:\nhash verified on load", "Secrets: none in source,\nlogs, or job environment"],
        310, kind="white", h=70, size=14)
    d.box("au", "Audit trail: one hash chain per run, append-only trigger, verifier; refusals committed",
          20, 420, W - 40, 50, kind="govern", size=14)
    return d


# --- Sections 13 and 14 --------------------------------------------------------------------
def fig19() -> Diagram:
    d = Diagram("Fig 19 Experimental Architecture", W, 430)
    d.box("hw", "One workstation: 8 logical cores, 10.96 GB memory,\n5.27 GB for containers; one pinned toolchain image",
          20, 10, W - 40, 56, kind="neutral", size=14)
    ids = row(d, "a", ["Seeded synthetic\ngenerator", "Fault and drift\ninjection", "Configurations:\nP (TRIAD), B2, B3"],
              100, kind="data", h=70, size=14)
    chain(d, ids)
    d.box("h", "Experiment harness: timers, JVM start-up measured apart,\npeak memory per process, environment record",
          20, 210, W - 40, 60, kind="agent", size=14)
    d.edge(ids[2], "h", exit="bottom")
    # The policy noise floors are simulations with no model and no harness (Section 15), so
    # they are not drawn as coming through it.
    ids2 = row(d, "b", ["EXP-1 to EXP-10", "Stored outputs:\nresult.json per run",
                        "Charts and tables\ngenerated from outputs"], 310, kind="compute", h=70, size=14)
    d.edge("h", ids2[0], exit="bottom")
    chain(d, ids2)
    return d


def fig20() -> Diagram:
    d = Diagram("Fig 20 Evaluation Framework", W, 400)
    cats = [("Predictive quality", "PR-AUC, ROC-AUC, recall"), ("Calibration", "ECE (8), Brier skill (9)"),
            ("Comparison", "paired bootstrap (10)"), ("Fairness", "segment recall gap"),
            ("Performance", "wall clock, JVM start-up"), ("Resources", "peak memory per process"),
            ("Automation and operations", "lines, steps, faults handled;\napprovals, escalations, decisions"),
            ("Reliability", "gates, recovery, replay"),
            ("Trust and explainability", "trust score, equation (6);\nreason coverage, claim check")]
    for i, (cat, inst) in enumerate(cats):
        x = 20 + (i % 3) * 287
        y = 20 + (i // 3) * 120
        kind = ["compute", "agent", "govern"][i // 3]
        d.box(f"k{i}", f"{cat}\n{inst}", x, y, 266, 90, kind=kind, size=15)
    d.text("t", "Reported only if measured by a named instrument and stored; otherwise reported as not measured.",
           20, 370, W - 40, 26, italic=True, size=14)
    return d


# --- Section 18 (conceptual) ------------------------------------------------------------------
def fig26() -> Diagram:
    d = Diagram("Fig 26 Smart Manufacturing Analytics Ecosystem", W, 430)
    # The disclaimer goes under the figure: at the top it read as an in-image title.
    d.text("c", "Conceptual transfer analysis - not implemented or measured", 20, 392, W - 40, 26, italic=True, size=14)
    src = row(d, "s", ["Machine sensors\n(streaming)", "Maintenance\nrecords", "Quality\ninspections"],
              45, kind="data", h=64, size=14, dashed=True)
    d.box("ing", "Governed ingestion: contracts, profiling, quarantine (reused)", 20, 140, W - 40, 50, kind="govern", size=14)
    for cid in src:
        d.edge(cid, "ing")
    mid = row(d, "m", ["Time-series feature\ntemplates (new)", "Point-in-time rule:\nno data after failure (reused)",
                       "Failure prediction\nand calibration (reused)"], 220, kind="compute", h=70, size=14)
    d.edge("ing", mid[0])
    chain(d, mid)
    d.box("app", "Maintenance planner approves interventions (separation of duties, reused)",
          20, 330, W - 40, 50, kind="person", size=14)
    d.edge(mid[2], "app")
    return d


def fig27() -> Diagram:
    d = Diagram("Fig 27 Healthcare Intelligence Platform", W, 430)
    # The disclaimer goes under the figure: at the top it read as an in-image title.
    d.text("c", "Conceptual transfer analysis - not implemented or measured", 20, 392, W - 40, 26, italic=True, size=14)
    src = row(d, "s", ["Clinical records\n(de-identified, new)", "Scheduling and\noperations data", "Laboratory\nresults"],
              45, kind="data", h=64, size=14, dashed=True)
    d.box("ing", "Governed ingestion and profiling; transport encryption required (new)", 20, 140, W - 40, 50, kind="govern", size=14)
    for cid in src:
        d.edge(cid, "ing")
    mid = row(d, "m", ["Readmission or no-show\nfeatures (new catalogue)", "Fairness groups chosen\nwith clinicians (new)",
                       "Calibrated risk with\nnamed explanations (reused)"], 220, kind="compute", h=70, size=14)
    d.edge("ing", mid[0])
    chain(d, mid)
    d.box("app", "Clinician review; sandbox, audit, no-deletion policy (reused)", 20, 330, W - 40, 50, kind="person", size=14)
    d.edge(mid[2], "app")
    return d


# --- Sections 20 to 22 ------------------------------------------------------------------------
def fig29() -> Diagram:
    d = Diagram("Fig 29 Challenges and Limitations Roadmap", W, 380)
    cols = [("Engineering (known fixes)", ["Transport encryption", "Rootless container engine",
                                            "Faster first gate-5 run", "Relational and streaming sources"]),
            ("Policy (needs a decision)", ["Interval-based acceptance rules", "Override path for noisy checks",
                                            "Review load at small data", "Weights of the trust score"]),
            ("Research (needs study)", ["LLM generation inside gates", "Spark tuning from history",
                                         "Tiers above 10 GB", "Real retail data"])]
    for ci, (title, items) in enumerate(cols):
        x = 20 + ci * 287
        d.group(f"g{ci}", title, x, 10, 266, 350, kind=["compute", "govern", "agent"][ci])
        for ii, item in enumerate(items):
            d.box(f"i{ci}{ii}", item, x + 14, 50 + ii * 74, 238, 58, kind="white", size=14)
    d.edge("g0", "g1")
    d.edge("g1", "g2")
    return d


def fig30() -> Diagram:
    d = Diagram("Fig 30 Future Research Roadmap", W, 330)
    ids = row(d, "r", ["Statistically aware\ngovernance", "LLM-assisted\nprogramming in gates", "Self-evolving\npipelines",
                       "Federated data\nscience", "Cognitive\nprogramming,\nhuman-agent loops"],
              80, kind="agent", h=90, size=14)
    chain(d, ids)
    # A time axis, as an arrow between its two ends; spaces in one label collapsed to one.
    d.text("near", "nearer term", 20, 200, 110, 30, align="left", size=13)
    d.text("far", "longer term", W - 130, 200, 110, 30, align="right", size=13)
    d.edge("near", "far", exit="right", entry="left")
    d.text("t", "Each direction starts from a limitation measured or stated in this report.", 20, 260, W - 40, 30, italic=True, size=14)
    return d


def fig31() -> Diagram:
    d = Diagram("Fig 31 Next-Generation Agentic Analytics Ecosystem", W, 430)
    d.text("c", "Proposed - not implemented", 20, 385, W - 40, 26, italic=True, size=14)
    d.box("llm", "LLM agents propose\nslot code, plans, narratives", 20, 50, 260, 70, kind="agent", dashed=True, size=14)
    d.box("pol", "Interval-based policies decide\n(error rates stated)", 310, 50, 260, 70, kind="govern", dashed=True, size=14)
    d.box("tun", "Tuning loop adapts\nexecution from history", 600, 50, 260, 70, kind="compute", dashed=True, size=14)
    d.box("core", "Governed core (exists today): gates, sandbox, routing, approvals, audit, replay",
          20, 170, W - 40, 60, kind="neutral", bold=True, size=15)
    for cid in ("llm", "pol", "tun"):
        d.edge(cid, "core")
    d.box("fed", "Federated participants\nshare decisions, not data", 20, 290, 400, 70, kind="data", dashed=True, size=14)
    d.box("hum", "People's decisions feed back\ninto the rules they apply", 460, 290, 400, 70, kind="person", dashed=True, size=14)
    # Separate points on the core's lower edge: sharing one, the outgoing and incoming edges
    # read as a single line with arrows at both ends.
    d.edge("core", "fed", dashed=True, exit=(d.at("core", d.cx("fed")), 1), entry="top")
    d.edge("hum", "core", dashed=True, exit="top", entry=(d.at("core", d.cx("hum")), 1))
    return d


def fig32() -> Diagram:
    d = Diagram("Fig 32 Research Contribution Framework", W, 400)
    contribs = row(d, "c", ["Bounded agent\nautonomy", "Template-constrained\ncode generation", "Point-in-time\nfeature engineering",
                            "Evidence-first\nevaluation", "Honest evaluation\nof limits"], 30, kind="agent", h=80, size=14)
    evid = row(d, "e", ["Sections V, VI, XI", "Sections VI, VII, IX, XII", "Sections VII, VIII",
                        "Sections X, XIV, XVII", "Sections XIII, XV, XX"], 150, kind="white", h=50, size=14)
    for a, b in zip(contribs, evid, strict=True):
        d.edge(a, b)
    d.box("goal", "Automate the engineering between decision points; keep the decisions with rules and people",
          20, 260, W - 40, 60, kind="govern", bold=True, size=15)
    for b in evid:
        d.edge(b, "goal")
    return d


DIAGRAMS = [fig01, fig02, fig03, fig04, fig05, fig06, fig07, fig08, fig09, fig10, fig11, fig12,
            fig13, fig14, fig15, fig16, fig17, fig18, fig19, fig20, fig26, fig27, fig29, fig30,
            fig31, fig32]


if __name__ == "__main__":
    for build in DIAGRAMS:
        print("wrote", build().save(OUT).name)
