# Live-eval credentials: GitHub OIDC → Workload Identity Federation

One-time setup that `.github/workflows/evals-live.yml` assumes. Wayfinder
ticket 024, implementing [ticket 009](../.wayfinder/tickets/009-eval-suite-design.md)
decision 17.

**Zero long-lived secrets.** No service-account JSON key is created, downloaded,
or stored in this repository at any point. A key would be a permanent bearer
credential sitting in a settings page; the federated flow mints one that lasts
minutes and is scoped to a single workflow run. If any step below tempts you
toward `gcloud iam service-accounts keys create`, the setup has gone wrong.

The offline job in `ci.yml` needs none of this and must never be granted it.

## 0. The repository itself

At the time of writing this repo has **no git remote**. Create the GitHub
repository first — everything below pins to its full name.

```sh
gh repo create <OWNER>/<REPO> --private --source=. --remote=origin --push
```

Keep it **private** and accepting **no fork contributions**. The workflow skips
fork PRs rather than authenticating them, and `pull_request_target` is a
standing prohibition (see the header of `evals-live.yml`).

## 1. Substitutions

| Placeholder | Meaning |
|---|---|
| `<PROJECT_ID>` | the GCP project billing the evals |
| `<PROJECT_NUMBER>` | numeric id of that project |
| `<OWNER>/<REPO>` | the GitHub repository full name, e.g. `mstarks01/work-agent` |
| `<LOCATION>` | Vertex region, e.g. `us-central1` |

## 2. Pool and provider, pinned to this repository

```sh
gcloud iam workload-identity-pools create github \
  --project=<PROJECT_ID> --location=global \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-actions \
  --project=<PROJECT_ID> --location=global --workload-identity-pool=github \
  --display-name="GitHub Actions OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository == '<OWNER>/<REPO>'"
```

The `--attribute-condition` is **load-bearing and not optional**. Without it the
provider trusts every OIDC token GitHub issues to anyone — any repository on
github.com could then exchange its token for these credentials. The condition
must name the repository, not just the owner: `repository_owner` alone still
admits every repo under the account, including one an attacker gets a workflow
merged into.

## 3. A separate eval service account

```sh
gcloud iam service-accounts create stride-evals \
  --project=<PROJECT_ID> --display-name="STRIDE golden-case evals"

# The only role it gets. Not editor, not owner, not the deploy identity.
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:stride-evals@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Let only this repository's federated principals impersonate it.
gcloud iam service-accounts add-iam-policy-binding \
  stride-evals@<PROJECT_ID>.iam.gserviceaccount.com \
  --project=<PROJECT_ID> \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/attribute.repository/<OWNER>/<REPO>"
```

Separate from whatever identity deploys to Cloud Run, and holding
`roles/aiplatform.user` and nothing else. CI runs model inference; it has no
business deploying, reading buckets, or minting tokens. If a future job needs
another permission, add another service account rather than widening this one.

## 4. Repository variables

Set as **variables**, not secrets — none of these is confidential, and storing
a non-secret as a secret only removes it from log output where it would have
been useful.

```sh
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER \
  --body "projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-actions"
gh variable set GCP_EVAL_SERVICE_ACCOUNT --body "stride-evals@<PROJECT_ID>.iam.gserviceaccount.com"
gh variable set GCP_PROJECT_ID --body "<PROJECT_ID>"
gh variable set GCP_LOCATION --body "<LOCATION>"
```

## 5. Verify

```sh
gh workflow run "Evals (live Vertex)" -f mode=extraction
```

`extraction` is the cheap mode — it exercises the whole auth path against the
`flash` tier without spending six analysts and a critic per case.

## Cost

Confirmed against Vertex list pricing (standard tier, July 2026):

| Model | Input ≤200K | Output ≤200K |
|---|---|---|
| Gemini 2.5 Pro | $1.25 / 1M | $10.00 / 1M |
| Gemini 2.5 Flash | $0.30 / 1M | $2.50 / 1M |

A full `end-to-end` sweep is six cases × (one flash extraction + six pro
analysts + one pro critic), plus the judge's pro calls during scoring. Output
tokens dominate at $10/1M, and the eight-way pro fan-out is the whole bill —
ticket 009 decision 18's "~$0.30/case" is the right order of magnitude for a
run that stays inside the corpus's 8–20 element sizing, but it is an estimate,
not a quote. **What binds is quota and nondeterminism, not dollars**, which is
why the workflow serializes cases and allows one live run at a time.

## Known gap

The pinned model strings in `config/model_tiers.toml` — `gemini-2.5-pro-002`
and `gemini-2.5-flash-002` — could not be corroborated against Vertex
documentation; Gemini 2.5's published GA identifiers carry no `-002` suffix
(that numbering belongs to the retired 1.5 generation). The first live run is
therefore also the first test of those strings, and a `404 Publisher Model not
found` there is a config bug, not a CI bug. Tracked as
[Verify the pinned Vertex model strings resolve](../.wayfinder/tickets/026-verify-pinned-model-strings.md).
