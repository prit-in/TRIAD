"""The container runner's own logic, without a docker engine: path translation, the controls it
asks for, the hard timeout, and the remote runner failing closed. (ST-06/ST-07 against real
containers are in tests/security/test_sandbox_isolation.py.)"""

from __future__ import annotations

import json
import math
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
import requests
from docker.errors import APIError, NotFound

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
    def __init__(self, *, exit_code: int = 0, hang: bool = False,
                 oom_killed: bool = False) -> None:
        self.exit_code, self.hang = exit_code, hang
        self.killed = self.removed = False
        self.name = "triad-sbx-run-1-clean-000000"
        # docker-py fills `attrs` from the engine's inspect; `reload` refreshes it.
        self.attrs = {"State": {"OOMKilled": oom_killed}}

    def reload(self) -> None:
        pass

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
        self.stale: list[_Container] = []   # what an earlier submission left behind
        self.listed: dict | None = None
        self.containers = self
        self.images = self

    def run(self, image, command, **kwargs):  # containers.run
        self.calls.append((image, command, kwargs))
        return self.container

    def list(self, **kwargs):  # containers.list
        self.listed = kwargs
        return list(self.stale)

    def get(self, name):  # containers.get and images.get: nothing by that name
        raise NotFound(name)


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
    assert kw["labels"] == {"triad.run_key": "run-1", "triad.stage": "clean"}
    assert (run_folder, 1000, 1000, False) in chowned, "the run folder is handed to the job's user"
    assert result.ok and result.isolation == "container" and client.container.removed


def test_the_hard_timeout_kills_the_container(monkeypatch) -> None:
    monkeypatch.setitem(rn.RESOURCE_PROFILES, "small", {**rn.RESOURCE_PROFILES["small"],
                                                        "timeout_s": 0})
    client = _Client(_Container(hang=True))
    result = DockerRunner(client=client, own_mounts=OWN_MOUNTS).run(_request())
    assert result.timed_out and result.exit_code == 124 and not result.ok
    assert client.container.killed and client.container.removed


# --- an engine that stalls while the job runs (DEC-52) ----------------------------------------


class _Flaky(_Container):
    """A running job whose engine drops the wait `drops` times; `finished` has the job end
    during the first drop, and the first `silent_reloads` inspections go unanswered."""

    def __init__(self, *, drops: int, exit_code: int = 0, finished: bool = False,
                 silent_reloads: float = 0) -> None:
        super().__init__(exit_code=exit_code)
        self.drops, self.finished, self.waits = drops, finished, 0
        self.silent_reloads, self.reloads = silent_reloads, 0
        self.attrs["State"]["Status"] = "running"

    def wait(self, timeout: int) -> dict:
        self.waits += 1
        if self.waits <= self.drops:
            if self.finished:
                self.attrs["State"].update(Status="exited", ExitCode=self.exit_code)
            raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")
        return {"StatusCode": self.exit_code}

    def reload(self) -> None:
        self.reloads += 1
        if self.reloads <= self.silent_reloads:
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)")


@pytest.fixture()
def no_pauses(monkeypatch):
    monkeypatch.setattr(rn, "ENGINE_RETRY_PAUSE_S", 0.0)
    monkeypatch.setattr(rn, "ENGINE_BACKOFF_S", 0.0)


def test_a_broken_wait_does_not_end_a_job_that_is_still_running(no_pauses) -> None:
    """EXP-2, 11-10-2026: the runner read a dropped wait as the job failing, and its clean-up
    killed a feature job that was still working - twice, 24 minutes in each time."""
    container = _Flaky(drops=2)
    result = DockerRunner(client=_Client(container), own_mounts=OWN_MOUNTS).run(_request())
    assert result.ok and result.exit_code == 0 and container.waits == 3
    assert not container.killed and container.removed


def test_a_job_that_ends_while_the_wait_is_broken_reports_its_own_exit(no_pauses) -> None:
    container = _Flaky(drops=1, exit_code=3, finished=True)
    result = DockerRunner(client=_Client(container), own_mounts=OWN_MOUNTS).run(_request())
    assert not result.ok and result.exit_code == 3 and not result.timed_out
    assert container.waits == 1, "the engine said it had exited; no second wait"


def test_a_silent_engine_is_waited_out_not_given_up_after_three_questions(no_pauses) -> None:
    """Review of 12-10-2026: the runner gave up on a running job after three unanswered
    questions - about 210 s against a stalled engine - long before the job's deadline, and its
    clean-up then killed or orphaned the job."""
    container = _Flaky(drops=2, silent_reloads=4)
    result = DockerRunner(client=_Client(container), own_mounts=OWN_MOUNTS).run(_request())
    assert result.ok and result.exit_code == 0 and container.waits == 3
    assert not container.killed


def test_an_engine_silent_to_the_deadline_is_an_outage_not_the_jobs_failure(
        no_pauses, monkeypatch) -> None:
    monkeypatch.setitem(rn.RESOURCE_PROFILES, "small", {**rn.RESOURCE_PROFILES["small"],
                                                        "timeout_s": 0.3})
    container = _Flaky(drops=10**9, silent_reloads=math.inf)
    started = time.perf_counter()
    with pytest.raises(rn.EngineUnavailable, match="did not answer in 3 attempts"):
        DockerRunner(client=_Client(container), own_mounts=OWN_MOUNTS).run(_request())
    assert time.perf_counter() - started >= 0.3, "the job was waited on to its deadline"
    assert container.waits > 1 and container.removed


def test_a_resubmission_ends_the_copy_an_earlier_submission_left_behind() -> None:
    left_behind = _Container()
    client = _Client(_Container())
    client.stale = [left_behind]
    result = DockerRunner(client=client, own_mounts=OWN_MOUNTS).run(_request())
    assert left_behind.removed, "never two copies of one job on the same paths"
    assert client.listed["filters"] == {"label": ["triad.run_key=run-1", "triad.stage=clean"]}
    assert result.ok


def test_a_create_that_fails_transiently_is_retried_with_backoff(no_pauses) -> None:
    class _BusyClient(_Client):
        def run(self, image, command, **kwargs):  # refused before anything was created
            self.calls.append((image, command, kwargs))
            if len(self.calls) < 3:
                raise requests.exceptions.ConnectionError("Connection aborted")
            return self.container

    client = _BusyClient(_Container())
    result = DockerRunner(client=client, own_mounts=OWN_MOUNTS).run(_request())
    assert result.ok and len(client.calls) == 3


def test_a_container_that_fails_to_start_is_not_left_behind() -> None:
    container = _Container()

    class _RefusingClient(_Client):
        def run(self, image, command, **kwargs):  # created, then refused at start
            self.calls.append((image, command, kwargs))
            r = requests.Response()
            r.status_code = 400
            raise APIError("invalid mount config", response=r)

        def get(self, name):
            if name.startswith("triad-sbx-"):
                return self.container
            raise NotFound(name)

    with pytest.raises(APIError):
        DockerRunner(client=_RefusingClient(container), own_mounts=OWN_MOUNTS).run(_request())
    assert container.removed


def test_a_transient_engine_failure_is_retried_with_backoff_at_most_three_times(
        monkeypatch) -> None:
    """NFR-REL-01: transient failures retry with exponential backoff, at most three attempts."""
    pauses: list[float] = []
    monkeypatch.setattr(rn.time, "sleep", pauses.append)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("Connection aborted")
        return "answered"

    assert rn._engine(flaky) == "answered" and calls["n"] == 3
    assert pauses == [rn.ENGINE_BACKOFF_S, 2 * rn.ENGINE_BACKOFF_S]
    with pytest.raises(rn.EngineUnavailable):
        rn._engine(lambda: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("t")))


def test_a_lost_reply_to_create_finds_the_container_instead_of_making_a_second(no_pauses) -> None:
    container = _Container()
    container.status, container.started = "created", 0

    def start() -> None:
        container.started += 1

    container.start = start

    class _LosingClient(_Client):
        def run(self, image, command, **kwargs):  # created and started, but the reply was lost
            self.calls.append((image, command, kwargs))
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)")

        def get(self, name):  # containers.get finds it; images.get finds no digest
            if name.startswith("triad-sbx-"):
                return self.container
            raise NotFound(name)

    client = _LosingClient(container)
    result = DockerRunner(client=client, own_mounts=OWN_MOUNTS).run(_request())
    assert result.ok and len(client.calls) == 1 and container.started == 1


def test_only_a_failed_conversation_with_the_engine_is_transient() -> None:
    def response(code: int) -> requests.Response:
        r = requests.Response()
        r.status_code = code
        return r

    assert rn._transient(requests.exceptions.ChunkedEncodingError("Response ended prematurely"))
    assert rn._transient(APIError("engine fault", response=response(500)))
    assert not rn._transient(APIError("name in use", response=response(409)))
    assert not rn._transient(NotFound("no such container"))
    assert not rn._transient(ValueError("not the engine"))


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


def test_an_engine_outage_is_a_503_and_is_resubmitted_not_regenerated() -> None:
    from triad_core.errors import ErrorClass, Remedy, classify

    class _Down:
        isolation = "container"

        def run(self, request):
            raise rn.EngineUnavailable("the container engine did not answer for 300 s: ReadTimeout")

    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 make_handler(_Down(), "tok", frozenset({"triad-base:0.1"})))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        with pytest.raises(SandboxError, match=r"refused the job \(503\)") as refused:
            RemoteRunner(url, "tok").run(_request())
    finally:
        server.shutdown()
    diagnosis = classify(f"tool_exception: SandboxError: {refused.value}")
    assert diagnosis.error_class is ErrorClass.SANDBOX_UNAVAILABLE
    assert diagnosis.remedy is Remedy.RESUBMIT


@pytest.mark.parametrize("message", [
    # The failures of run-2026-10-11-032 (EXP-2), as its stages recorded them.
    'tool_exception: SandboxError: sandbox-runner refused the job (500): {"detail": "ReadTimeout: '
    "UnixHTTPConnectionPool(host='localhost', port=None): Read timed out. (read timeout=60)\"}",
    'tool_exception: SandboxError: sandbox-runner refused the job (500): '
    '{"detail": "ChunkedEncodingError: Response ended prematurely"}',
    "tool_exception: SandboxError: sandbox-runner unreachable at http://sandbox-runner:8090: "
    "[Errno 111] Connection refused",
])
def test_the_recorded_engine_failures_are_an_outage_not_a_python_exception(message) -> None:
    from triad_core.errors import ErrorClass, classify

    assert classify(message).error_class is ErrorClass.SANDBOX_UNAVAILABLE


def test_the_engines_own_refusal_is_not_an_outage() -> None:
    from triad_core.errors import ErrorClass, classify

    refusal = ('tool_exception: SandboxError: sandbox-runner refused the job (500): {"detail": '
               '"APIError: 400 Client Error: Bad Request (\\"invalid mount config: subpath does '
               'not exist\\")"}')
    assert classify(refusal).error_class is not ErrorClass.SANDBOX_UNAVAILABLE


def test_a_killed_container_says_who_killed_it() -> None:
    """Exit 137 is the kernel's OOM killer or someone else; the engine knows which."""
    for oom, expected in ((True, "exceeded its memory limit"), (False, "killed from outside")):
        container = _Container(exit_code=137, oom_killed=oom)
        result = DockerRunner(client=_Client(container), own_mounts=OWN_MOUNTS).run(_request())
        assert not result.ok
        assert expected in result.detail


def test_executor_memory_is_set_on_a_cluster_and_never_in_local_mode() -> None:
    """On a cluster the out-of-memory remedy is the executors' memory; in local mode the
    driver is the executor, and an executor setting there would change nothing."""
    base = {"run_key": "r", "stage": "s", "jar": "/data/triad/j.jar",
            "core_jar": "/data/triad/c.jar", "main_class": "M", "job_args": [],
            "executor_memory": "2g"}
    cluster = spark_submit_request(**base, master="spark://spark-master:7077").command
    local = spark_submit_request(**base, master="local[2]").command
    assert "spark.executor.memory=2g" in cluster
    assert not any("spark.executor.memory" in part for part in local)
