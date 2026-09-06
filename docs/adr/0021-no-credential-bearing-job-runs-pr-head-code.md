# 21. No credential-bearing job runs pull-request-head code

- **Status**: accepted
- **Date**: 2026-09-01
- **Effort**: [#508 — Prevent PR-head code from receiving live provider
  credentials](https://github.com/mstarks01/work-agent/issues/508)

## Context

Three workflows hand a job live provider authority. `evals-live.yml` and the
Vertex lane of `provider-smoke.yml` hold `id-token: write`, which they exchange
for short-lived Vertex credentials. `evals-live-api-key.yml` and the API-key
lane of `provider-smoke.yml` mount `secrets.ANALYSIS_*_API_KEY`.

Two of them triggered on `pull_request`. Both skipped fork pull requests, and
both said so in their headers: this repository takes no fork contributions, a
fork gets no secrets, so a fork lane could only report itself unexercised.

**The fork check is not the boundary.** A collaborator who can push a branch and
open a pull request can edit `src/analysis_service/**`, which every one of these
lanes imports, or edit the lane's own workflow file. The edit then executes
while the job holds the credential. The OIDC token minted for that run carries
the same `repository` claim as one minted on `main`, so the federation's
repository-scoped attribute condition admits it. Nothing between the branch push
and the provider call distinguishes reviewed code from unreviewed code.

`evals-live-api-key.yml` had no `pull_request` trigger and looked exempt. It was
not: `workflow_dispatch` names its own ref, so anyone with write access could
dispatch it against an unreviewed branch and reach the same place with one extra
click.

## Decisions

### The rule is about the code, not about the trigger

**No credential-bearing job may execute mutable pull-request-head code.**

Stated that way rather than as "do not use `pull_request`", because the trigger
list is a symptom. `pull_request_target` was already prohibited for this reason;
plain `pull_request` reaches the same place by a longer road, and
`workflow_dispatch` reaches it by a third. A rule naming triggers goes stale the
moment a fourth one exists.

### Live lanes move to the trusted ref, and pull requests keep the offline suite

`evals-live.yml` and `provider-smoke.yml` trigger on `push` to `main`, keeping
their existing path filters. `evals-live-api-key.yml` stays dispatch-only. All
three carry `if: github.ref == 'refs/heads/main'` on the credential-bearing job,
written on the job rather than left to the trigger list — so adding a trigger
cannot quietly widen what runs with that job's identity.

What a pull request gets instead is `ci.yml`'s offline conformance suite, which
exercises every vendor equally and holds no credential at all.
It establishes what each provider *would be asked for*; the live lanes answer
the second question one merge later. That split already existed and is what
makes the move affordable: no pull request loses provider-adapter coverage,
only the live confirmation.

Moving the smoke from per-pull-request to per-merge also *reduces* what it
spends, because a `pull_request` trigger fires on every push to the branch and
`push` on `main` fires once per merge.

### The federation pins the workflow's ref, not just the repository

`assertion.repository == '<OWNER>/<REPO>'` becomes

```
assertion.repository == '<OWNER>/<REPO>'
  && assertion.job_workflow_ref.endsWith('@refs/heads/main')
```

`job_workflow_ref` names the ref of the workflow *definition* GitHub is running,
so pinning its suffix admits only workflow files already on the trusted ref. It
covers a reusable workflow called from elsewhere for the same reason: the claim
names the file that is executing, not the file that called it.

Either layer closes the hole alone. Two exist because the workflow layer lives
in files a collaborator can edit inside a pull request, and the federation layer
does not.

### The rule is a lint, not a paragraph

`tests/test_workflow_lints.py` reads **every** file in `.github/workflows/` and
fails when one grants `id-token: write` or references a non-`GITHUB_TOKEN`
secret while triggering on `pull_request` or `pull_request_target`, or while
carrying no ref guard. It also asserts that `ci.yml` still triggers on
`pull_request` and still holds no credential, because the move is only safe
while that lane exists.

Reading the directory rather than a list of the three files that hold
credentials today is the same choice the path-filter lints in that module
already make: a lane added later is covered without anyone remembering to come
back. The lint found `evals-live-api-key.yml`, which #508 did not name.

## Consequences

- A live provider lane no longer reports on the pull request that changed it.
  The signal arrives one merge later, and a regression is found on `main`. This
  is the cost, and it is accepted: the alternative is a credential reachable
  from an unreviewed branch.
- The API-key jobs name a `live-providers` environment, so a deployment can
  require reviewers and restrict deployment branches from repository settings.
  An environment with no protection rules configured protects nothing, which is
  why the ref guard does not depend on it.
- Re-running `.github/scripts/setup-workload-identity.sh` is required to apply
  the tightened attribute condition. Until it is, layer 1 is the only layer.
- `--trusted-ref` lets a repository whose default branch is not `main` name its
  own. The workflows' `if:` guards spell `refs/heads/main` literally, so such a
  repository must change both.
- **Not addressed here**: what a live lane does with the credential once it
  holds it. Egress from the runner, provider-side spend limits, and the scope of
  the eval service account are separate controls; this decision only bounds
  *which code* gets to hold one.
