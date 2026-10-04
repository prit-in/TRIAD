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
        x0: int = 20, x1: int = W - 20, h: int = 56, gap: int = 14, size: int = 16,
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
    ids = row(d, "s", ["Single-machine scientific stack\n(Python, NumPy, SciPy)",
                       "Distributed engines\n(MapReduce, Spark, SQL)",
                       "Machine learning operations\n(pipelines, registries, monitoring)",
                       "Governed agentic data science\n(agents propose, rules decide)"],
              60, kind="white", h=80)
    chain(d, ids, labels=["scale", "operations", "automation"])
    row(d, "b", ["Bottleneck: one machine's memory", "Bottleneck: people and pipelines",
                 "Bottleneck: laborious discipline", "Open question: what may an agent decide?"],
        170, kind="neutral", h=56, size=14)
    d.text("t", "Each stage moves the binding constraint; none removes the earlier stages.",
           20, 250, W - 40, 30, italic=True, size=14)
    return d


def fig02() -> Diagram:
    d = Diagram("Fig 02 Agentic Analytics Ecosystem Overview", W, 430)
    d.group("ga", "Agents (propose and compose)", 20, 10, W - 40, 80, kind="agent")
    row(d, "a", ["Discovery", "Profiling", "Cleaning", "Spark planning", "Code generation",
                 "Model selection", "Reporting"], 40, kind="agent", h=40, size=14, x0=30, x1=W - 30)
    flow = row(d, "f", ["Versioned\nsources", "Governed\ningestion", "Generated Scala\njobs in sandbox",
                        "Point-in-time\nfeatures, labels", "Python models:\ntrain, calibrate",
                        "Approval by\na second person", "Champion and\nscored customers"],
               150, kind="compute", h=80, size=14)
    chain(d, flow)
    d.group("gg", "Governance (decides)", 20, 270, W - 40, 140, kind="govern")
    row(d, "g", ["Routing rules\nLR-01 to LR-10", "Nine validation\ngates", "Acceptance policy\nAP-1 to AP-8",
                 "Separation\nof duties", "Hash-chained\naudit per run"],
        310, kind="govern", h=70, size=14, x0=30, x1=W - 30)
    d.edge("a4", "f2", "slot code", dashed=True)
    d.edge("g1", "f2", "", dashed=True)
    d.edge("g2", "f4", "", dashed=True)
    d.edge("g3", "f5", "", dashed=True)
    return d


# --- Sections 2 to 4 --------------------------------------------------------------------------
def fig03() -> Diagram:
    d = Diagram("Fig 03 Challenges in Modern Data Science Platforms", W, 400)
    d.box("c", "Modern data\nscience platform", 350, 160, 180, 80, kind="neutral", bold=True)
    items = [
        ("w", "Workflow complexity\nfive components, three languages", 20, 20),
        ("m", "Code maintenance\nhand-written and generated jobs", 600, 20),
        ("s", "Scalability\nwhich engine, and who decides", 20, 300),
        ("h", "Heterogeneous environments\nversions across runtimes", 600, 300),
        ("a", "Automation without accountability\nwho decided a model is fit to use?", 310, 300),
    ]
    for cid, label, x, y in items:
        d.box(cid, label, x, y, 260, 76, kind="alert" if cid == "a" else "white", size=14)
        d.edge("c", cid)
    return d


def fig04() -> Diagram:
    d = Diagram("Fig 04 Literature Taxonomy", W, 420)
    d.box("r", "Agentic data science programming", 290, 10, 300, 50, kind="neutral", bold=True)
    top = row(d, "a", ["Python analytics", "Scala, Spark, distributed processing",
                       "Configuration tuning", "MLOps and engineering of ML systems"],
              120, kind="compute", h=70, size=14)
    bottom = row(d, "b", ["Data quality and provenance", "AutoML and hyperparameter optimization",
                          "LLM agents and generated code", "Explainability, trust, and fairness"],
                 230, kind="agent", h=70, size=14)
    for cid in top + bottom:
        d.edge("r", cid)
    d.box("app", "Application: customer churn prediction", 290, 340, 300, 50, kind="data", size=14)
    for cid in bottom:
        d.edge(cid, "app", dashed=True)
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
    d.edge(ids[3], "au", "", exit="bottom")
    d.edge("au", ids[2], "corrects", exit="top")
    d.text("t", "Every requirement maps to a test, and every test to a stored result.",
           20, 310, W - 40, 30, italic=True, size=14)
    return d


# --- Section 5 ------------------------------------------------------------------------------
def fig06() -> Diagram:
    d = Diagram("Fig 06 Agentic Data Science Programming Architecture Integrating Python Scala and Apache Spark", W, 620)
    d.group("gp", "Presentation and service", 20, 10, 690, 90, kind="person")
    d.box("ui", "Dashboard\nReact, 11 pages", 40, 40, 200, 50, kind="person", size=14)
    d.box("api", "API (Python, FastAPI)\n41 endpoints, roles", 260, 40, 220, 50, kind="person", size=14)
    d.box("ppl", "People: engineers,\napprovers, analysts", 500, 40, 190, 50, kind="person", size=14)
    d.group("go", "Orchestration (Python)", 20, 115, 690, 150, kind="agent")
    d.box("wk", "Worker\nstate machine, queue, leases", 40, 150, 210, 56, kind="agent", size=14)
    d.box("sc", "Scheduler\nRT-1 to RT-9, landing folders", 270, 150, 200, 56, kind="agent", size=14)
    d.box("rt", "Routing rules\nLR-01 to LR-10", 490, 150, 200, 56, kind="agent", size=14)
    d.box("cg", "Code generation\ntemplates, 9 gates", 40, 215, 210, 40, kind="agent", size=14)
    d.box("tr", "Tool registry: allowlist, schema, policy", 270, 215, 420, 40, kind="agent", size=14)
    d.group("gc", "Compute", 20, 280, 690, 160, kind="compute")
    d.box("py", "Python ML\nscikit-learn, XGBoost, Optuna\ncalibration, SHAP", 40, 315, 210, 110, kind="compute", size=14)
    d.box("sr", "Sandbox runner\nonly holder of the\ncontainer socket", 270, 315, 160, 110, kind="govern", size=14)
    d.box("sp", "Scala jobs on Apache Spark\nCleanJob, FeatureJob, LabelJob\none container each, no network",
          450, 315, 240, 110, kind="compute", size=14)
    d.group("gs", "State", 20, 455, 690, 140, kind="data")
    d.box("pg", "PostgreSQL\n21 tables: runs, decisions,\napprovals, audit, lineage", 40, 490, 220, 90, kind="data", shape="cylinder", size=14)
    d.box("fs", "Versioned Parquet\nraw, clean, features,\nlabels, quarantine", 280, 490, 200, 90, kind="data", shape="cylinder", size=14)
    d.box("ml", "MLflow\nexperiments,\nmodel registry", 500, 490, 190, 90, kind="data", shape="cylinder", size=14)
    d.group("gg", "Governance", 725, 10, 135, 585, kind="govern")
    d.box("pe", "Policy engine\n23 actions,\nfail-closed", 735, 50, 115, 90, kind="govern", size=14)
    d.box("ap", "Approvals\nseparation\nof duties", 735, 200, 115, 90, kind="govern", size=14)
    d.box("au", "Audit\nhash chain\nper run", 735, 360, 115, 90, kind="govern", size=14)
    d.edge("api", "wk", "queue")
    d.edge("wk", "rt")
    d.edge("cg", "tr")
    d.edge("tr", "sr", "submit")
    d.edge("sr", "sp")
    d.edge("sp", "fs", "staging, then publish")
    d.edge("py", "ml")
    d.edge("wk", "pg", dashed=True)
    d.edge("tr", "pe", "", dashed=True)
    d.edge("ap", "api", "", dashed=True)
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
    d.box("reg", "Tool registry: permitted agents, argument schema, policy action, audit entry",
          20, 350, W - 40, 44, kind="govern", size=14)
    d.box("gates", "Nine validation gates", 20, 420, 260, 50, kind="govern", size=14)
    d.box("sbx", "Sandbox runner and containers", 300, 420, 280, 50, kind="compute", size=14)
    d.box("llm", "LLM provider (optional;\nnot configured in this evaluation)", 600, 420, 260, 50,
          kind="white", dashed=True, size=13)
    d.edge("reg", "gates")
    d.edge("gates", "sbx")
    d.edge("llm", "a11", "slot bodies", dashed=True)
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
    d.box("esc", "Escalation\nwith evidence", 20, 360, 150, 60, kind="alert", size=14)
    d.edge(mid[0], "esc", "refused", dashed=True, exit="bottom")
    return d


# --- Section 7 ------------------------------------------------------------------------------
def fig10() -> Diagram:
    d = Diagram("Fig 10 Autonomous Data Engineering Pipeline", W, 420)
    ids = row(d, "a", ["Landing folder\nor upload", "Register version\ncontract check", "Write-once raw\nchecksum",
                       "Profile\nquality score Q", "Cleaning job\n(library rules)"],
              30, kind="data", h=70, size=14)
    chain(d, ids)
    ids2 = row(d, "b", ["Policy check:\nrows affected", "Publish clean\n(atomic, manifest)", "Customer-day\naggregate",
                        "Snapshot expansion\n(days before T)", "Features and\nmatured labels"],
               190, kind="compute", h=70, size=14)
    d.edge(ids[4], ids2[0], exit="bottom", entry="top")
    chain(d, ids2)
    d.box("q", "Quarantine with reason codes\n(nothing is deleted)", 20, 330, 260, 60, kind="alert", size=14)
    d.box("acc", "Row accounting:\nin = out + quarantined + duplicates", 300, 330, 300, 60, kind="govern", size=14)
    d.box("ref", "Refused at ingestion:\nmissing required column", 620, 330, 240, 60, kind="alert", size=14)
    d.edge(ids[4], "q", dashed=True, exit="bottom")
    d.edge(ids[4], "acc", dashed=True, exit="bottom")
    d.edge(ids[1], "ref", dashed=True, exit="bottom")
    return d


# --- Section 8 ------------------------------------------------------------------------------
def fig11() -> Diagram:
    d = Diagram("Fig 11 Intelligent Analytics Workflow", W, 420)
    ids = row(d, "a", ["Features and\nlabels", "Hygiene on names\nH2, H3, H7", "Temporal split\nwith 90-day gaps",
                       "Hygiene on training\nrows: H4", "Five candidate\nfamilies"], 30, kind="compute", h=70, size=14)
    chain(d, ids)
    ids2 = row(d, "b", ["Stage A: screen\n(incl. leakage alarm)", "Stage B: tune\nfinalists (Optuna)",
                        "Stage C: calibrate,\nAP-4 to AP-6", "AP-8: beat the\nchampion?", "Stage D: score the\ntest set once"],
               190, kind="agent", h=70, size=14)
    d.edge(ids[4], ids2[0], exit="bottom", entry="top")
    chain(d, ids2)
    row(d, "c", ["Promote: request approval", "Keep the champion", "Escalate with a reason code"],
        330, kind="govern", h=56, size=14)
    d.edge(ids2[4], "c0", dashed=True, exit="bottom")
    d.edge(ids2[3], "c1", dashed=True, exit="bottom")
    d.edge(ids2[2], "c2", dashed=True, exit="bottom")
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
    d.edge("hy", "cr", "training set")
    return d


# --- Section 9 ------------------------------------------------------------------------------
def fig13() -> Diagram:
    d = Diagram("Fig 13 Distributed Spark Architecture", W, 470)
    ids = row(d, "a", ["Worker", "Routing rules\n(LR-07: local mode\nbelow 1 GB)", "Tool registry\nspark.submit_job",
                       "Sandbox runner\n(token, image\nallowlist)"], 30, kind="agent", h=80, size=14)
    chain(d, ids)
    d.group("gc", "Job container: uid 1000, no capabilities, read-only root, no network, cgroup limits",
            20, 150, W - 40, 170, kind="compute", size=14)
    d.box("drv", "spark-submit, local mode\ndriver and executor in one JVM", 40, 195, 300, 70, kind="compute", size=14)
    d.box("job", "Generated Scala job\non the TriadJob core library", 360, 195, 260, 70, kind="compute", size=14)
    d.box("mnt", "Mounts: inputs and JARs\nread-only; run folder\nwritable", 640, 190, 200, 110, kind="white", size=14)
    d.edge(ids[3], "drv", exit="bottom")
    d.edge("drv", "job")
    ids3 = row(d, "c", ["Staging Parquet\n(run folder)", "Contract check\n(gate 7)", "Atomic publish\nmanifest and hash"],
               350, kind="data", h=70, size=14, x1=620)
    d.edge("job", ids3[0], exit="bottom")
    chain(d, ids3)
    d.box("cl", "Optional standalone cluster\n(master and worker)", 640, 350, 220, 70, kind="white", dashed=True, size=14)
    return d


# --- Section 10 -----------------------------------------------------------------------------
def fig14() -> Diagram:
    d = Diagram("Fig 14 Explainability Framework", W, 400)
    d.box("m", "Selected model", 20, 160, 150, 60, kind="compute", bold=True)
    meth = row(d, "x", ["TreeSHAP\n(tree ensembles)", "Coefficient x input\n(logistic regression)",
                        "Permutation importance\n(global only)"], 40, kind="agent", h=64, size=14, x0=200, x1=640)
    for cid in meth:
        d.edge("m", cid)
    outs = row(d, "o", ["Per-customer reasons,\nmethod named", "Evaluation record:\ncurves, intervals, segments",
                        "Model card", "Claim-checked\nreport"], 170, kind="data", h=70, size=14, x0=200, x1=W - 20)
    d.edge(meth[0], outs[0])
    d.edge(meth[1], outs[0])
    d.edge("m", outs[1])
    d.box("dr", "Decision records: rule, evidence, rejected alternatives", 200, 300, 420, 50, kind="govern", size=14)
    d.box("ppl", "Reviewer or\napprover", 660, 290, 200, 70, kind="person", size=14)
    for cid in outs:
        d.edge(cid, "ppl", dashed=True)
    d.edge("dr", "ppl", dashed=True)
    return d


def fig15() -> Diagram:
    d = Diagram("Fig 15 Trustworthy Analytics Model", W, 430)
    comps = [("Audit chain verifies", "chain"), ("All nine gates passed", "gates"),
             ("Calibrated within the limit", "calib"), ("Report claim check passed", "claims"),
             ("Approved by another person", "approval"), ("Inputs and outputs traceable", "lineage")]
    for i, (label, key) in enumerate(comps):
        y = 20 + i * 64
        d.box(f"c{i}", f"{label}\ns_{key}", 20, y, 300, 54, kind="govern", size=14)
    d.box("ts", "Trust score (6)\nTS = mean of the components\nthat apply to the run",
          420, 150, 240, 110, kind="agent", bold=True, size=15)
    for i in range(len(comps)):
        d.edge(f"c{i}", "ts")
    d.box("note", "Equal weights: a stated design choice,\nnot a validated result.\nFairness is reported per segment,\nnot folded into the score.",
          690, 140, 170, 130, kind="white", size=13)
    return d


# --- Section 11 -----------------------------------------------------------------------------
def fig16() -> Diagram:
    d = Diagram("Fig 16 Agent Orchestration Workflow", W, 470)
    happy = row(d, "s", ["RECEIVED", "PROFILED", "PLANNED", "FEATURES_\nREADY", "TRAINING", "EVALUATED"],
                40, kind="agent", h=56, size=14)
    chain(d, happy)
    end = row(d, "e", ["APPROVAL_REQUIRED\n(waits for a person)", "PUBLISHED", "SELECTED\n(champion kept)"],
              170, kind="govern", h=64, size=14, x0=300, x1=W - 20)
    d.edge(happy[5], end[0], exit="bottom")
    d.edge(end[0], end[1], "approved")
    d.edge(happy[5], end[2], "keep", exit="bottom")
    fail = row(d, "f", ["REJECTED_INPUT", "ESCALATED\n(resumable)", "FAILED", "CANCELLED"],
               320, kind="alert", h=60, size=14)
    d.edge(happy[0], fail[0], dashed=True, exit="bottom")
    d.edge(happy[2], fail[1], dashed=True, exit="bottom")
    d.edge(fail[1], happy[2], "resume", dashed=True)
    d.text("t", "Every transition is committed with its audit entry; a dead worker's lease expires and the task is re-queued.",
           20, 410, W - 40, 40, italic=True, size=14)
    return d


def fig17() -> Diagram:
    d = Diagram("Fig 17 Autonomous Decision Lifecycle", W, 380)
    pos = [("o", "Observe\na measured signal", 60, 40), ("c", "Classify\nfixed taxonomy", 330, 40),
           ("d", "Decide\nfirst matching rule", 600, 40), ("r", "Record\nrule, evidence,\nalternatives", 600, 200),
           ("a", "Act\nwithin bounds", 330, 200), ("v", "Verify\nagainst a contract", 60, 200)]
    for cid, label, x, y in pos:
        d.box(cid, label, x, y, 210, 76, kind="agent", size=14)
    chain(d, ["o", "c", "d", "r", "a", "v"])
    d.edge("v", "o", "next signal")
    d.box("e", "Escalate to a person\nwith the evidence", 330, 310, 210, 56, kind="alert", size=14)
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
    d.edge(ids[3], ids2[1], "governed action", dashed=True)
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
    ids2 = row(d, "b", ["EXP-1 to EXP-10\nand policy noise floors", "Stored outputs:\nresult.json per run",
                        "Charts and tables\ngenerated from outputs"], 310, kind="compute", h=70, size=14)
    d.edge("h", ids2[0], exit="bottom")
    chain(d, ids2)
    return d


def fig20() -> Diagram:
    d = Diagram("Fig 20 Evaluation Framework", W, 400)
    cats = [("Predictive quality", "PR-AUC, ROC-AUC, recall"), ("Calibration", "ECE (8), Brier skill (9)"),
            ("Comparison", "paired bootstrap (10)"), ("Fairness", "segment recall gap"),
            ("Performance", "wall clock, JVM start-up"), ("Resources", "peak memory per process"),
            ("Automation", "lines, steps, faults handled"), ("Reliability", "gates, recovery, replay"),
            ("Trust", "trust score (6) components")]
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
    d = Diagram("Fig 26 Smart Manufacturing Analytics Ecosystem", W, 400)
    d.text("c", "Conceptual transfer analysis - not implemented or measured", 20, 5, W - 40, 26, italic=True, size=14)
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
    d = Diagram("Fig 27 Healthcare Intelligence Platform", W, 400)
    d.text("c", "Conceptual transfer analysis - not implemented or measured", 20, 5, W - 40, 26, italic=True, size=14)
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
                       "Federated data\nscience", "Cognitive programming,\nhuman-agent loops"],
              80, kind="agent", h=90, size=14)
    chain(d, ids)
    d.box("ax", "nearer term                                                                                       longer term",
          20, 200, W - 40, 30, kind="white", size=13)
    d.text("t", "Each direction starts from a limitation measured or stated in this report.", 20, 260, W - 40, 30, italic=True, size=14)
    return d


def fig31() -> Diagram:
    d = Diagram("Fig 31 Next-Generation Agentic Analytics Ecosystem", W, 430)
    d.text("c", "Proposed - not implemented", 20, 5, W - 40, 26, italic=True, size=14)
    d.box("llm", "LLM agents propose\nslot code, plans, narratives", 20, 50, 260, 70, kind="agent", dashed=True, size=14)
    d.box("pol", "Interval-based policies decide\n(error rates stated)", 310, 50, 260, 70, kind="govern", dashed=True, size=14)
    d.box("tun", "Tuning loop adapts\nexecution from history", 600, 50, 260, 70, kind="compute", dashed=True, size=14)
    d.box("core", "Governed core (exists today): gates, sandbox, routing, approvals, audit, replay",
          20, 170, W - 40, 60, kind="neutral", bold=True, size=15)
    for cid in ("llm", "pol", "tun"):
        d.edge(cid, "core")
    d.box("fed", "Federated participants\nshare decisions, not data", 20, 290, 400, 70, kind="data", dashed=True, size=14)
    d.box("hum", "People's decisions feed back\ninto the rules they apply", 460, 290, 400, 70, kind="person", dashed=True, size=14)
    d.edge("core", "fed", dashed=True)
    d.edge("hum", "core", dashed=True)
    return d


def fig32() -> Diagram:
    d = Diagram("Fig 32 Research Contribution Framework", W, 400)
    contribs = row(d, "c", ["Bounded agent\nautonomy", "Template-constrained\ncode generation", "Point-in-time\nfeature engineering",
                            "Evidence-first\nevaluation", "Honest evaluation\nof limits"], 30, kind="agent", h=80, size=14)
    evid = row(d, "e", ["Sections 5, 6, 11", "Sections 6, 7, 9, 12", "Sections 7, 8", "Sections 10, 14, 17",
                        "Sections 13, 15, 20"], 150, kind="white", h=50, size=14)
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
