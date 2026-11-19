#!/bin/bash
# Run STRIDE analysis cases one at a time; stop before the total could pass the cap.
S=/tmp/claude-1000/-home-me-work-agent/184c3cc5-734c-4c1c-8628-134c09c7ad85/scratchpad
CAP=3.00
cd $S/wt-run
export ANALYSIS_TIERS_FILE=$HOME/.config/analysis-service/model_tiers.toml ANALYSIS_ASSERTIONS=false UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/me/work-agent/.venv PYTHONPATH=$S/wt-run/src:$S/wt-run
spent() { python3 - "$S/paid" <<'PY'
import json,glob,sys
c=[sum(json.load(open(f))['node_charges'].values()) for f in sorted(glob.glob(sys.argv[1]+'/[0-9][0-9].json'))]
print(f"{sum(c):.4f} {max(c):.4f}")
PY
}
for case in 12-overclaiming-supplier-portal 09-cookbook-sokify-retail 13-dispatch-control-plane 01-payments-checkout 08-sso-identity-broker 10-cookbook-generic-cms 11-sparse-shift-scheduling; do
  read total top < <(spent)
  if python3 -c "import sys; sys.exit(0 if $total + 1.5*$top > $CAP else 1)"; then
    echo "STOP before $case: spent \$$total, largest case \$$top"; break
  fi
  n=${case:0:2}
  echo "START $case at spent \$$total"
  timeout 1800 uv run --no-sync python -m evals.harness.run run --mode analysis --framework stride --case $case --accept-cost unknown --out $S/paid/$n.json > $S/paid/$n.log 2>&1
  rc=$?; echo "END $case exit $rc"
  [ $rc -ne 0 ] && { echo "STOP: case failed"; break; }
done
read total top < <(spent); echo "TOTAL \$$total"
