---
id: 024
title: "Wire the eval suite into GitHub Actions CI with WIF"
label: wayfinder:task
status: open
assignee:
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
