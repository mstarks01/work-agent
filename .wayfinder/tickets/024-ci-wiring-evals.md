---
id: 024
title: "Wire the eval suite into GitHub Actions CI with WIF"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
blocked-by: [023]
---

## Question

Stand up CI for this repo — there is currently **no `.github/` and no git remote** — and wire in the eval suite per [Golden-case eval suite design](009-eval-suite-design.md) decision 17.

**The offline/live split is the load-bearing structure**, not an optimization:

- **Offline job** — the existing pytest suite plus the eval scorer's unit tests over ticket 022's labelled pairs, schema lints, reference resolution, severity arithmetic. **No credentials.** Runs on every PR.
- **Live job** — real Vertex calls through the shipped `build_pipeline()`. Runs on a credentialed trigger only.

**Auth**: GitHub OIDC → Workload Identity Federation → short-lived credentials, **zero long-lived secrets**. A **separate eval service account** from the deploy identity, holding only `roles/aiplatform.user`, with the WIF pool's attribute condition pinned to this repository. Vertex is IAM-authenticated — API keys are the AI Studio surface and cannot serve ticket 007's pinned regional model strings, so there is no key-based alternative that keeps Vertex. Nothing in `src/` reads `GOOGLE_*` or constructs a client (ADK uses ADC implicitly), so **no service code change is needed to authenticate**.

**`pull_request_target` is refused.** Combined with a PR-head checkout it runs untrusted code holding the Vertex identity — a credential-exfiltration primitive. The repo takes no fork contributions, so `pull_request` with `id-token: write` is sufficient and this is a standing prohibition, not a workaround to revisit.

**Triggers**:

- live evals **path-filtered** to `prompts/**`, `skills/**`, `config/**`, and `src/stride_service/{graph,prompts,skills,critic,pipeline,report,system_model}.py` — `report.py` and `system_model.py` count as agentic because they define the schemas bound as node output schemas
- a **scheduled full sweep** as the backstop, because a path filter can always be fooled
- `workflow_dispatch` for on-demand runs

**Gating**: Tier 1 structural checks fail the build. Must-find recall and all Tier 3 metrics are reported as run artifacts only (decision 16 phase-in). Bounded concurrency across cases — they are independent, but quota is shared.

Confirm actual Vertex pricing while wiring this up; ticket 009 decision 18's ~$0.30/case is an order-of-magnitude estimate that should not be quoted as fact.

## Answer

Shipped `.github/`: two workflows and the WIF runbook. The offline/live split is expressed as **two files, not two jobs** — `ci.yml` carries `permissions: {}` at the top and grants only `contents: read`, so there is no path by which a credential-free job acquires `id-token: write` through a later edit. That is the whole security boundary, and a file boundary is harder to erode than a `needs:` edge.

**`ci.yml` (offline, every PR + push to main).** uv-based: `uv sync --locked` (installs the reviewed lockfile rather than re-resolving), `evals/verify_corpus.py`, then `pytest -q`. Verified locally exactly as CI runs it: corpus lints clean over 6 cases, **395 passed / 1 skipped**. The scorer's replay over ticket 022's 129 recorded judge labels rides inside that suite, which is what makes the judged half of the eval credential-free on PRs.

**`evals-live.yml`.** Path filter as specified plus the workflow file itself. `pull_request_target` refused in the file header as a standing prohibition with its reasoning attached, and fork PRs are *skipped* (`github.event_name != 'pull_request' || head.repo.full_name == github.repository`) rather than authenticated. Auth is `google-github-actions/auth` → external-account ADC; no service-code change was needed, as predicted. Every third-party action is **pinned by commit SHA** with the tag in a trailing comment — a mutable tag is remote code execution holding this job's OIDC token.

**Two departures from the ticket, both deliberate:**

1. **No per-case matrix.** The ticket asked for bounded concurrency across cases; the harness's `command_run` scores all selected cases in one process and computes `exemplar_delta` across them, so a matrix would split the near/far delta into six artifacts that no longer contain it. Cases run serially inside the CLI (bounded concurrency = 1) and the *real* quota guard is `concurrency: group: evals-live` with `cancel-in-progress: false` — quota is shared repo-wide, so "one live run at a time" is the constraint that binds, and cancelling mid-run spends the money while keeping none of the result.
2. **The judge-calibration run is wired here**, on the scheduled sweep and a `workflow_dispatch` boolean, not on the PR trigger. Its failure blocks a *judge* change, not a prompt change, and it costs judge calls. This gives [Eval phase 2](025-eval-phase-2.md)'s never-executed ≥90% bar a trigger to run from.

**Pricing confirmed** (Vertex standard tier, July 2026): Pro $1.25/1M in, **$10/1M out**; Flash $0.30/1M in, $2.50/1M out. Output on the eight-way `pro` fan-out is the entire bill. Ticket 009 decision 18's ~$0.30/case is the right order of magnitude at the corpus's 8–20 element sizing but should not be quoted as fact — and it remains true that **quota and nondeterminism bind, not dollars**.

**Two things CI cannot do anything about, both now written down in `.github/WORKLOAD_IDENTITY.md`:**

- **There is still no git remote.** Step 0 of the runbook creates it; until then these workflows are files that have never executed. The runbook is the HITL checklist — pool and provider with the `assertion.repository ==` attribute condition (spelled out as load-bearing, and why `repository_owner` alone is insufficient), a `stride-evals` service account holding `roles/aiplatform.user` and nothing else, `roles/iam.workloadIdentityUser` scoped to this repo's `principalSet`, and four repository **variables** (not secrets — none is confidential, and hiding a non-secret only removes it from logs where it was useful). No SA key is created at any point.
- **The pinned model strings are unverified and probably wrong.** `gemini-2.5-pro-002` / `gemini-2.5-flash-002` could not be corroborated: Gemini 2.5's published GA identifiers carry no numbered suffix, and `-002` is the retired 1.5 generation's convention. Nothing in this repo has ever spoken to Vertex, so the first live run is also the first test of those strings. Raised as [Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md), which now blocks [Eval phase 2](025-eval-phase-2.md) — baselines measured against a model that 404s are not baselines.
