#!/usr/bin/env bash
#
# One-time setup for the live-eval credential path: GitHub OIDC -> Workload
# Identity Federation -> short-lived Google Cloud credentials.
#
# This is the executable form of .github/WORKLOAD_IDENTITY.md sections 2-4.
# The document carries the reasoning; this file carries the commands, so that
# what is documented and what is run cannot drift apart.
#
# NO LONG-LIVED SECRETS. This script never calls
# `gcloud iam service-accounts keys create` and nothing it produces is a
# credential at rest. If you find yourself adding a key here to make something
# work, the setup has gone wrong -- read the document instead.
#
# Idempotent: safe to re-run. Every resource is described before it is created,
# and re-running only converges the repository variables.
#
# Usage:
#   .github/scripts/setup-workload-identity.sh \
#       --project-id  <PROJECT_ID> \
#       --location    us-central1 \
#       [--repo        mstarks01/work-agent] \
#       [--trusted-ref refs/heads/main] \
#       [--dry-run]
#
# --repo defaults to the `origin` remote as gh resolves it.
# --trusted-ref is the ref a credential-bearing workflow must be defined on;
#   only workflow files already on it can federate. Defaults to refs/heads/main.

set -euo pipefail

POOL_ID="github"
PROVIDER_ID="github-actions"
SA_ID="analysis-evals"

# The ref a credential-bearing workflow must be defined on to federate. See the
# attribute condition below for why this is a ref and not a branch name.
TRUSTED_REF="refs/heads/main"

PROJECT_ID=""
LOCATION=""
REPO=""
DRY_RUN=0

die() {
  echo "error: $*" >&2
  exit 1
}

note() { echo "==> $*"; }

# Echoes the command in --dry-run rather than running it. Printing the exact
# argv keeps a dry run reviewable as the thing that would actually execute.
run() {
  if ((DRY_RUN)); then
    printf '  [dry-run]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

while (($#)); do
  case "$1" in
    --project-id) PROJECT_ID="${2:-}"; shift 2 ;;
    --location)   LOCATION="${2:-}";   shift 2 ;;
    --repo)       REPO="${2:-}";       shift 2 ;;
    --trusted-ref) TRUSTED_REF="${2:-}"; shift 2 ;;
    --dry-run)    DRY_RUN=1;           shift   ;;
    # The header comment is the usage text; print it rather than maintaining a
    # second copy that can disagree with it.
    -h|--help)    awk 'NR>2 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "$0"
                  exit 0 ;;
    *)            die "unknown argument: $1" ;;
  esac
done

# ---------------------------------------------------------------- preconditions

command -v gcloud >/dev/null || die "gcloud not found; install the Google Cloud CLI"
command -v gh     >/dev/null || die "gh not found; install the GitHub CLI"

[[ -n "$PROJECT_ID" ]] || die "--project-id is required"
[[ -n "$LOCATION"   ]] || die "--location is required (Vertex region, e.g. us-central1)"

gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . \
  || die "no active gcloud account; run: gcloud auth login"

gh auth status >/dev/null 2>&1 || die "gh is not authenticated; run: gh auth login"

if [[ -z "$REPO" ]]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)" \
    || die "could not resolve the repository; pass --repo <OWNER>/<REPO>"
fi
[[ "$REPO" == */* ]] || die "--repo must be <OWNER>/<REPO>, got: $REPO"

# Derived rather than asked for: the project number is the field most often
# transcribed wrong, and getting it wrong yields a provider that authenticates
# nothing with no obvious cause.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')" \
  || die "cannot read project '$PROJECT_ID' -- check the id and your access"

SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
POOL_PATH="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"
PROVIDER_PATH="${POOL_PATH}/providers/${PROVIDER_ID}"
PRINCIPAL_SET="principalSet://iam.googleapis.com/${POOL_PATH}/attribute.repository/${REPO}"

cat <<EOF

  project      ${PROJECT_ID} (${PROJECT_NUMBER})
  location     ${LOCATION}
  repository   ${REPO}
  pool         ${POOL_ID}
  provider     ${PROVIDER_ID}
  service acct ${SA_EMAIL}
$( ((DRY_RUN)) && echo "
  DRY RUN -- nothing will be created." )
EOF

if ! ((DRY_RUN)); then
  read -rp $'\nProceed? [y/N] ' reply
  [[ "$reply" == [yY] ]] || die "aborted"
fi

# ------------------------------------------------------------------------- APIs

note "Enabling required APIs"
run gcloud services enable \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  aiplatform.googleapis.com \
  --project="$PROJECT_ID"

# --------------------------------------------------- 2. pool and OIDC provider

note "Workload identity pool: ${POOL_ID}"
if gcloud iam workload-identity-pools describe "$POOL_ID" \
     --project="$PROJECT_ID" --location=global >/dev/null 2>&1; then
  echo "  already exists"
else
  run gcloud iam workload-identity-pools create "$POOL_ID" \
    --project="$PROJECT_ID" --location=global \
    --display-name="GitHub Actions"
fi

# The attribute-condition is load-bearing and not optional. Without it the
# provider trusts every OIDC token GitHub issues to anyone, and any repository
# on github.com could exchange its token for these credentials.
#
# IT NAMES TWO THINGS, AND THE REPOSITORY IS ONLY THE FIRST. `repository_owner`
# alone still admits every repo under the account, including one an attacker
# gets a workflow merged into -- so the repository is named. But the repository
# is where this condition used to stop, and #508 is what that missed: a
# collaborator who pushes a branch and opens a pull request produces a token
# whose `repository` claim is identical to main's. Repository scoping cannot
# tell reviewed code from unreviewed code, so on its own it federates both.
#
# `job_workflow_ref` is the claim that can. It carries the ref of the *workflow
# definition* that GitHub is running -- `owner/repo/.github/workflows/x.yml@REF`
# -- so pinning its suffix to the trusted ref admits only workflow files that
# are already on that ref. It covers a reusable workflow called from elsewhere
# for the same reason, because the claim names the file that is executing.
#
# This is defence in depth behind the workflow files themselves, which no longer
# carry a `pull_request` trigger on any credential-bearing job and guard
# `workflow_dispatch` with a ref condition. Either layer alone closes the hole;
# the point of two is that the workflow half lives in a file a collaborator can
# edit in a pull request, and this half does not.
#
# --trusted-ref exists so a repository whose default branch is not `main` can
# say so. It is a full git ref, not a branch name, because that is what the
# claim carries.
ATTRIBUTE_CONDITION="assertion.repository == '${REPO}' && assertion.job_workflow_ref.endsWith('@${TRUSTED_REF}')"

note "OIDC provider: ${PROVIDER_ID}"
if gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
     --project="$PROJECT_ID" --location=global \
     --workload-identity-pool="$POOL_ID" >/dev/null 2>&1; then
  echo "  already exists -- updating the attribute condition to match --repo"
  run gcloud iam workload-identity-pools providers update-oidc "$PROVIDER_ID" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_ID" \
    --attribute-condition="$ATTRIBUTE_CONDITION"
else
  run gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_ID" \
    --display-name="GitHub Actions OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="$ATTRIBUTE_CONDITION"
fi

# -------------------------------------------------- 3. the eval service account

note "Service account: ${SA_EMAIL}"
if gcloud iam service-accounts describe "$SA_EMAIL" \
     --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  already exists"
else
  run gcloud iam service-accounts create "$SA_ID" \
    --project="$PROJECT_ID" --display-name="STRIDE golden-case evals"
fi

# The only role it gets. Not editor, not owner, not the deploy identity. CI
# runs model inference; it has no business deploying, reading buckets, or
# minting tokens. A future job needing more permission gets its own account
# rather than widening this one.
note "Granting roles/aiplatform.user"
run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --condition=None >/dev/null

note "Letting only ${REPO}'s federated principals impersonate it"
run gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="$PRINCIPAL_SET" \
  --condition=None >/dev/null

# ------------------------------------------------------ 4. repository variables

# Variables, not secrets: none of these is confidential, and storing a
# non-secret as a secret only removes it from the log output where it would
# have been useful.
note "Setting repository variables on ${REPO}"
run gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --repo "$REPO" --body "$PROVIDER_PATH"
run gh variable set GCP_EVAL_SERVICE_ACCOUNT       --repo "$REPO" --body "$SA_EMAIL"
run gh variable set GCP_PROJECT_ID                 --repo "$REPO" --body "$PROJECT_ID"
run gh variable set GCP_LOCATION                   --repo "$REPO" --body "$LOCATION"

# ------------------------------------------------------------------- 5. verify

cat <<EOF

Done. Verify with the cheap mode, which exercises the whole auth path against
the base tier without spending six analysts and a critic per case:

  gh workflow run "Evals (live Vertex)" -f mode=extraction --repo ${REPO}

IAM propagation is not instant; a first run inside a minute or so of this
script can fail on permissions and succeed on retry.

Note that this is the first run in this repository ever to reach Vertex, so it
is also the first real test of the model strings in config/model_tiers.toml.
A '404 Publisher Model not found' is a config bug, not a CI bug -- see the
first-run checklist in .github/WORKLOAD_IDENTITY.md.
EOF
