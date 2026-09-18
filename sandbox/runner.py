"""The sandbox runner (PRD section 22, DD-04, FR-056).

Only this module starts containers for generated code. Its API accepts exactly one request
shape — run a known image with a registered artifact under a named resource profile — so
there is a single place where isolation is enforced and audited.

Controls applied to every run (PRD 22.2):
  non-root user, all capabilities dropped, no privilege escalation, read-only root
  filesystem, tmpfs scratch with a size cap, CPU/memory/pids limits, a hard timeout, an
  internal network with no route off the host, and no secrets in the environment.

`LocalRunner` is the fallback used when the docker socket is not available to this process
(the dev container deliberately has no socket). It applies the limits it can — working
directory, timeout, environment scrubbing — and records honestly that container isolation
was not in force, so no run can silently claim protection it did not have.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from triad_core.ids import utcnow

# PRD 22.4
RESOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "small": {"cpus": "2", "memory": "4g", "pids": 256, "timeout_s": 600, "scratch": "2g"},
    "medium": {"cpus": "4", "memory": "16g", "pids": 512, "timeout_s": 3600, "scratch": "10g"},
    "large": {"cpus": "8", "memory": "32g", "pids": 1024, "timeout_s": 10800, "scratch": "50g"},
}

# Env-A-mini cannot honour the PRD's medium/large sizes; the runner clamps them and says so.
HOST_LIMITS = {"cpus": 4.0, "memory_gb": 3.0}

SECRET_ENV_PREFIXES = (
    "ANTHROPIC", "OPENAI", "GEMINI", "GOOGLE", "AWS", "AZURE", "TRIAD_JWT", "POSTGRES",
    "TRIAD_DB", "MLFLOW_TRACKING", "KAGGLE", "GITHUB",
)


class SandboxError(Exception):
    pass


@dataclass
class SandboxRequest:
    run_key: str
    stage: str
    image: str
    command: list[str]
    profile: str = "small"
    mounts_ro: dict[str, str] = field(default_factory=dict)   # host path -> container path
    mounts_rw: dict[str, str] = field(default_factory=dict)   # run-scoped staging only
    env: dict[str, str] = field(default_factory=dict)
    network: str = "triad_triad-sandbox-net"
    workdir: str = "/workspace"

    def validate(self) -> None:
        if self.profile not in RESOURCE_PROFILES:
            raise SandboxError(f"unknown resource profile: {self.profile}")
        if not self.command:
            raise SandboxError("no command given")
        for key in self.env:
            if any(key.upper().startswith(p) for p in SECRET_ENV_PREFIXES):
                raise SandboxError(f"refusing to pass a secret-looking variable: {key}")
        # Writable mounts must be scoped to this run: its sandbox folder, a staging path
        # inside it, or scratch. Anything else (raw/, clean/, features/) is read-only.
        allowed_markers = (f"/sandbox/{self.run_key}", "staging", "/tmp")
        for target in self.mounts_rw.values():
            if not any(marker in target for marker in allowed_markers):
                raise SandboxError(
                    f"writable mount {target} is not scoped to run {self.run_key} (PRD 22.2)"
                )


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    isolation: str                     # "container" | "process"
    image_digest: str | None = None
    command: str = ""
    limits: dict[str, Any] = field(default_factory=dict)
    timed_out: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "isolation": self.isolation,
            "image_digest": self.image_digest,
            "command": self.command[:2000],
            "limits": self.limits,
            "timed_out": self.timed_out,
            "detail": self.detail[:2000],
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
        }


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def _scrubbed_env(extra: dict[str, str]) -> dict[str, str]:
    """A minimal environment: no inherited secrets, no LLM credentials (PRD 22.2)."""
    base = {
        "PATH": "/opt/java/temurin-17/bin:/opt/spark/bin:/usr/local/bin:/usr/bin:/bin",
        "JAVA_HOME": "/opt/java/temurin-17",
        "SPARK_HOME": "/opt/spark",
        "HOME": "/tmp",
        "TZ": "UTC",
        "PYTHONPATH": "/workspace",
        "TRIAD_SANDBOX": "1",
    }
    base.update(extra)
    return base


class DockerRunner:
    """Starts one `--rm` container per execution with the PRD's controls."""

    isolation = "container"

    def run(self, request: SandboxRequest) -> SandboxResult:
        request.validate()
        profile = RESOURCE_PROFILES[request.profile]
        cpus = min(float(profile["cpus"]), HOST_LIMITS["cpus"])
        mem_gb = min(float(profile["memory"].rstrip("g")), HOST_LIMITS["memory_gb"])

        name = f"triad-sbx-{request.run_key}-{request.stage}-{uuid.uuid4().hex[:6]}"[:60]
        args = [
            "docker", "run", "--rm", "--name", name,
            "--user", "1000:1000",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", f"/tmp:rw,size={profile['scratch']},exec",
            "--cpus", str(cpus),
            "--memory", f"{mem_gb}g",
            "--pids-limit", str(profile["pids"]),
            "--network", request.network,
            "--workdir", request.workdir,
        ]
        for host, target in request.mounts_ro.items():
            args += ["-v", f"{host}:{target}:ro"]
        for host, target in request.mounts_rw.items():
            args += ["-v", f"{host}:{target}:rw"]
        for k, v in _scrubbed_env(request.env).items():
            args += ["-e", f"{k}={v}"]
        args += [request.image, *request.command]

        started = time.perf_counter()
        timed_out = False
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=profile["timeout_s"]
            )
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            code = 124
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            subprocess.run(["docker", "kill", name], capture_output=True)

        duration = int((time.perf_counter() - started) * 1000)
        return SandboxResult(
            ok=(code == 0),
            exit_code=code,
            stdout=out,
            stderr=err,
            duration_ms=duration,
            isolation=self.isolation,
            image_digest=image_digest(request.image),
            command=" ".join(request.command),
            limits={"cpus": cpus, "memory_gb": mem_gb, "pids": profile["pids"],
                    "timeout_s": profile["timeout_s"], "network": request.network},
            timed_out=timed_out,
            detail="hard timeout reached" if timed_out else "",
        )


class LocalRunner:
    """Fallback for environments without a docker socket.

    It still enforces the timeout, a scrubbed environment and a run-scoped working directory,
    but it does **not** provide container isolation, and says so in every result so no report
    can claim protection that was not applied.
    """

    isolation = "process"

    def run(self, request: SandboxRequest) -> SandboxResult:
        request.validate()
        profile = RESOURCE_PROFILES[request.profile]
        started = time.perf_counter()
        timed_out = False
        workdir = next(iter(request.mounts_rw), None) or os.getcwd()
        try:
            proc = subprocess.run(
                request.command,
                capture_output=True,
                text=True,
                timeout=profile["timeout_s"],
                env=_scrubbed_env(request.env),
                cwd=workdir if Path(workdir).exists() else None,
            )
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            code = 124
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")

        return SandboxResult(
            ok=(code == 0),
            exit_code=code,
            stdout=out,
            stderr=err,
            duration_ms=int((time.perf_counter() - started) * 1000),
            isolation=self.isolation,
            command=" ".join(request.command),
            limits={"timeout_s": profile["timeout_s"], "network": "host (not isolated)"},
            timed_out=timed_out,
            detail="container isolation unavailable: no docker socket in this process",
        )


def image_digest(image: str) -> str | None:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{index .RepoDigests 0}}"],
            capture_output=True, text=True, timeout=30,
        )
        digest = proc.stdout.strip()
        if digest:
            return digest
        proc = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True, text=True, timeout=30,
        )
        return proc.stdout.strip() or None
    except Exception:
        return None


def get_runner() -> DockerRunner | LocalRunner:
    """The single entry point. Nothing else in TRIAD starts a process for generated code."""
    return DockerRunner() if docker_available() else LocalRunner()


def spark_submit_request(
    *,
    run_key: str,
    stage: str,
    jar: str,
    core_jar: str,
    main_class: str,
    job_args: list[str],
    master: str = "local[2]",
    profile: str = "small",
    image: str = "triad-base:0.1",
    data_root: str = "/data/triad",
    driver_memory: str = "1g",
) -> SandboxRequest:
    """Build the one spark-submit shape the runner accepts (DEC-04: thin JARs plus --jars)."""
    command = [
        "/opt/spark/bin/spark-submit",
        "--master", master,
        "--class", main_class,
        "--jars", core_jar,
        "--conf", "spark.ui.enabled=false",
        "--conf", f"spark.driver.memory={driver_memory}",
        "--conf", "spark.sql.ansi.enabled=true",
        "--conf", "spark.sql.session.timeZone=UTC",
        "--conf", "spark.sql.parquet.outputTimestampType=TIMESTAMP_MICROS",
        jar,
        *job_args,
    ]
    return SandboxRequest(
        run_key=run_key,
        stage=stage,
        image=image,
        command=command,
        profile=profile,
        mounts_rw={f"{data_root}/sandbox/{run_key}": f"{data_root}/sandbox/{run_key}"},
        env={"TRIAD_RUN_KEY": run_key, "SPARK_SUBMIT_AT": utcnow().isoformat()},
    )
