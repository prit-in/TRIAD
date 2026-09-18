"""Print the resolved version of every pinned component, from inside the image.

Evidence for docs/implementation/ENVIRONMENT_STATUS.md and configs/versions.yaml: the
manifest is generated from what is actually installed, never typed by hand.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from importlib import metadata


def _cmd(args: list[str], pattern: str | None = None) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=180)
        text = (out.stdout or "") + (out.stderr or "")
    except Exception as exc:  # noqa: BLE001 - reporting tool, never fatal
        return f"ERROR: {exc}"
    if pattern:
        m = re.search(pattern, text)
        return m.group(1) if m else text.strip().splitlines()[0] if text.strip() else "unknown"
    return text.strip().splitlines()[0] if text.strip() else "unknown"


def _dist(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"


def collect() -> dict[str, object]:
    spark_home = os.environ.get("SPARK_HOME", "/opt/spark")
    java_home = os.environ.get("JAVA_HOME", "")
    java_bin = f"{java_home}/bin/java" if java_home else "java"

    scala_jar = ""
    jars_dir = os.path.join(spark_home, "jars")
    if os.path.isdir(jars_dir):
        for f in sorted(os.listdir(jars_dir)):
            if f.startswith("scala-library-"):
                scala_jar = f.replace("scala-library-", "").replace(".jar", "")
                break

    spark_core = ""
    if os.path.isdir(jars_dir):
        for f in sorted(os.listdir(jars_dir)):
            m = re.match(r"spark-core_(\d+\.\d+)-(\d+\.\d+\.\d+)\.jar", f)
            if m:
                spark_core = f"{m.group(2)} (scala {m.group(1)})"
                break

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "java": _cmd([java_bin, "-version"], r'version "([^"]+)"'),
        "java_vendor": _cmd([java_bin, "-version"], r"(Temurin[-\w.+]*)"),
        "spark_home": spark_home,
        "spark_dist": spark_core or "unknown",
        "scala_library": scala_jar or "unknown",
        "sbt": _cmd([os.environ.get("SBT_HOME", "/opt/sbt") + "/bin/sbt", "--script-version"]),
        "packages": {
            name: _dist(name)
            for name in [
                "pyspark",
                "pandas",
                "pyarrow",
                "numpy",
                "scipy",
                "scikit-learn",
                "xgboost",
                "optuna",
                "shap",
                "mlflow",
                "fastapi",
                "pydantic",
                "sqlalchemy",
                "alembic",
                "uvicorn",
                "psycopg",
                "langgraph",
                "langgraph-checkpoint-postgres",
                "jinja2",
                "matplotlib",
                "pytest",
                "hypothesis",
                "ruff",
                "bandit",
                "pip-audit",
                "detect-secrets",
                "jsonschema",
                "prometheus-client",
                "structlog",
            ]
        },
    }


if __name__ == "__main__":
    data = collect()
    json.dump(data, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
