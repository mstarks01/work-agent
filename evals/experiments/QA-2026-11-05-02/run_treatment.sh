#!/usr/bin/env bash
# QA-2026-11-05-02-E2, stage 2: the treatment arm. prompts/recritic.md with
# treatment.patch applied in the run worktree,
# six fault views (two drafts x three shapes), two repeats, one re-ask each.
set -u
S=/tmp/claude-1000/-home-me-work-agent/bf9d1c83-18bb-4da4-8280-cfda1752e71e/scratchpad
WT=$S/wt-run-e2
EXP=$S/wt-qa1105-e2/evals/experiments/QA-2026-11-05-02
RUNS=$EXP/runs
ARTIFACT=/home/me/work-agent/evals/runs/20261024T-sonnet-medium-sweep/05-cookbook-queue-webapp.json
CAP=0.30
cd "$WT" || exit 1
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/me/work-agent/.venv
export PYTHONPATH=$WT/src:$WT
export ANALYSIS_TIERS_FILE=$HOME/.config/analysis-service/model_tiers.toml
total=0
for repeat in 1 2; do
  for draft in I-02 R-03; do
    for fault in row-unanswered row-not-shown row-on-other-step; do
      name=trt-$draft-$fault-$repeat
      if awk -v t="$total" -v c="$CAP" 'BEGIN { exit !(t + 0.10 > c) }'; then
        echo "STOP before $name: total $total plus 0.10 passes $CAP"
        echo "TOTAL $total"
        exit 0
      fi
      uv run --no-sync python -m evals.harness.run recritic-replay "$ARTIFACT" \
        --case 05-cookbook-queue-webapp --framework stride \
        --faults "$EXP/faults.json" --draft "$draft" --fault "$fault" \
        --accept-cost 0.10 --out "$RUNS/$name.json" > "$RUNS/$name.log" 2>&1
      rc=$?
      spent=$(sed -n 's/^spent: \$\([0-9.]*\) reported.*/\1/p' "$RUNS/$name.log" | tail -1)
      total=$(awk -v t="$total" -v s="${spent:-0}" 'BEGIN { printf "%.4f", t + s }')
      echo "$name rc=$rc spent=${spent:-none} total=$total"
      if grep -qiE '402|credit|insufficient' "$RUNS/$name.log"; then
        echo "STOP after $name: the vendor reported a credit error"
        echo "TOTAL $total"
        exit 0
      fi
      if [ "$rc" -ne 0 ] || [ -z "$spent" ]; then
        echo "STOP after $name: rc $rc or no reported charge"
        echo "TOTAL $total"
        exit 0
      fi
    done
  done
done
echo "TOTAL $total"
git -C "$WT" status --short | wc -l
