#!/usr/bin/env bash
# QA-2026-11-05-02-E4: the fault list of step 1. Two arms on seven fault views
# of case 05: the control runs prompts/recritic.md with control-e4.patch (the
# step 1 list of b8319a8f^ beside today's step 8), the treatment runs it as on
# main. Repeat 1 runs every view once in each arm, control first. Repeat 2
# runs only if repeat 1 spent $0.20 or less.
set -u
S=/tmp/claude-1000/-home-me-work-agent/bf9d1c83-18bb-4da4-8280-cfda1752e71e/scratchpad
CTL=$S/wt-run-e4-ctl
TRT=$S/wt-run-e4-trt
EXP=$S/wt-e4/evals/experiments/QA-2026-11-05-02
RUNS=$EXP/runs
ARTIFACT=/home/me/work-agent/evals/runs/20261024T-sonnet-medium-sweep/05-cookbook-queue-webapp.json
CAP=0.50
VIEWS="I-01:dropped I-01:invented I-01:duplicate-id I-01:verdict-shape
I-01:unresolved-unknown I-02:control-row-missing I-02:control-row-not-shown"
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/me/work-agent/.venv
export ANALYSIS_TIERS_FILE=$HOME/.config/analysis-service/model_tiers.toml
total=0
stop() {
  echo "$1"
  echo "TOTAL $total"
  exit 0
}
for repeat in 1 2; do
  if [ "$repeat" = 2 ] && awk -v t="$total" 'BEGIN { exit !(t > 0.20) }'; then
    stop "NO REPEAT 2: repeat 1 spent $total, more than 0.20"
  fi
  for view in $VIEWS; do
    draft=${view%%:*}
    fault=${view#*:}
    for arm in ctl trt; do
      if [ "$arm" = ctl ]; then wt=$CTL; else wt=$TRT; fi
      name=e4-$arm-$draft-$fault-$repeat
      if awk -v t="$total" -v c="$CAP" 'BEGIN { exit !(t + 0.10 > c) }'; then
        stop "STOP before $name: total $total plus 0.10 passes $CAP"
      fi
      (cd "$wt" && PYTHONPATH=$wt/src:$wt uv run --no-sync python -m evals.harness.run \
        recritic-replay "$ARTIFACT" --case 05-cookbook-queue-webapp \
        --framework stride --faults "$EXP/faults-e4.json" --draft "$draft" \
        --fault "$fault" --accept-cost 0.10 --out "$RUNS/$name.json") \
        > "$RUNS/$name.log" 2>&1
      rc=$?
      spent=$(sed -n 's/^spent: \$\([0-9.]*\) reported.*/\1/p' "$RUNS/$name.log" | tail -1)
      total=$(awk -v t="$total" -v s="${spent:-0}" 'BEGIN { printf "%.4f", t + s }')
      echo "$name rc=$rc spent=${spent:-none} total=$total"
      if grep -qiE '402|credit|insufficient' "$RUNS/$name.log"; then
        stop "STOP after $name: the vendor reported a credit error"
      fi
      if [ "$rc" -ne 0 ] || [ -z "$spent" ]; then
        stop "STOP after $name: rc $rc or no reported charge"
      fi
    done
  done
done
echo "TOTAL $total"
git -C "$CTL" status --short | wc -l
git -C "$TRT" status --short | wc -l
