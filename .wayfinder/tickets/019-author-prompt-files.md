---
id: 019
title: "Author agent prompt files and category exemplars"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Write the prompt content decided by [Author agent prompts and few-shot exemplars](013-agent-prompts-exemplars.md) — content first, composition code second (ticket 020 fits the code to this layout, not the reverse).

Deliverables under `prompts/`:

- `extract.md`, `repair.md`, `analyst.md`, `critic.md` — each with exactly the four H2 headings `Role`, `Input`, `Procedure`, `Output`, in order, all non-empty. No JSON in `## Output`: semantics in prose only (good `title` vs `description`, the per-category ID numbering rule, when `mitigations` may be empty).
- `prompts/exemplars/<category>.md` ×6, filenames matching the `StrideCategory` literals exactly (as `skills/stride/*.md` already do). Three exemplars each — canonical, second-order, unknown-conditional — one H2 per exemplar using its unique name, each section containing exactly one fenced `json` block that parses as `DraftThreat`. Wrong-perspective contrast goes in the prose body only, never as JSON.
- One shared miniature system model in `analyst.md`'s `## Input`: a handful of elements and flows spanning a trust boundary, whose element/flow IDs all eighteen exemplars cite.
- `analyst.md` leaves the category as an ADK state placeholder — no per-category copies.
- `extract.md` carries two inline exemplars (sparse input → heavy `unknown`; a legitimate inference recorded in `assumptions`).

Token caps to write against: `analyst.md` ≤2000, each exemplar file ≤1500, `critic.md` ≤1500, `extract.md` ≤1500, `repair.md` ≤800.

The critic prompt's `## Procedure` runs the five judgment steps in fixed order (evidence → lane → duplicate → severity → confidence, 1-3 gating), and must not ask the LLM to perform any mechanical check that ticket 020 puts in code.

## Resolution

Resolved 2026-07-20. Ten files authored under `prompts/`, written against the shipped `stride_service` models so ticket 020's lints have something real to bind to.

**The exemplar system.** A five-flow payment service in `analyst.md`'s `## Input`, given as two compact tables plus its derived crossings: `entity:customer` and `entity:payments-provider` in `boundary:public-internet`, `process:web-api` (internet-facing) in `boundary:dmz`, `process:ledger-service` + `store:accounts-db` + `store:audit-log` in `boundary:core`. `trust_zone` values are boundary **IDs**, matching what `validation.py:119` actually enforces. Four deliberate `unknown`s carry all six unknown-conditional exemplars — webhook `authentication`, `accounts-db.encryption_at_rest`, DB-flow `encryption_in_transit`, `ledger-service.exposure` — and are the only unknowns, so an analyst reading them learns the conditional pattern without ambiguity about which facts are stated. All 18 exemplars cite only IDs defined in that table (checked mechanically, not by eye).

**Lane contrast is prose, never JSON.** The wrong-perspective failure mode is taught by a sentence above the fenced block (spoofing S-02 vs elevation E-02 over the *same* unauthenticated gRPC flow; tampering T-01 vs information-disclosure I-01 over the same plaintext flow), so the model never sees a badly-phrased threat in a shape it might imitate. Same-flow pairs across categories were chosen deliberately: they make the lane boundary legible on identical facts, which is the whole point of one shared system.

**Section names as the lint's grip.** Exemplar H2s are the exemplar's own name (`Canonical: …`, `Second-order: …`, `Unknown-conditional: …`) — unique per file, as `split_sections` requires. Each holds exactly one fenced `json` block.

**`## Output` is prose in all four bodies**, no JSON anywhere in it — the machine-enforced shape is `DraftThreat`'s and duplicating it in the prompt would be a second source of truth. `analyst.md`'s `## Output` carries the semantics instead: title-as-attacker-action vs description-as-argument, the per-category ID letters with a sequence from `01`, and the one case where `mitigations` may be empty (a threat wholly conditional on an `unknown`, where no countermeasure can be named before the fact is learned — and the description must say so).

**Category stays a placeholder.** `analyst.md` uses `{category}`; job data uses `{system_model}`, `{boundary_crossings}`; `extract.md` uses `{input_text}`; `repair.md` uses `{previous_model}`, `{validation_issues}`, `{input_text}`; `critic.md` uses `{system_model}`, `{boundary_crossings}`, `{draft_threats}`. One analyst body, six exemplar files — ticket 020 composes.

**`critic.md`'s `## Procedure`** is the five steps in fixed order with 1-3 gating, and it opens by naming the mechanical checks (ID resolution, category letters, verdict shape, counts) as *already done in code* — so the LLM is told not to spend judgement there rather than left to guess.

**Verified, not asserted** (script over all ten files): exactly the four H2s in order and non-empty; no fence in any `## Output`; exemplar filenames derived from `StrideCategory`; ≥3 sections each with exactly one `json` block; every block carrying exactly the seven `DraftThreat` field names, its category letter matching, and its `severity`/`mitigations` parsing through the shipped `Severity`/`Mitigation` models; every `affected_element_ids` entry resolving against the exemplar system; no non-`.md` files under `prompts/`. Token counts (cap): analyst 1380 (2000), critic 996 (1500), extract 1047 (1500), repair 512 (800), exemplars 1079-1159 (1500). Worst-case analyst instruction ≈ 2.2K skill + 1380 + 1159 ≈ 4.7K, inside ticket 006's 6-8K envelope. Existing suite still 183 passed, 1 skipped.

**For ticket 020:** the checks above are the lint spec, already expressed as assertions against the real models — port them into `tests/test_prompt_lints.py` rather than re-deriving. Two are worth keeping beyond the ticket's list: `affected_element_ids` resolving against the exemplar system (an exemplar citing a nonexistent ID teaches the model to hallucinate IDs), and the no-fence-in-`## Output` rule (the drift guard that keeps the shape in exactly one place). `DraftThreat` needs no `level` field for these to pass — no exemplar asserts a severity band.
