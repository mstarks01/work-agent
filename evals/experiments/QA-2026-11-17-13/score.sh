#!/bin/bash
# usage: score.sh <checkout> <outdir>
T=$1; O=$2; mkdir -p "$O"
for f in /home/me/work-agent/evals/runs/20261024T-sonnet-medium-sweep/[0-9]*.json; do
  UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/me/work-agent/.venv PYTHONPATH=$T/src:$T ANALYSIS_OFFLINE=1 \
    uv run --no-sync python -m evals.harness.run score --corpus "$T/evals/corpus" \
    --out "$O/$(basename "$f")" "$f" > "$O/$(basename "$f").log" 2>&1
  echo "$? $(basename "$f")"
done
