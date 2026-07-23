---
id: 034
title: "Migrate Gemini 2.5 → 3.x before the 2026-10-16 retirement"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

**Reframed 2026-07-23** as a code/config migration. The eval gate is gone: the
user ruled GCP/Vertex provisioning and all live eval measurement out of scope
(the app assumes a correctly configured environment), so
[Establish baselines and promote the gates](032-establish-baselines.md) is closed
out of scope and no longer blocks this. Ticket 007 decision 4's "upgrades are
eval-gated" cannot be honoured without a suite that runs, so for this effort the
migration is a straight pinned-string bump verified against the assumed-configured
environment by whoever runs it — not a metric-gated move. The judge migration
(`evals/config/judge.toml`) is also out of scope now that the eval judge is; this
ticket owns only the **production** tier config, `config/model_tiers.toml`.

**Hard external deadline: Gemini 2.5 retires on Vertex 2026-10-16.** Every
production LLM node is pinned to `gemini-2.5-pro` / `gemini-2.5-flash`
(`config/model_tiers.toml`, version 2 after
[Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md)).
This is dated work, and the date does not move.

The one real gate left is **external**, not a ticket: as of 2026-07-21 the 3.x
models (3 Flash, 3.1 Pro) were **preview-stage**, and the restated pin rule from
ticket 026 admits only the most specific *stable GA* identifier — rejecting
`-preview`, `-exp`, `-latest`. So today there is nothing rule-compliant to
migrate **to**. Re-check GA status first; if 3.x has gone GA, the rule resolves
itself and this is a normal tier bump plus a prompt-compat read. If the deadline
approaches with no GA option, the decision this ticket owns is which rule bends
and how it is recorded — a decision for the map, not a silent config edit.

## Comments

### GA re-check 2026-07-23 — external gate still closed, no config change

Re-checked Vertex GA status (the one real gate). **Held, no bump.** Findings:

- **Pro tier (the crux) is still Preview.** The Vertex AI release notes carry
  no GA announcement for any 3.x model; `gemini-3.1-pro` is Preview (Feb 19),
  `gemini-3-flash` public preview (Dec 17), `gemini-3.1-flash-lite` public
  preview (Mar 3). The Model Garden ID for 3 Pro is `gemini-3-pro-preview` —
  the `-preview` suffix ticket 026's rule explicitly rejects. There is no
  rule-compliant *stable GA* Pro string to pin to on Vertex.
- **One new data point since the 2026-07-21 reframe:** `gemini-3.6-flash`
  appeared (~2026-07-21) on the **Gemini Enterprise Agent Platform** surface,
  and a pricing schedule refers to "GA Gemini 3 and later families" effective
  2026-07-01. But (a) that surface is not the same as a *Vertex* GA label, which
  the release notes don't corroborate, and (b) even a GA Flash would not unblock
  the migration, since the tiers move as a coherent pair (ticket 007) and Pro
  has no compliant target. A Flash-only partial bump is unverifiable here (no
  creds — live-Vertex measurement is out of scope) and mixing a 3.x Flash with a
  2.5 Pro is not worth it. So no partial move.
- A lifecycle-page listing that appeared to mark `gemini-3-pro`/`-3.6-flash` as
  "Stable/GA" was suffix-guessing by the doc summarizer and is contradicted by
  two independent reads of the release notes; not trusted for a production pin.

**Disposition:** the situation is materially unchanged from the reframe — no
compliant GA 3.x on Vertex — so the rule does not bend yet (deadline
2026-10-16 is ~12 weeks out, GA can still land first). Ticket returned to the
frontier (open, unclaimed) as a periodic external-gate re-check. **Next check:**
watch the Vertex AI release notes for a *stable GA* 3.x **Pro** identifier (bare
`gemini-3-pro`, no `-preview`). When it lands this becomes a mechanical tier bump
(`config/model_tiers.toml`, version 3) + prompt-compat read. If ~2026-09 arrives
with still no GA option, escalate the which-rule-bends decision to the map before
the 2026-10-16 retirement.

### Ruled out of scope 2026-07-23 (user decision) — closed, not resolved

**User call: do not consider Gemini 3.x; design for 2.x only.** That removes this
ticket's reason to exist — it was the forced 2.5→3.x migration — so it is
**closed as out of scope**, not resolved (no route step; it leaves one line in
the map's Out of scope section, not Decisions-so-far). The GA re-check above is
now moot.

Consequence, recorded not blocked: **Gemini 2.5 retires on Vertex 2026-10-16.**
Designing for 2.x only means the pinned defaults stay `gemini-2.5-pro` /
`gemini-2.5-flash`, and this effort does **not** own keeping them current past
that retirement. That is consistent with the standing posture that the app
"assumes a correctly configured environment" (live-Vertex provisioning already
out of scope) — model currency is an ops/env concern, and the tier strings are
overridable at deploy time via `STRIDE_MODEL_PRO` / `STRIDE_MODEL_FLASH` without
an image rebuild. No config strings change; `config/model_tiers.toml` keeps 2.5.
