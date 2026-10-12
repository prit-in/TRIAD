"""sandbox-runner: the one process allowed to start containers (DD-04, PRD 22.3).

The orchestrator has no docker socket. It sends each job here as a `SandboxRequest`; this
service checks the caller's token and the image, and starts the job with `DockerRunner`'s
controls. Its environment holds no database credentials and no LLM key, so whoever controls
this process holds the docker socket and nothing else.

    python -m sandbox.service [--port 8090]

The standard library's HTTP server is enough: one caller, one job at a time per pipeline, and a
request that lasts as long as the job it runs.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sandbox.runner import DockerRunner, EngineUnavailable, SandboxError, SandboxRequest

log = logging.getLogger("triad.sandbox")

# A failing Spark job can print megabytes; the caller classifies from the tail.
OUTPUT_CAP = 256 * 1024
MAX_REQUEST_BYTES = 1024 * 1024


def make_handler(runner: Any, token: str, images: frozenset[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._reply(200, {"status": "ok", "isolation": runner.isolation})
            else:
                self._reply(404, {"detail": "not found"})

        def do_POST(self) -> None:
            if self.path != "/run":
                self._reply(404, {"detail": "not found"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                self._reply(401, {"detail": "missing or wrong token"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_REQUEST_BYTES:
                self._reply(413, {"detail": "request body missing or too large"})
                return
            try:
                request = SandboxRequest(**json.loads(self.rfile.read(length)))
                if request.image not in images:
                    raise SandboxError(f"image {request.image!r} is not one this runner starts")
                result = runner.run(request)
            except EngineUnavailable as exc:
                # Not a refusal of the request, and not the job failing: the engine is down.
                log.error("engine unavailable: %s", exc)
                self._reply(503, {"detail": str(exc)})
                return
            except (SandboxError, TypeError, ValueError) as exc:
                log.warning("refused: %s", exc)
                self._reply(422, {"detail": str(exc)})
                return
            except Exception as exc:
                # The engine refusing - a missing input folder, an absent image - is the job's
                # failure to report, not a dropped connection the caller would read as "down".
                log.exception("engine error")
                self._reply(500, {"detail": f"{type(exc).__name__}: {exc}"})
                return
            log.info("%s/%s exit %s in %d ms (%s)", request.run_key, request.stage,
                     result.exit_code, result.duration_ms, result.isolation)
            payload = asdict(result)
            payload["stdout"] = result.stdout[-OUTPUT_CAP:]
            payload["stderr"] = result.stderr[-OUTPUT_CAP:]
            self._reply(200, payload)

        def log_message(self, format: str, *args: Any) -> None:
            log.debug("%s " + format, self.address_string(), *args)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="The TRIAD sandbox runner.")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    token = os.environ.get("TRIAD_SANDBOX_TOKEN", "")
    if not token:
        raise SystemExit("TRIAD_SANDBOX_TOKEN is not set; refusing to start an unauthenticated runner")
    images = frozenset(i for i in os.environ.get("TRIAD_SANDBOX_IMAGES", "triad-base:0.1").split(",") if i)

    runner = DockerRunner()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(runner, token, images))
    log.info("sandbox-runner on :%d; images %s; shares %s", args.port, sorted(images),
             [m["Destination"] for m in runner.own_mounts])
    server.serve_forever()


if __name__ == "__main__":
    main()
