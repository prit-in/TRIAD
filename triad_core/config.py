"""Configuration for every TRIAD process.

One versioned configuration tree, validated at start-up, hashed into each run
(PRD section 10.2). Secrets come from the environment only; nothing is defaulted to a real
credential.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Process settings. Every field is overridable by a TRIAD_* environment variable."""

    model_config = SettingsConfigDict(env_prefix="TRIAD_", extra="ignore")

    env: str = "dev"
    data_root: Path = Path("/data/triad")
    repo_root: Path = REPO_ROOT

    # Database ---------------------------------------------------------------
    db_url: str = "postgresql+psycopg://triad:triad_dev_pw@postgres:5432/triad"
    db_dsn: str = "postgresql://triad:triad_dev_pw@postgres:5432/triad"

    # Auth -------------------------------------------------------------------
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60

    # Spark ------------------------------------------------------------------
    spark_master_url: str = "local[2]"
    spark_driver_memory: str = "1g"
    spark_executor_memory: str = "1g"

    # LLM --------------------------------------------------------------------
    llm_provider: str = "template_only"  # template_only | recorded | anthropic | openai | ollama
    llm_model_id: str = "none"
    llm_cassette_dir: Path = REPO_ROOT / "tests" / "fixtures" / "cassettes"

    # Policy / catalogue paths ----------------------------------------------
    governance_policy_path: Path = REPO_ROOT / "agents" / "policies" / "governance.yaml"
    routing_policy_path: Path = REPO_ROOT / "agents" / "policies" / "routing.yaml"
    feature_catalog_path: Path = REPO_ROOT / "pipelines" / "feature_catalog" / "churn_features.yaml"
    pipeline_config_path: Path = REPO_ROOT / "pipelines" / "churn_v1.yaml"

    # Worker -----------------------------------------------------------------
    worker_name: str = "worker-1"
    lease_seconds: int = 300
    poll_interval_seconds: float = 1.0

    @property
    def mlflow_tracking_uri(self) -> str:
        return os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


@lru_cache(maxsize=8)
def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML configuration file. Raises if it is missing or not a mapping."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"configuration file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"configuration file is not a mapping: {p}")
    return data


def config_hash(config: dict[str, Any]) -> str:
    """Stable hash of a configuration mapping, used in run records (NFR-REP-01)."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def versions() -> dict[str, Any]:
    """The pinned version manifest (configs/versions.yaml)."""
    return load_yaml(REPO_ROOT / "configs" / "versions.yaml")
