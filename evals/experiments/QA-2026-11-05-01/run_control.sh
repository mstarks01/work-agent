#!/usr/bin/env bash
# QA-2026-11-05-01-E1: the control arm, one information-disclosure lane call per case.
set -u
S=/tmp/claude-1000/-home-me-work-agent/bf9d1c83-18bb-4da4-8280-cfda1752e71e/scratchpad
WT=$S/wt-ctl
RUNS=$S/wt-b2/evals/experiments/QA-2026-11-05-01/runs
cd "$WT" || exit 1
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/me/work-agent/.venv
export PYTHONPATH=$WT/src:$WT
export ANALYSIS_TIERS_FILE=$HOME/.config/analysis-service/model_tiers.toml
total=0
for entry in 01-payments-checkout:signed 02-iot-fleet-telemetry:signed \
    03-batch-data-pipeline:signed 04-ml-inference-service:signed \
    05-cookbook-queue-webapp:signed 06-cookbook-online-game:signed \
    09-cookbook-sokify-retail:signed 10-cookbook-generic-cms:signed \
    11-sparse-shift-scheduling:signed 12-overclaiming-supplier-portal:signed \
    13-dispatch-control-plane:signed 14-loyalty-oauth-platform:none \
    15-multitenant-invoicing:none; do
  case_id=${entry%%:*}
  arm=${entry##*:}
  if awk -v t="$total" 'BEGIN { exit !(t + 0.10 > 0.95) }'; then
    echo "STOP before $case_id: total $total plus 0.10 passes 0.95"
    break
  fi
  out=$RUNS/ctl-${case_id:0:2}.json
  log=$RUNS/ctl-${case_id:0:2}.log
  uv run --no-sync python -m evals.harness.run lane-replay \
    --case "$case_id" --lane information-disclosure --framework stride \
    --prepare-today "$arm" --accept-cost 0.10 --out "$out" > "$log" 2>&1
  rc=$?
  spent=$(sed -n 's/^spent: \$\([0-9.]*\) reported.*/\1/p' "$log" | tail -1)
  total=$(awk -v t="$total" -v s="${spent:-0}" 'BEGIN { printf "%.4f", t + s }')
  echo "$case_id arm=$arm rc=$rc spent=${spent:-none} total=$total"
  if grep -qiE '402|credit|insufficient' "$log"; then
    echo "STOP after $case_id: the vendor reported a credit error"
    break
  fi
  if [ "$rc" -ne 0 ] || [ -z "$spent" ]; then
    echo "STOP after $case_id: rc $rc or no reported charge"
    break
  fi
done
echo "TOTAL $total"
git -C "$WT" status --short | wc -l
