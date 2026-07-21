---
id: 035
title: "Create the GitHub repository and land the first CI run"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Step 0 of `.github/WORKLOAD_IDENTITY.md`, split out of
[Provision live infrastructure](027-provision-live-infrastructure.md) because it
is the one part of that ticket that needs no GCP project and no credentials.

Until this lands, `git remote -v` is empty, no workflow in `.github/` has ever
executed, and every WIF identifier in the runbook is unpinnable — the provider's
`--attribute-condition` names the repository's full name, so the repository has
to exist before anything downstream can be written down.

Create the repository private, push `main`, and confirm `ci.yml` — the offline
job, which needs no credentials by design — actually runs.

## Resolution

**Repository: `mstarks01/work-agent`** — private, https://github.com/mstarks01/work-agent.
Created with the runbook's own command (`gh repo create ... --private --source=.
--remote=origin --push`); `origin` is wired and `main` tracks it. 151 tracked
files pushed at `5c5fd3c`.

**`ci.yml` executed and passed** — run 29859067040, "Offline suite (no
credentials)", green in 21s on the push to `main`. This is the first workflow
execution in the repo's history, and it confirms the offline suite's central
claim: 399 passed / 1 skipped reproduces on a runner with no ADC, no
`GOOGLE_*`, and no `STRIDE_MODEL_*` set. The runbook's step 5 also wants it
green **on a PR**; that trigger is still unexercised and rides with the first
PR opened.

**"No fork contributions" is satisfied by construction, not by the setting.**
`PATCH /repos/... allow_forking=false` returns 422 — the flag is only settable
on *org-owned* private repositories, and this is user-owned. The reported value
stays `true` and cannot be changed. It is not a hole: a private user-owned repo
can only be forked by an account already granted access to it, so the fork-PR
path `evals-live.yml` skips has no unauthenticated origin to arrive from. If
this repo ever moves under an org, set the flag there — the runbook's wording
implies a toggle that does not exist at this ownership level.

**Two workflows are registered and un-run:** "Evals (live Vertex)"
(317655376) and "Dependency Graph" (317655417). The former stays un-run until
[Provision live infrastructure](027-provision-live-infrastructure.md) supplies
the four repository variables — it has no credentials to authenticate with, and
that is the correct state, not a failure.

Facts later tickets pin to: repository full name `mstarks01/work-agent`; the
`principalSet://` member and `--attribute-condition` in the runbook both take
that exact string.
