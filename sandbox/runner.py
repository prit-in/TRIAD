"""The sandbox runner (PRD section 22, DD-04, FR-056).

Only this module starts containers for generated code. Its API accepts exactly one request
shape — run a known image with a registered artifact under a named resource profile — so
there is a single place where isolation is enforced and audited.

Controls applied to every run (PRD 22.2):
  non-root user, all capabilities dropped, no privilege escalation, read-only root
  filesystem, tmpfs scratch with a size cap, CPU/memory/pids limits, a hard timeout, no
  network at all for a local-mode Spark job (an internal one for a cluster job), only the
  job's own inputs and JARs mounted read-only, only run-scoped folders writable, and no
  secrets in the environment.

Three runners, one chosen by `get_runner`:

  RemoteRunner   the orchestrator's: hands each request to the sandbox-runner service
                 (sandbox/service.py), the only process with the docker socket (DD-04).
                 Fails closed when the service cannot be reached.
  DockerRunner   the service's: starts the container through the Docker SDK.
  LocalRunner    the fallback when neither is configured. It applies what it can - working
                 directory, timeout, environment scrubbing - and records that container
                 isolation was not in force, so no run can claim protection it did not have.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from triad_core.ids import utcnow

log = logging.getLogger(__name__)

# PRD 22.4, whose values are marked (EX). The process limits are doubled from it (DEC-25): a
# Spark driver's threads count against the cgroup's pids limit, and the feature job peaked at
# 292 (measured through `pids.peak`), so 256 killed it with "unable to create native thread".
RESOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "small": {"cpus": "2", "memory": "4g", "pids": 512, "timeout_s": 600, "scratch": "2g"},
    "medium": {"cpus": "4", "memory": "16g", "pids": 1024, "timeout_s": 3600, "scratch": "10g"},
    "large": {"cpus": "8", "memory": "32g", "pids": 2048, "timeout_s": 10800, "scratch": "50g"},
}

# Env-A-mini cannot honour the PRD's medium/large sizes; the runner clamps them and says so.
HOST_LIMITS = {"cpus": 4.0, "memory_gb": 3.0}

SECRET_ENV_PREFIXES = (
    "ANTHROPIC", "OPENAI", "GEMINI", "GOOGLE", "AWS", "AZURE", "TRIAD_JWT", "POSTGRES",
    "TRIAD_DB", "MLFLOW_TRACKING", "KAGGLE", "GITHUB", "TRIAD_SANDBOX_TOKEN",
)

# `triad` in triad-base. Writable mounts are handed to this user before a job starts.
SANDBOX_USER = (1000, 1000)

# The only paths a job may be given. Anything else this runner can see - the docker socket
# above all - must never become a mount, whatever a request asks for.
DATA_ROOT = os.environ.get("TRIAD_DATA_ROOT", "/data/triad")
SHAREABLE_ROOTS = (DATA_ROOT, "/workspace")


# A call to the engine that fails transiently is retried with exponential backoff, at most
# three attempts in all (NFR-REL-01): 10 s, then 20 s, each call itself bounded by the SDK's
# 60 s, so an engine stalled for about three and a half minutes is outlasted. Under memory
# pressure it stalls for minutes while the containers it runs carry on (EXP-2, 11-10-2026,
# DEC-52). ENGINE_RETRY_PAUSE_S spaces the waits on a job that is still running.
ENGINE_ATTEMPTS = 3
ENGINE_BACKOFF_S = 10.0
ENGINE_RETRY_PAUSE_S = 5.0


SANDBOX_NETWORK_SUFFIX = "triad-sandbox-net"
"""The compose network declared `internal: true`; the project prefix varies, the name does not."""


def _transient(exc: BaseException) -> bool:
    """A failure of the conversation with the engine, not of the job or of the request: a
    dropped, broken or timed-out connection, or the engine's own server error.

    The SDK's `APIError` is a `requests` HTTP error too, so it is told apart first: a 404 or a
    409 is the engine's answer, and asking again would get the same one.
    """
    import requests
    from docker.errors import APIError

    if isinstance(exc, APIError):
        return bool(exc.is_server_error())
    return isinstance(exc, requests.exceptions.RequestException)


def _engine(call: Any, *args: Any, **kwargs: Any) -> Any:
    """`call(*args, **kwargs)` against the engine, retried while it fails transiently - with
    exponential backoff, at most ENGINE_ATTEMPTS attempts - and then EngineUnavailable."""
    for attempt in range(1, ENGINE_ATTEMPTS + 1):
        try:
            return call(*args, **kwargs)
        except Exception as exc:
            if not _transient(exc):
                raise
            if attempt == ENGINE_ATTEMPTS:
                raise EngineUnavailable(
                    f"the container engine did not answer in {ENGINE_ATTEMPTS} attempts: "
                    f"{type(exc).__name__}: {exc}") from exc
            time.sleep(ENGINE_BACKOFF_S * 2 ** (attempt - 1))
    raise AssertionError("unreachable")  # pragma: no cover


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
    network: str = "triad_" + SANDBOX_NETWORK_SUFFIX
    workdir: str = "/workspace"

    def validate(self) -> None:
        if self.profile not in RESOURCE_PROFILES:
            raise SandboxError(f"unknown resource profile: {self.profile}")
        # `internal_only` in the policy file is a real constraint, so it is checked where
        # every path has to pass: a job that names any other network could reach off the
        # host. The one permitted name is the compose network declared `internal: true`.
        if self.network != "none" and not self.network.endswith(SANDBOX_NETWORK_SUFFIX):
            raise SandboxError(
                f"sandbox network {self.network!r} is not the internal sandbox network "
                f"(must end with {SANDBOX_NETWORK_SUFFIX!r}); jobs never reach off the host"
            )
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
    """Whether this process can reach a Docker engine. Only sandbox-runner has the socket."""
    try:
        import docker

        return bool(docker.from_env(timeout=10).ping())
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


class EngineUnavailable(SandboxError):
    """The container engine stopped answering. The job is not known to have failed - it may
    still be running - so this is never the job's error, and the service reports it as 503."""


class DockerRunner:
    """Starts one container per execution with the PRD's controls, through the Docker SDK.

    The image ships the SDK and no CLI. This used to shell out to `docker run`, which no
    container here could execute, and its result omitted two required fields - it had never
    once run. Paths in a request are paths *this* container sees; `mount` turns each into the
    volume or bind mount it lives on, so the job sees the same path and nothing more.
    """

    isolation = "container"

    def __init__(self, client: Any = None, own_mounts: list[dict[str, Any]] | None = None) -> None:
        if client is None:
            import docker

            client = docker.from_env(timeout=60)
        self.client = client
        if own_mounts is None:
            import socket

            own_mounts = client.containers.get(socket.gethostname()).attrs.get("Mounts", [])
        self.own_mounts = sorted(own_mounts, key=lambda m: len(m["Destination"]), reverse=True)

    def mount(self, path: str, target: str, *, read_only: bool) -> Any:
        from docker.types import Mount

        for p in (path, target):
            if os.path.normpath(p) != p or not any(
                p == root or p.startswith(root + "/") for root in SHAREABLE_ROOTS
            ):
                raise SandboxError(f"{p} is outside what a job may be given")
        for m in self.own_mounts:
            dest = m["Destination"].rstrip("/")
            if path != dest and not path.startswith(dest + "/"):
                continue
            rel = path[len(dest):].strip("/")
            if m["Type"] == "volume":
                return Mount(target, m["Name"], type="volume", read_only=read_only,
                             subpath=rel or None)
            if m["Type"] == "bind":
                source = f"{m['Source'].rstrip('/')}/{rel}" if rel else m["Source"]
                return Mount(target, source, type="bind", read_only=read_only)
        raise SandboxError(f"{path} is on no volume or bind mount this runner shares")

    def run(self, request: SandboxRequest) -> SandboxResult:
        request.validate()
        profile = RESOURCE_PROFILES[request.profile]
        cpus = min(float(profile["cpus"]), HOST_LIMITS["cpus"])
        mem_gb = min(float(profile["memory"].rstrip("g")), HOST_LIMITS["memory_gb"])

        mounts = [self.mount(s, t, read_only=True) for s, t in request.mounts_ro.items()]
        for source, target in request.mounts_rw.items():
            _hand_to_sandbox_user(source)
            mounts.append(self.mount(source, target, read_only=False))

        name = f"triad-sbx-{request.run_key}-{request.stage}-{uuid.uuid4().hex[:6]}"[:60]
        # Labelled with the stage it runs, so a resubmission of the stage can find and end any
        # copy an earlier submission left behind: two copies of one job write the same staging
        # and report paths, and compete for the same memory (DEC-52).
        labels = {"triad.run_key": request.run_key, "triad.stage": request.stage}
        self._reap(labels)
        started = time.perf_counter()
        try:
            container = self._start(
                request, name, labels=labels,
                user=f"{SANDBOX_USER[0]}:{SANDBOX_USER[1]}",
                cap_drop=["ALL"], security_opt=["no-new-privileges"],
                read_only=True, tmpfs={"/tmp": f"rw,size={profile['scratch']},exec"},
                nano_cpus=int(cpus * 1e9), pids_limit=int(profile["pids"]),
                # Memory and memory-plus-swap set to the same value: no swap. Docker's default
                # lets a container swap as much again as its memory limit, so the 3 GB clamp was
                # really 6, and a job that outgrew it ground through the VM's shared swap -
                # starving every other container - instead of being stopped at the limit (EXP-2,
                # 08-10-2026).
                mem_limit=f"{mem_gb}g", memswap_limit=f"{mem_gb}g",
                network_mode=request.network, working_dir=request.workdir,
                # With no network the container's generated hostname resolves to nothing, and
                # the JVM's logging asks for it on every logger it creates.
                hostname="localhost" if request.network == "none" else None,
                mounts=mounts, environment=_scrubbed_env(request.env),
            )
        except Exception:
            # Starting failed, but the container may exist: removed here if the engine answers,
            # and otherwise by the next submission of the stage (_reap).
            with contextlib.suppress(Exception):
                self.client.containers.get(name).remove(force=True)
            raise
        timed_out = removed = False
        try:
            code = self._wait(container, started + profile["timeout_s"])
            if code is None:
                timed_out, code = True, 124
                # It may have stopped on its own since, and a stopped container refuses a kill.
                with contextlib.suppress(Exception):
                    _engine(container.kill)
            # Exit 137 is a SIGKILL, and only the engine knows whose: the kernel's, because the
            # container outgrew its memory limit, or someone else's. The two need different
            # remedies, and the job's own log - cut off mid-line - says neither.
            _engine(container.reload)
            oom_killed = bool(container.attrs.get("State", {}).get("OOMKilled"))
            out = _engine(container.logs, stdout=True, stderr=False).decode("utf-8", "replace")
            err = _engine(container.logs, stdout=False, stderr=True).decode("utf-8", "replace")
        finally:
            # Never at the cost of the result, or of the error that ended the job: on 11-10-2026
            # a remove that timed out against a stalled engine replaced both (DEC-52).
            try:
                _engine(container.remove, force=True)
                removed = True
            except Exception:
                log.warning("sandbox container %s could not be removed", name, exc_info=True)

        return SandboxResult(
            ok=(code == 0 and not timed_out),
            exit_code=code,
            stdout=out,
            stderr=err,
            duration_ms=int((time.perf_counter() - started) * 1000),
            isolation=self.isolation,
            image_digest=_image_id(self.client, request.image),
            command=" ".join(request.command),
            limits={"cpus": cpus, "memory_gb": mem_gb, "pids": profile["pids"],
                    "timeout_s": profile["timeout_s"], "network": request.network,
                    "read_only_root": True, "user": f"{SANDBOX_USER[0]}:{SANDBOX_USER[1]}"},
            timed_out=timed_out,
            detail="; ".join(part for part in (
                "hard timeout reached" if timed_out
                else "Container killed by the kernel: it exceeded its memory limit (OOMKilled)"
                if oom_killed
                else "Lost executor: the sandbox container was killed from outside (exit 137)"
                if code == 137 else "",
                "" if removed else f"container {name} could not be removed",
            ) if part),
        )

    def _reap(self, labels: dict[str, str]) -> None:
        """End and remove every container an earlier submission of this stage left behind.

        One is left when the engine stops answering while it runs, or when the orchestrator
        gives up on the service and resubmits - and it may still be running.
        """
        stale = _engine(self.client.containers.list, all=True,
                        filters={"label": [f"{key}={value}" for key, value in labels.items()]})
        for container in stale:
            log.warning("removing %s, left behind by an earlier submission of %s/%s",
                        container.name, labels["triad.run_key"], labels["triad.stage"])
            try:
                _engine(container.remove, force=True)
            except EngineUnavailable:
                raise
            except Exception:  # already gone, or going
                continue

    def _start(self, request: SandboxRequest, name: str, **spec: Any) -> Any:
        """Create and start the job's container, retried like any call to the engine.

        The engine can act and lose its reply. Before each new attempt the runner looks for the
        container by its name: one only created is started, one already running is taken, and
        one is created only if there is none - never a second.
        """
        from docker.errors import NotFound

        for attempt in range(1, ENGINE_ATTEMPTS + 1):
            try:
                if attempt > 1:
                    try:
                        container = self.client.containers.get(name)
                    except NotFound:
                        container = None
                    if container is not None:
                        if container.status == "created":
                            container.start()
                        return container
                return self.client.containers.run(request.image, request.command, name=name,
                                                  detach=True, **spec)
            except Exception as exc:
                if not _transient(exc):
                    raise
                if attempt == ENGINE_ATTEMPTS:
                    raise EngineUnavailable(
                        f"the container engine did not start the job in {ENGINE_ATTEMPTS} "
                        f"attempts: {type(exc).__name__}: {exc}") from exc
                time.sleep(ENGINE_BACKOFF_S * 2 ** (attempt - 1))
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, container: Any, deadline: float) -> int | None:
        """The job's exit code, or None once the hard deadline has passed.

        A wait whose connection broke or timed out early says nothing about the job. The runner
        used to read it as the job failing, and its clean-up then removed - killed - a container
        that was still working: twice in one run, a 24-minute feature job each time (EXP-2,
        11-10-2026, DEC-52). Now it asks the engine where the job is, and waits again.
        """
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None
            try:
                return int(container.wait(timeout=max(1, int(remaining)))["StatusCode"])
            except Exception as exc:
                if not _transient(exc):
                    raise
            # Not a retry of failed work - the job is not known to have failed - but a question
            # to the engine. A job needs no engine to run, so an engine that stops answering is
            # waited out until the job's own deadline, and only then given up: giving up sooner
            # killed, or orphaned, a job that was still working (review of 12-10-2026, DEC-52).
            try:
                _engine(container.reload)
            except EngineUnavailable:
                if time.perf_counter() >= deadline:
                    raise
            else:
                state = container.attrs.get("State", {})
                if state.get("Status") in ("exited", "dead"):
                    return int(state.get("ExitCode", -1))
            time.sleep(min(ENGINE_RETRY_PAUSE_S, max(0.0, deadline - time.perf_counter())))


def _hand_to_sandbox_user(path: str) -> None:
    """Make a writable mount writable by the job: the platform created it as root.

    Symlinks are changed themselves, never followed: a job could have left one in its own
    folder pointing anywhere this runner can see.
    """
    if not (path == DATA_ROOT or path.startswith(DATA_ROOT + "/")):
        raise SandboxError(f"writable mount {path} is not under the data root")
    os.makedirs(path, exist_ok=True)
    uid, gid = SANDBOX_USER
    for root, _dirs, files in os.walk(path):
        os.chown(root, uid, gid, follow_symlinks=False)
        for name in files:
            os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)


def _image_id(client: Any, image: str) -> str | None:
    try:
        attrs = client.images.get(image).attrs
        return (attrs.get("RepoDigests") or [None])[0] or attrs.get("Id")
    except Exception:
        return None


class RemoteRunner:
    """The orchestrator's runner: each request goes to sandbox-runner, which holds the socket.

    Fails closed. If the service cannot be reached the job does not run - falling back to
    LocalRunner would quietly drop the isolation this deployment was configured to have.
    """

    isolation = "container"

    def __init__(self, url: str, token: str) -> None:
        if not token:
            raise SandboxError("TRIAD_SANDBOX_URL is set without TRIAD_SANDBOX_TOKEN")
        self.url = url.rstrip("/")
        self.token = token

    def run(self, request: SandboxRequest) -> SandboxResult:
        request.validate()
        call = urllib.request.Request(
            f"{self.url}/run", data=json.dumps(asdict(request)).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        # The job's own hard timeout is enforced by the service; this one only bounds the wait,
        # and leaves room for the service's own retries against a stalled engine (DEC-52).
        timeout = RESOURCE_PROFILES[request.profile]["timeout_s"] + 600
        try:
            with urllib.request.urlopen(call, timeout=timeout) as response:
                return SandboxResult(**json.loads(response.read()))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:2000]
            raise SandboxError(f"sandbox-runner refused the job ({exc.code}): {detail}") from exc
        except OSError as exc:
            raise SandboxError(f"sandbox-runner unreachable at {self.url}: {exc}") from exc


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


def get_runner() -> RemoteRunner | DockerRunner | LocalRunner:
    """The single entry point. Nothing else in TRIAD starts a process for generated code."""
    url = os.environ.get("TRIAD_SANDBOX_URL")
    if url:
        return RemoteRunner(url, os.environ.get("TRIAD_SANDBOX_TOKEN", ""))
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
    executor_memory: str | None = None,
) -> SandboxRequest:
    """Build the one spark-submit shape the runner accepts (DEC-04: thin JARs plus --jars).

    `executor_memory` is for a cluster master only: in local mode the driver is the executor.
    """
    command = [
        "/opt/spark/bin/spark-submit",
        "--master", master,
        "--class", main_class,
        "--jars", core_jar,
        "--conf", "spark.ui.enabled=false",
        "--conf", f"spark.driver.memory={driver_memory}",
        *(["--conf", f"spark.executor.memory={executor_memory}"]
          if executor_memory and not master.startswith("local") else []),
        "--conf", "spark.sql.ansi.enabled=true",
        "--conf", "spark.sql.session.timeZone=UTC",
        "--conf", "spark.sql.parquet.outputTimestampType=TIMESTAMP_MICROS",
        jar,
        *job_args,
    ]
    # Least privilege: the job sees its own inputs and the two JARs, read-only, and writes only
    # inside its run's sandbox folder - output, report and quarantine alike; the platform moves
    # them into place. Not the data root.
    sandbox_dir = f"{data_root}/sandbox/{run_key}"
    readable = {str(Path(j).parent): str(Path(j).parent) for j in (jar, core_jar)}
    writable = {sandbox_dir: sandbox_dir}
    for arg in job_args:
        if arg.startswith("--input="):
            path = _local_path(arg.split(":", 1)[1])
            folder = str(Path(path).parent) if Path(path).suffix else path
            readable[folder] = folder
    local = master.startswith("local")
    return SandboxRequest(
        run_key=run_key,
        stage=stage,
        image=image,
        command=command,
        profile=profile,
        mounts_ro=readable,
        mounts_rw=writable,
        # A local-mode job is one JVM: it needs no network, so it gets none. Spark is told to
        # bind the loopback, which is all a container with no network has.
        network="none" if local else "triad_" + SANDBOX_NETWORK_SUFFIX,
        workdir="/tmp",
        env={"TRIAD_RUN_KEY": run_key, "SPARK_SUBMIT_AT": utcnow().isoformat(),
             **({"SPARK_LOCAL_IP": "127.0.0.1", "SPARK_LOCAL_HOSTNAME": "localhost"}
                if local else {})},
    )


def _local_path(uri: str) -> str:
    return uri[len("file://"):] if uri.startswith("file://") else uri
