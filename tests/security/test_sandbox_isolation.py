"""ST-06 and ST-07, executed: a job in the sandbox cannot reach the network, write outside its
run's folders, or escape its limits.

These start real containers through the running sandbox-runner, the path every Spark job takes,
so they are skipped where no runner is configured (TRIAD_SANDBOX_URL). Until 2026-10-04 there was
no runner: `DockerRunner` shelled out to a `docker` CLI no container had, the orchestrator had no
socket, and every job ran under `LocalRunner` on the host network, while the documentation said
the sandbox had no route to the internet or the database.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from sandbox.runner import RemoteRunner, SandboxError, SandboxRequest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TRIAD_SANDBOX_URL"), reason="no sandbox-runner configured"
)

DATA_ROOT = os.environ.get("TRIAD_DATA_ROOT", "/data/triad")


@pytest.fixture()
def runner() -> RemoteRunner:
    return RemoteRunner(os.environ["TRIAD_SANDBOX_URL"], os.environ.get("TRIAD_SANDBOX_TOKEN", ""))


@pytest.fixture()
def run_key() -> str:
    return f"st-{uuid.uuid4().hex[:8]}"


def _probe(runner: RemoteRunner, run_key: str, code: str, **request: object) -> dict:
    result = runner.run(SandboxRequest(
        run_key=run_key, stage="probe", image="triad-base:0.1",
        command=["python", "-c", code], network="none", workdir="/tmp", **request,
    ))
    assert result.isolation == "container"
    assert result.ok, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


EGRESS = """
import json, socket
out = {}
for name, host, port in [("internet", "1.1.1.1", 443), ("postgres", "postgres", 5432),
                         ("runner", "sandbox-runner", 8090), ("mlflow", "mlflow", 5000)]:
    try:
        socket.create_connection((host, port), timeout=3).close()
        out[name] = "reached"
    except OSError as exc:
        out[name] = type(exc).__name__
try:
    socket.getaddrinfo("example.com", 443)
    out["dns"] = "resolved"
except OSError as exc:
    out["dns"] = type(exc).__name__
print(json.dumps(out))
"""


def test_st06_a_job_reaches_nothing_on_the_network(runner, run_key) -> None:
    seen = _probe(runner, run_key, EGRESS)
    assert "reached" not in seen.values(), seen
    assert seen["dns"] != "resolved", seen


FILESYSTEM = """
import json, os
def attempt(path):
    try:
        with open(path, "w") as f:
            f.write("x")
        return "written"
    except OSError as exc:
        return type(exc).__name__
status = open("/proc/self/status").read()
cap_eff = next(l.split()[1] for l in status.splitlines() if l.startswith("CapEff"))
def cgroup(name):
    try:
        return open(f"/sys/fs/cgroup/{name}").read().strip()
    except OSError:
        return None
print(json.dumps({
    "uid": os.getuid(),
    "cap_eff": cap_eff,
    "root_fs": attempt("/probe"),
    "etc": attempt("/etc/probe"),
    "read_only_input": attempt(os.environ["RO"] + "/probe"),
    "run_folder": attempt(os.environ["RW"] + "/probe"),
    "data_root": attempt(os.environ["DATA_ROOT"] + "/probe"),
    "scratch": attempt("/tmp/probe"),
    "pids_max": cgroup("pids.max"),
    "memory_max": cgroup("memory.max"),
    "socket_visible": os.path.exists("/var/run/docker.sock"),
}))
"""


def test_st07_a_job_writes_only_where_its_run_may_and_runs_unprivileged(runner, run_key) -> None:
    run_folder = f"{DATA_ROOT}/sandbox/{run_key}"
    read_only = f"{DATA_ROOT}/sandbox/{run_key}-input"
    os.makedirs(read_only, exist_ok=True)

    seen = _probe(
        runner, run_key, FILESYSTEM,
        mounts_ro={read_only: read_only}, mounts_rw={run_folder: run_folder},
        env={"RO": read_only, "RW": run_folder, "DATA_ROOT": DATA_ROOT},
    )

    assert seen["uid"] == 1000, "the job runs as the unprivileged `triad` user"
    assert int(seen["cap_eff"], 16) == 0, "every capability is dropped"
    assert seen["root_fs"] != "written" and seen["etc"] != "written", "the root filesystem is read-only"
    assert seen["read_only_input"] != "written", "an input is mounted read-only"
    assert seen["run_folder"] == "written", "the run's own folder is writable"
    assert seen["data_root"] != "written", "the rest of the data root is not even mounted"
    assert seen["scratch"] == "written", "scratch is a tmpfs"
    assert seen["pids_max"] == "256", "the small profile's process limit"
    assert seen["memory_max"] == str(3 * 1024**3), "the small profile, clamped to Env-A-mini"
    assert not seen["socket_visible"], "the docker socket never reaches a job"


def test_st07_a_request_for_the_docker_socket_is_refused(runner, run_key) -> None:
    with pytest.raises(SandboxError, match="outside what a job may be given"):
        runner.run(SandboxRequest(
            run_key=run_key, stage="probe", image="triad-base:0.1", command=["true"],
            network="none", mounts_ro={"/var/run/docker.sock": "/var/run/docker.sock"},
        ))


def test_st07_an_unlisted_image_is_refused(runner, run_key) -> None:
    with pytest.raises(SandboxError, match="not one this runner starts"):
        runner.run(SandboxRequest(run_key=run_key, stage="probe", image="alpine:latest",
                                  command=["true"], network="none"))


def test_the_runner_refuses_a_caller_without_the_token(run_key) -> None:
    impostor = RemoteRunner(os.environ["TRIAD_SANDBOX_URL"], "not-the-token")
    with pytest.raises(SandboxError, match="401"):
        impostor.run(SandboxRequest(run_key=run_key, stage="probe", image="triad-base:0.1",
                                    command=["true"], network="none"))
