#!/bin/bash
# Alternate before and after extraction sweeps; stop when charges reach the cap.
# Set CANDIDATE_PROMPTS to the prompts directory of a candidate worktree.
cd /home/me/work-agent || exit 1
unset ANALYSIS_OFFLINE
export ANALYSIS_TIERS_FILE=$HOME/.config/analysis-service/model_tiers.toml
OUT=evals/runs/20261106T-vendor-claim
CAP=1.05
spent() {
  python3 -c "
import json, glob
total = 0.0
for f in glob.glob('$OUT/*.json'):
    total += sum((json.load(open(f)).get('node_charges') or {}).values())
print(f'{total:.4f}')"
}
for r in 1 2 3 4 5; do
  for arm in before after; do
    total=$(spent)
    if awk "BEGIN {exit !($total >= $CAP)}"; then echo "stop: spent $total"; exit 0; fi
    if [ "$arm" = after ]; then
      export ANALYSIS_PROMPTS_DIR="$CANDIDATE_PROMPTS"
    else
      unset ANALYSIS_PROMPTS_DIR
    fi
    timeout 2400 uv run --no-sync python -m evals.harness.run run --mode extraction \
      --cases-in-flight 3 --accept-cost unknown --out $OUT/$arm-r$r.json > $OUT/$arm-r$r.log 2>&1
    echo "$arm-r$r exit=$? spent=$(spent)"
  done
done
echo "done spent=$(spent)"
