# Live-eval credentials: GitHub OIDC → Workload Identity Federation

One-time setup that `.github/workflows/evals-live.yml` assumes: how a CI run
gets short-lived Google Cloud credentials without any secret being stored in
this repository.

The same four variables enable the Vertex lane of
`.github/workflows/provider-smoke.yml`, which is the cheaper of the two and the
one to watch first: it runs one small job rather than the thirteen-case corpus, so
it answers "does Vertex serve this graph" for cents, on every merge to `main`.

This covers **Vertex only**, because that is what `evals-live.yml` selects in
its `ANALYSIS_MODEL_*` block. The shipped configuration selects no vendor at all
— see [Configuration](../docs/Configuration.md#models-and-vendors) — so the
workflow names one, and this setup follows that choice rather than the other way
round. Point those variables at Anthropic or OpenAI and the CI credential
becomes an API key in a repository secret, with none of the federation setup
below applying to it.

**Zero long-lived secrets.** No service-account JSON key is created, downloaded,
or stored in this repository at any point. A key is a permanent credential
sitting in a settings page; the federated flow mints one that lasts minutes and
is scoped to a single workflow run. If any step below tempts you toward
`gcloud iam service-accounts keys create`, the setup has gone wrong.

The offline job in `ci.yml` needs none of this and must never be granted it.

## The trust model

**No credential-bearing job runs mutable pull-request-head code.** That is the
one rule this setup exists to keep, and every choice below follows from it.

The threat is a *collaborator*, not a fork. Fork pull requests get no secrets
and were always skipped, which is what made a `pull_request` trigger look
sufficient. It is not. Anyone who can push a branch to this repository and open
a pull request can edit the application code a live lane imports, or edit the
lane's own workflow file, and have the edit run while the job holds the Vertex
identity or a provider key. The OIDC token minted for that run carries the same
`repository` claim as one minted on `main`, so repository-scoped federation
admits it.

Two layers close it, and either one closes it alone:

1. **The workflows.** No credential-bearing job triggers on `pull_request`.
   `evals-live.yml` and `provider-smoke.yml` run on `push` to `main`;
   `evals-live-api-key.yml` is dispatch-only. All three carry
   `if: github.ref == 'refs/heads/main'` on the job, because a
   `workflow_dispatch` names its own ref and would otherwise reach an
   unreviewed branch. `pull_request_target` is prohibited outright.
2. **The federation.** The attribute condition pins `job_workflow_ref` to the
   trusted ref, so only a workflow file already on `main` can exchange a token
   at all — see section 3.

Two layers rather than one because layer 1 lives in files a collaborator can
edit in a pull request, and layer 2 does not.

`tests/test_workflow_lints.py` enforces both halves of layer 1 against every
workflow in the directory, so a lane added later is covered without anyone
remembering to come back here.

What a pull request gets instead is `ci.yml`'s offline conformance suite. It
exercises Vertex, Anthropic and OpenAI equally and holds no credential, so it
establishes what each provider would be asked for. The live lanes answer the
second question one merge later.

The API-key jobs additionally name a `live-providers` environment, so a
deployment can require reviewers and restrict deployment branches from the
repository settings. That is a second layer behind the ref guard, not a
replacement: an environment nobody has configured protection rules on protects
nothing.

## 0. The repository

The repository already exists at
[`mstarks01/work-agent`](https://github.com/mstarks01/work-agent) and `origin`
points at it; everything below pins to that full name. If you are standing this
setup up somewhere else, create the repository first:

```sh
gh repo create <OWNER>/<REPO> --private --source=. --remote=origin --push
```

Keep it **private** and accepting **no fork contributions**. The workflow skips
fork PRs rather than authenticating them, and `pull_request_target` is
prohibited here (see the header of `evals-live.yml` for why).

## 1. Substitutions

| Placeholder | Meaning | Supplied as |
|---|---|---|
| `<PROJECT_ID>` | the GCP project billing the evals | `--project-id` |
| `<LOCATION>` | Vertex region, e.g. `us-central1` | `--location` |
| `<OWNER>/<REPO>` | the GitHub repository full name, e.g. `mstarks01/work-agent` | `--repo`, else `origin` |
| `<PROJECT_NUMBER>` | numeric id of that project | derived from `<PROJECT_ID>` |

## 2. Run the setup

```sh
.github/scripts/setup-workload-identity.sh \
  --project-id <PROJECT_ID> --location <LOCATION>
```

`--repo` defaults to whatever `origin` resolves to, and `<PROJECT_NUMBER>` is
read from the project rather than typed — it is the field most often
transcribed wrong, and getting it wrong yields a provider that authenticates
nothing with no obvious cause. Pass `--dry-run` to print the exact commands
without executing them.

The script is idempotent: it describes each resource before creating it, so
re-running converges rather than erroring. Sections 3–5 below explain what it
does and why; the commands themselves live only in the script, so that what is
documented and what is run cannot drift apart.

## 3. What it creates: pool and provider, pinned to this repository

A workload identity pool `github` and an OIDC provider `github-actions`,
issuer `https://token.actions.githubusercontent.com`, carrying this attribute
condition:

```
assertion.repository == '<OWNER>/<REPO>' && assertion.job_workflow_ref.endsWith('@refs/heads/main')
```

That condition is **load-bearing and not optional**. Without it the provider
trusts every OIDC token GitHub issues to anyone — any repository on github.com
could then exchange its token for these credentials. The condition must name
the repository, not just the owner: `repository_owner` alone still admits every
repo under the account, including one an attacker gets a workflow merged into.

**The repository claim is where this condition used to stop, and it is not
enough.** A collaborator's pull-request branch produces a token whose
`repository` claim is identical to `main`'s, so repository scoping federates
reviewed and unreviewed code alike. `job_workflow_ref` is the claim that tells
them apart: it names the ref of the *workflow definition* GitHub is running —
`<OWNER>/<REPO>/.github/workflows/<file>@<REF>` — so pinning its suffix admits
only workflow files already on the trusted ref. It covers a reusable workflow
called from elsewhere for the same reason, because the claim names the file
that is executing.

Pass `--trusted-ref` to name a different ref. It is a full git ref rather than a
branch name, because that is what the claim carries.

Re-running with a different `--repo` or `--trusted-ref` updates the condition
rather than leaving the old one trusted.

### What this rejects

| Token from | `repository` | `job_workflow_ref` | Admitted |
| --- | --- | --- | --- |
| `push` to `main` | matches | `…@refs/heads/main` | yes |
| `workflow_dispatch` on `main` | matches | `…@refs/heads/main` | yes |
| a collaborator's pull request | matches | `…@refs/heads/<branch>` | **no** |
| `workflow_dispatch` on a branch | matches | `…@refs/heads/<branch>` | **no** |
| another repository | differs | — | **no** |

## 4. What it creates: a separate eval service account

`analysis-evals@<PROJECT_ID>.iam.gserviceaccount.com`, holding
`roles/aiplatform.user` and nothing else, impersonable only by this
repository's federated principals via `roles/iam.workloadIdentityUser`.

Separate from whatever identity deploys to Cloud Run. CI runs model inference;
it has no business deploying, reading buckets, or minting tokens. If a future
job needs another permission, add another service account rather than widening
this one.

It also enables `iamcredentials`, `sts`, and `aiplatform` on the project.

## 5. What it sets: repository variables

Set as **variables**, not secrets — none of these is confidential, and storing
a non-secret as a secret only removes it from log output where it would have
been useful.

| Variable | Value |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-actions` |
| `GCP_EVAL_SERVICE_ACCOUNT` | `analysis-evals@<PROJECT_ID>.iam.gserviceaccount.com` |
| `GCP_PROJECT_ID` | `<PROJECT_ID>` |
| `GCP_LOCATION` | `<LOCATION>` |

Until all four are set, `evals-live.yml` does not attempt to authenticate. Its
preflight step skips the job on `pull_request` with a warning naming the unset
variables, and fails it on `schedule` and `workflow_dispatch` — those asked for
a live run, and a sweep that silently runs nothing is worse than one that
stops. A skipped PR run is therefore the expected state before this setup, not
a symptom of it going wrong.

`provider-smoke.yml`'s Vertex lane reads the same four variables and makes the
same call on every event: it reports itself **unexercised** in the job summary
and stops, rather than failing. It fires on pull requests, which did not ask for
a live run.

## 6. Verify

```sh
gh workflow run "Evals (live Vertex)" -f mode=extraction
```

`extraction` is the cheap mode — it exercises the whole auth path against the
`base` tier without spending six category agents and a critic per case.

## Cost

Confirmed against Vertex list pricing (standard tier, July 2026) for the models
`evals-live.yml` selects — `gemini-2.5-flash` on the `base` tier and
`gemini-2.5-pro` on the `strong` tier:

| Model | Input ≤200K | Output ≤200K |
|---|---|---|
| Gemini 2.5 Pro | $1.25 / 1M | $10.00 / 1M |
| Gemini 2.5 Flash | $0.30 / 1M | $2.50 / 1M |

A full `end-to-end` sweep is thirteen cases × (one `base` extraction + six
`strong` category agents + one `strong` critic). Scoring adds nothing to that:
claim matching is the identity rule, which is code, and no model is asked.
Output tokens dominate at $10/1M, and the eight-way `strong` fan-out is the
whole bill. The original "~$0.30/case" estimate is the right order of magnitude
for a run that stays inside the corpus's 8–20 element sizing, but it is an
estimate, not a quote. **What binds is quota and nondeterminism, not dollars** —
which is why the workflow runs cases one at a time and allows a single live run
at once.

## First-run checklist: the model strings

The model strings in `evals-live.yml` have never been exercised.
An earlier pass corrected them from `gemini-2.5-{pro,flash}-002` — a Gemini
1.5-era naming convention that does not resolve — to the stable identifiers
`gemini-2.5-pro` and `gemini-2.5-flash`, and restated the rule that guards them:
use the most specific *stable* identifier, never `-latest`, `-preview`, or
`-exp`. That correction was made from documentation, not from a run, because
**no run in this repo has ever reached Vertex** — there are no credentials here.

So the first live run is still the first real test of these strings. On the
first run after the setup above:

1. `gh workflow run "Evals (live Vertex)" -f mode=extraction` — exercises the
   `base` tier through the `extract` node.
2. Read the `provenance` block in the run artifact. Each node execution
   records a `served_model` — the provider's own report of which build
   answered. An execution with no served build carries no execution-identity
   fingerprint either, which silently removes the record that makes a result
   reproducible.

The ≥90% agreement bar is **not** part of this checklist. It prices the identity
rule against the recorded labels, and `python -m evals.harness.run calibrate`
runs it offline against the repository: no workflow, no provider, no
credentials.

A `404 Publisher Model not found` in step 1 is a config bug, not a CI bug.

**Deadline:** Gemini 2.5 retires on Vertex **2026-10-16**. The 3.x models were
still preview-stage as of 2026-07-21 and so fail the pin rule. Moving generation
means re-baselining every tracked metric — see
[TUNING](../evals/TUNING.md) for that loop.
