"""The container runner's own logic, without a docker engine: path translation, the controls it
asks for, the hard timeout, and the remote runner failing closed. (ST-06/ST-07 against real
containers are in tests/security/test_sandbox_isolation.py.)"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

from sandbox import runner as rn
from sandbox.runner import (
    DockerRunner,
    RemoteRunner,
    SandboxError,
    SandboxRequest,
    SandboxResult,
    spark_submit_request,
)
from sandbox.service import make_handler

OWN_MOUNTS = [
    {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"},
    {"Type": "volume", "Name": "triad_triad-data", "Source": "/var/lib/docker/volumes/x",
     "Destination": "/data/triad"},
    {"Type": "bind", "Source": "/run/desktop/mnt/host/d/Case_Study", "Destination": "/workspace"},
]


class _Container:
    def __init__(self, *, exit_code: int = 0, hang: bool = False) -> None:
        self.exit_code, self.hang = exit_code, hang
        self.killed = self.removed = False

    def wait(self, timeout: int) -> dict:
        if self.hang:
            raise requests.exceptions.ReadTimeout("read timed out")
        return {"StatusCode": self.exit_code}

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        return b"out" if stdout else b"err"

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool) -> None:
        self.removed = True


class _Client:
    def __init__(self, container: _Container) -> None:
        self.container, self.calls = container, []
        self.containers = self
        self.images = self

    def run(self, image, command, **kwargs):  # containers.run
        self.calls.append((image, command, kwargs))
        return self.container

    def get(self, image):  # images.get
        raise LookupError(image)


def _request(**overrides) -> SandboxRequest:
    fields = {"run_key": "run-1", "stage": "clean", "image": "triad-base:0.1",
              "command": ["true"], "network": "none", "workdir": "/tmp"}
    return SandboxRequest(**{**fields, **overrides})


# --- path translation ---------------------------------------------------------------


def test_a_data_path_becomes_a_subpath_of_the_shared_volume() -> None:
    m = DockerRunner(client=_Client(_Container()), own_mounts=OWN_MOUNTS).mount(
        "/data/triad/raw/transactions/v=12", "/data/triad/raw/transactions/v=12", read_only=True)
    assert m["Type"] == "volume" and m["Source"] == "triad_triad-data"
    assert m["VolumeOptions"]["Subpath"] == "raw/transactions/v=12"
    assert m["ReadOnly"] is True


def test_a_source_path_becomes_a_bind_from_the_hosts_folder() -> None:
    m = DockerRunner(client=_Client(_Container()), own_mounts=OWN_MOUNTS).mount(
        "/workspace/scala_spark/jobs/target", "/workspace/scala_spark/jobs/target", read_only=True)
    assert m["Type"] == "bind"
    assert m["Source"] == "/run/desktop/mnt/host/d/Case_Study/scala_spark/jobs/target"


@pytest.mark.parametrize("path", ["/var/run/docker.sock", "/etc", "/data/triad/../../etc",
                                  "/data/triadx", "/"])
def test_nothing_outside_the_data_root_and_the_source_can_be_mounted(path) -> None:
    runner = DockerRunner(client=_Client(_Container()), own_mounts=OWN_MOUNTS)
    with pytest.raises(SandboxError, match="outside what a job may be given"):
        runner.mount(path, path, read_only=True)


# --- the controls -------------------------------------------------------------------------


def test_every_control_is_asked_for(monkeypatch, tmp_path) -> None:
    chowned = []
    monkeypatch.setattr(rn, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(rn, "SHAREABLE_ROOTS", (str(tmp_path), "/workspace"))
    monkeypatch.setattr(rn.os, "chown", lambda p, u, g, follow_symlinks: chowned.append(
        (p, u, g, follow_symlinks)))
    run_folder = str(tmp_path / "sandbox" / "run-1")
    client = _Client(_Container())
    own = [{"Type": "volume", "Name": "v", "Destination": str(tmp_path)}]

    result = DockerRunner(client=client, own_mounts=own).run(
        _request(mounts_rw={run_folder: run_folder}, env={"TRIAD_RUN_KEY": "run-1"}))

    [(image, command, kw)] = client.calls
    assert (image, command) == ("triad-base:0.1", ["true"])
    assert kw["user"] == "1000:1000"
    assert kw["cap_drop"] == ["ALL"] and kw["security_opt"] == ["no-new-privileges"]
    assert kw["read_only"] is True and kw["network_mode"] == "none"
    # With no network an unresolvable hostname stalled every logger's lookup, and Spark's
    # cached thread pools grew until the pids limit killed the job (run-2026-10-04-002).
    assert kw["hostname"] == "localhost"
    assert kw["pids_limit"] == 512 and kw["mem_limit"] == "3.0g"
    assert kw["tmpfs"] == {"/tmp": "rw,size=2g,exec"}
    assert "TRIAD_DB_URL" not in kw["environment"]
    assert (run_folder, 1000, 1000, False) in chowned, "the run folder is handed to the job's user"
    assert result.ok and result.isolation == "container" and client.container.removed


def test_the_hard_timeout_kills_the_container(monkeypatch) -> None:
    monkeypatch.setitem(rn.RESOURCE_PROFILES, "small", {**rn.RESOURCE_PROFILES["small"],
                                                        "timeout_s": 0})
    client = _Client(_Container(hang=True))
    result = DockerRunner(client=client, own_mounts=OWN_MOUNTS).run(_request())
    assert result.timed_out and result.exit_code == 124 and not result.ok
    assert client.container.killed and client.container.removed


def test_a_wait_that_fails_early_is_an_error_not_a_timeout() -> None:
    client = _Client(_Container(hang=True))   # raises long before the 600 s limit
    with pytest.raises(requests.exceptions.RequestException):
        DockerRunner(client=client, own_mounts=OWN_MOUNTS).run(_request())
    assert client.container.removed and not client.container.killed


# --- the remote runner and the service ------------------------------------------------------


@pytest.fixture()
def service():
    class _Runner:
        isolation = "container"

        def run(self, request):
            return SandboxResult(ok=True, exit_code=0, stdout="hi", stderr="", duration_ms=5,
                                 isolation="container", command=" ".join(request.command))

    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 make_handler(_Runner(), "tok", frozenset({"triad-base:0.1"})))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_request_round_trips_through_the_service(service) -> None:
    result = RemoteRunner(service, "tok").run(_request(command=["echo", "hi"]))
    assert result.ok and result.isolation == "container" and result.stdout == "hi"


def test_the_service_refuses_a_wrong_token_an_unlisted_image_and_a_secret(service) -> None:
    with pytest.raises(SandboxError, match="401"):
        RemoteRunner(service, "wrong").run(_request())
    with pytest.raises(SandboxError, match="not one this runner starts"):
        RemoteRunner(service, "tok").run(_request(image="alpine:latest"))
    with pytest.raises(SandboxError, match="secret-looking"):
        RemoteRunner(service, "tok").run(_request(env={"TRIAD_SANDBOX_TOKEN": "tok"}))


def test_an_unreachable_runner_fails_closed() -> None:
    """No fallback to LocalRunner: the job does not run at all."""
    with pytest.raises(SandboxError, match="unreachable"):
        RemoteRunner("http://127.0.0.1:9", "tok").run(_request())


def test_a_runner_url_without_a_token_is_refused_at_start(monkeypatch) -> None:
    monkeypatch.setenv("TRIAD_SANDBOX_URL", "http://sandbox-runner:8090")
    monkeypatch.delenv("TRIAD_SANDBOX_TOKEN", raising=False)
    with pytest.raises(SandboxError, match="without TRIAD_SANDBOX_TOKEN"):
        rn.get_runner()


def test_the_service_reply_is_valid_json_for_the_runner(service) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{service}/health") as response:
        assert json.loads(response.read()) == {"status": "ok", "isolation": "container"}


# --- what a Spark job is given ---------------------------------------------------------------


def test_a_spark_job_is_given_its_inputs_read_only_and_its_run_folders_writable() -> None:
    req = spark_submit_request(
        run_key="run-1", stage="clean",
        jar="/workspace/scala_spark/jobs/target/scala-2.13/jobs.jar",
        core_jar="/workspace/scala_spark/core/target/scala-2.13/core.jar",
        main_class="triad.jobs.generated.CleanJob_x",
        job_args=["--staging=file:///data/triad/sandbox/run-1/staging/clean",
                  "--quarantine=file:///data/triad/sandbox/run-1/quarantine/clean",
                  "--input=source:file:///data/triad/raw/transactions/v=12/part-00000.parquet",
                  "--input=returns:file:///data/triad/clean/returns/v=1"],
    )
    req.validate()
    assert set(req.mounts_ro) == {
        "/workspace/scala_spark/jobs/target/scala-2.13",
        "/workspace/scala_spark/core/target/scala-2.13",
        "/data/triad/raw/transactions/v=12",       # a file input: its version folder
        "/data/triad/clean/returns/v=1",           # a folder input: itself
    }
    # One writable mount: everything the job writes is inside it. A folder Spark overwrites
    # cannot itself be a mount point - it deletes the folder first.
    assert set(req.mounts_rw) == {"/data/triad/sandbox/run-1"}
    assert req.network == "none" and req.env["SPARK_LOCAL_IP"] == "127.0.0.1"


def test_a_cluster_job_gets_the_internal_network_not_none() -> None:
    req = spark_submit_request(run_key="run-1", stage="clean", jar="/workspace/a/j.jar",
                               core_jar="/workspace/a/c.jar", main_class="X", job_args=[],
                               master="spark://spark-master:7077")
    assert req.network.endswith(rn.SANDBOX_NETWORK_SUFFIX)
    assert "SPARK_LOCAL_IP" not in req.env


def test_an_engine_error_is_reported_as_the_jobs_failure_not_as_an_outage() -> None:
    class _Broken:
        isolation = "container"

        def run(self, request):
            raise RuntimeError("invalid mount config: subpath does not exist")

    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 make_handler(_Broken(), "tok", frozenset({"triad-base:0.1"})))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        with pytest.raises(SandboxError, match=r"refused the job \(500\).*subpath does not exist"):
            RemoteRunner(url, "tok").run(_request())
    finally:
        server.shutdown()
