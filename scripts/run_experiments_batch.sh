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
  if [ "$exp" = "EXP-6" ]; then extra="-v /var/run/docker.sock:/var/run/docker.sock"; fi
  echo "$(date -u +%FT%TZ) start $exp" | tee -a "$LOG/progress.txt"
  # shellcheck disable=SC2086
  MSYS_NO_PATHCONV=1 docker compose -f docker/compose.yaml --profile tools run --rm -T $extra dev \
    python scripts/run_experiment.py "$exp" > "$LOG/$exp.log" 2>&1
  code=$?
  verdict=$(tr '\r' '\n' < "$LOG/$exp.log" | grep -E "^\s+status=" | tail -1)
  echo "$(date -u +%FT%TZ) done  $exp exit=$code $verdict" | tee -a "$LOG/progress.txt"
done
echo "$(date -u +%FT%TZ) all done" | tee -a "$LOG/progress.txt"
