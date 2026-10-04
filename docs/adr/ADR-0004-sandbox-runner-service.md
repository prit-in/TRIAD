# ADR-0004: Generated code runs in containers started by a sandbox-runner service

- Status: accepted
- Date: 04-10-2026
- PRD anchors: DD-04, section 22.2 (controls), section 22.3 (the runner service), 22.4,
  ST-06, ST-07, US-14, FR-056

## Context

PRD 22.3: "Only the runner service can start containers ... No other service mounts the
container-engine socket, because that socket grants host-level control." The compose file said
the same in a comment. The service did not exist.

What ran instead, on every pipeline run until this decision: `get_runner()` found no docker
socket in the orchestrator and returned `LocalRunner`, so each Spark job was a child process of
the worker on the host network, able to reach PostgreSQL, MLflow and the internet. Its results
said `isolation="process"`, which was true; `docs/limitations-and-ethics.md` also said "the
sandbox that runs Spark jobs has no route to the internet ... or the database", which was not.
`DockerRunner`, the implementation of every 22.2 control, shelled out to a `docker` CLI that the
image does not contain, and built its result without two required fields: it had never run.

## Decision

1. **A `sandbox-runner` service** (`sandbox/service.py`, compose service of the same name) is the
   only container with `/var/run/docker.sock`. It holds no database URL, JWT secret or LLM key.
   It accepts `POST /run` with a `SandboxRequest`, checks a bearer token (`TRIAD_SANDBOX_TOKEN`)
   and an image allowlist (`TRIAD_SANDBOX_IMAGES`), and runs the job with `DockerRunner`.
2. **`DockerRunner` uses the Docker SDK** the image already ships. It applies 22.2 as written: user
   1000, `cap_drop=ALL`, `no-new-privileges`, read-only root, size-capped tmpfs, CPU, memory and
   pids limits from the resource profile, and a hard timeout that kills the container.
3. **A local-mode Spark job gets no network at all** (`network_mode=none`), stricter than 22.2's
   internal network: one JVM needs none, and with none it cannot reach the runner either. A
   cluster job would get the `internal: true` sandbox network.
4. **Least-privilege mounts.** A request names paths as the orchestrator sees them; the runner maps
   each onto the volume or bind mount it lives on (volume subpaths need Engine 26+; this host
   runs 29.8). A Spark job is given its input folders and the two JARs read-only, and its run's
   sandbox folder and quarantine partition writable - not the data root. Only paths under the
   data root and `/workspace` can be mounted at all, so the socket can never be handed to a job.
5. **The orchestrator's `RemoteRunner` fails closed.** If the runner cannot be reached, the job does
   not run; it does not fall back to `LocalRunner`. `LocalRunner` remains for environments with no
   runner configured, and still labels its results `isolation="process"`.

## Evidence

`tests/security/test_sandbox_isolation.py` runs real containers through the service. Observed on
04-10-2026 from inside a job: the internet unreachable (`OSError`), `postgres`, `mlflow` and
`sandbox-runner` unresolvable, DNS failing; uid 1000 with `CapEff` 0; the root filesystem, `/etc`
and a read-only input refusing writes; the rest of the data root absent; the run folder and `/tmp`
writable; `pids.max` 256 and `memory.max` 3 GiB; no docker socket visible; the container removed
afterwards. `tests/unit/test_sandbox_runner.py` covers the translation, the refusals, the timeout
and the fail-closed paths without an engine.

## Consequences

- ST-06 and ST-07 are executed rather than asserted, and US-14 is met on this machine.
- The socket is still root-equivalent on the host. PRD 22.3 recommends rootless Docker or Podman for
  the runner host; Docker Desktop is not rootless, and that recommendation is not met here.
- The token is a development default in `docker/compose.yaml`, like the JWT secret; any real
  deployment sets `TRIAD_SANDBOX_TOKEN`.
- Spark's memory moves from the orchestrator to the job container, which is capped by the profile.
