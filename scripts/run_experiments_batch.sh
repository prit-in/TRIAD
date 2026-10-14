#!/usr/bin/env bash
# Re-run experiments one container each, strictly in sequence (no two share the Docker VM).
#
#   bash scripts/run_experiments_batch.sh                          # all ten, in the order below
#   ORDER="EXP-1 EXP-5 EXP-6" bash scripts/run_experiments_batch.sh # just these
#
# EXP-6 kills sandbox containers to inject failures, so it alone gets the docker socket.
# Logs go to experiments/_logs/ (git-ignored); results to experiments/EXP-n/<timestamp>/.
set -u
cd "$(dirname "$0")/.."
LOG=experiments/_logs
mkdir -p "$LOG"
ORDER="${ORDER:-EXP-9 EXP-10 EXP-7 EXP-3 EXP-4 EXP-1 EXP-8 EXP-2 EXP-5 EXP-6}"
for exp in $ORDER; do
  extra=""
  if [ "$exp" = "EXP-6" ]; then
    extra="-v /var/run/docker.sock:/var/run/docker.sock"
    # Its cluster arms need the compose Spark cluster. Created here, not started: EXP-6 starts
    # it for those two arms only and stops it after them, and the stop below is a backstop.
    MSYS_NO_PATHCONV=1 docker compose -f docker/compose.yaml --profile spark create       spark-master spark-worker > "$LOG/$exp.cluster.log" 2>&1
  fi
  # EXP-2 measures where memory runs out. `--no-deps` keeps `compose run` from starting anything
  # beside it; its configuration P needs postgres, mlflow and sandbox-runner already up (DEC-44).
  # The orchestrator is stopped for it: its scheduler started a retrain beside EXP-2's P on
  # 14-10-2026, which competed for the VM's memory and rebuilt the shared JAR under P (DEC-55).
  if [ "$exp" = "EXP-2" ]; then
    extra="--no-deps"
    MSYS_NO_PATHCONV=1 docker compose -f docker/compose.yaml stop orchestrator >> "$LOG/$exp.services.log" 2>&1
  fi
  name="triad-exp-$(echo "$exp" | tr 'A-Z' 'a-z')-$(date -u +%Y%m%dT%H%M%S)"
  started="$LOG/.$exp.started"
  touch "$started"
  echo "$(date -u +%FT%TZ) start $exp" | tee -a "$LOG/progress.txt"
  # shellcheck disable=SC2086
  MSYS_NO_PATHCONV=1 docker compose -f docker/compose.yaml --profile tools run --rm -T --name "$name" $extra dev \
    python scripts/run_experiment.py "$exp" > "$LOG/$exp.log" 2>&1
  code=$?
  # A compose client that loses the engine exits 125 ("unexpected EOF") while the experiment
  # goes on in its container and writes its own result (11-10 and 14-10-2026): wait for it.
  # Only "not running" or "no such container" ends the wait - an engine too busy to answer
  # says nothing about the container, and taking it for an end let 14-10's batch finish early.
  while :; do
    state=$(docker inspect -f '{{.State.Running}}' "$name" 2>&1)
    case "$state" in
      false|*"No such object"*|*"No such container"*) break ;;
      *) sleep 30 ;;
    esac
  done
  if [ "$exp" = "EXP-6" ]; then
    MSYS_NO_PATHCONV=1 docker compose -f docker/compose.yaml --profile spark stop       spark-master spark-worker >> "$LOG/$exp.cluster.log" 2>&1
  fi
  if [ "$exp" = "EXP-2" ]; then
    MSYS_NO_PATHCONV=1 docker compose -f docker/compose.yaml start orchestrator >> "$LOG/$exp.services.log" 2>&1
  fi
  verdict=$(tr '\r' '\n' < "$LOG/$exp.log" | grep -E "^\s+status=" | tail -1)
  if [ -z "$verdict" ]; then
    # The log ends where the client lost the engine; the experiment's own result file does not.
    # Only a result written after this start is this run's: latest.json may be the last run's.
    result=$(find "experiments/$exp" -mindepth 2 -maxdepth 2 -name result.json -newer "$started" 2>/dev/null | sort | tail -1)
    if [ -n "$result" ]; then
      verdict="$result: $(grep -oE '"(status|hypothesis_held)": [^,]*' "$result" | head -2 | tr '\n' ' ')"
    else
      verdict="no result written"
    fi
  fi
  echo "$(date -u +%FT%TZ) done  $exp exit=$code $verdict" | tee -a "$LOG/progress.txt"
done
echo "$(date -u +%FT%TZ) all done" | tee -a "$LOG/progress.txt"
