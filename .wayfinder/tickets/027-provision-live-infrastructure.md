---
id: 027
title: "Provision live infrastructure and run the first-run checklist"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

HITL. Nothing in this repository has ever spoken to Vertex, and no workflow in
`.github/` has ever executed. This ticket is the one that changes that; it
blocks every measurement ticket split out of [Eval phase 2](025-eval-phase-2.md).

Concretely, as of 2026-07-21 on the development box: `git remote -v` is empty,
`gcloud` is not installed, there is no ADC file, and no `GOOGLE_*` /
`STRIDE_MODEL_*` variable is set. The offline suite is 399 passed / 1 skipped
against scripted `BaseLlm` stand-ins, which is exactly as far as credential-free
work reaches.

The checklist is already written and must be followed rather than reinvented:
`.github/WORKLOAD_IDENTITY.md`, authored by
[Wire the eval suite into GitHub Actions CI with WIF](024-ci-wiring-evals.md).

1. **Create the GitHub repository** (step 0 of the runbook). Private, no fork
   contributions. Everything downstream pins to its full name.
2. **Wire the GCP project** — project id, number, and Vertex region — and
   install `gcloud` locally with ADC, so tickets can be worked from this box and
   not only from CI.
3. **Pool, provider, service account, bindings** — runbook steps 2-4. The
   provider's `--attribute-condition` naming the exact repository is
   load-bearing; `repository_owner` alone is insufficient. **No service-account
   key is created at any point** — if a step tempts toward
   `gcloud iam service-accounts keys create`, the setup has gone wrong.
4. **Run the first-run checklist**, which includes the live model-string check
   inherited from [Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md).
   `gemini-2.5-pro` / `gemini-2.5-flash` were corrected on documentary evidence
   only. A 404 here makes every sweep in the split-out tickets impossible, so
   this is the first thing that must actually resolve.
5. **Confirm both workflows execute** — `ci.yml` green on a PR, `evals-live.yml`
   reaching Vertex via OIDC with no static secret.

Resolved when a live Vertex call succeeds from both CI and this box, and the
answer records project id, region, service-account email, and the served model
version each pinned string resolved to.
