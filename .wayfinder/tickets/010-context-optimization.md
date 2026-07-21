---
id: 010
title: "Context optimization strategy"
label: wayfinder:grilling
status: resolved
assignee: github@michaelstarks.com
blocked-by: [003, 004]
---

## Question

How is state sliced per node so each agent sees only what it needs: which slices of the canonical model each node type receives (e.g., an S-category analyst gets only auth-relevant elements?), what intermediate artifacts pass between nodes vs stay in session state, summarization/compaction points, and Vertex context-caching opportunities for shared prefixes (skills, system model).

## Answer

Six decisions. The framing that produced them: measurement first. A 20-element
case gives the critic ≈16K tokens (skills 2.3K + prompt 1.3K + model ~2K +
crossings ~0.5K + drafts ~10K, from the ≈690-token exemplar system and
150-200-token drafts) against a million-token context. **Context economy is not
a live problem at the sizes this service is designed for** — so the answer is
to *bound* inputs and keep full fidelity inside the bound, not to compress.

1. **No per-analyst element view; the pre-filter promise is retired.** All six
   `## Applicability` openers claimed "your element view is mechanically
   pre-filtered", which `prepare_analysis` never did — every analyst gets the
   whole rendered model and all crossings. Reworded to "Your analysis targets
   are X … every element is available as evidence; what is scoped is where you
   may *file* a threat". Ticket 004's filter is cancelled, not deferred: a
   correct filter is targets ∪ incident flows ∪ crossings, which on a connected
   model is nearly everything (spoofing analyzes entities/processes but reads
   their flows; EoP cites crossings and outbound flows), so the token saving is
   ~0 while the costs are real — a filtered analyst that sees a cross-lane
   threat cannot cite the element it hangs on, and the critic's dedupe is only
   sound because all six analysts saw the same facts. Guarded by a new lint,
   `test_applicability_does_not_claim_a_filtered_element_view`.
2. **Admission cap: 150 elements, enforced in `validate`.** `MAX_ELEMENTS` in
   `stride_service.validation`, threaded through `validate()` and
   `parse_and_validate()` as a parameter. Over the cap emits a
   `too-many-elements` `ValidationIssue` and **returns alone** — a model too
   large to analyze cannot be fixed by correcting its IDs, and 300 companion
   issues would bury the one that matters. Element count rather than a token
   estimate because it is the number a user can act on ("split the system");
   `len()` rather than a model call because this is mechanical. The job lands
   in the existing `rejected` lifecycle state — no new error shape.
   The **value is a blast-radius guard, not a calibrated threshold**: the
   golden corpus is 8-20 elements by design (ticket 009), so where quality
   actually decays with draft count is unmeasured. Recorded as fog.
3. **Dual-key state ratified, with a write-once invariant.** Structured keys
   are the code's view, rendered keys the model's; both copies are kept
   deliberately, because reading back the exact bytes a model saw is what makes
   a failed job debuggable. The rule that stops them drifting, now documented
   at the state-key block in `graph.py`: **a rendered key is written once by
   the FunctionNode that derives it and never read by Python**, and no node
   mutates an artifact after rendering it. Cost is ~2x state bytes — trivial in
   memory, but a real per-job cost once a persisted session backend lands, so
   it is recorded against that fog line rather than fixed here.
4. **Implicit context caching only; no `CachedContent` in v1.** The six
   analysts do *not* share a prefix with each other (different skill, different
   exemplars), so caching buys nothing within a job — it pays only across jobs
   for the same category, making this a function of traffic we do not have.
   Explicit caches would add 7 resources billed for storage plus an
   invalidation hazard where a stale cache serves last deploy's prompt text,
   silently invalidating evals. The stable-first composition already shipped
   (skills before prompt, shared body before exemplars) is what keeps hits
   *possible*; capturing `cached_content_token_count` per node turns the
   revisit into a number rather than an argument, and belongs to the
   observability fog line.
5. **Request-size cap: the shipped 100 KiB stands.** Ticket 008 already
   enforces `MAX_DESCRIPTION_BYTES = 100 * 1024` plus a 120 KiB raw-body
   middleware check before parsing. Ratified unchanged — a freshly-guessed
   128 KiB is not better, only out of step with 008. The two caps sit at
   different layers on purpose: bytes are a transport fact knowable at zero
   cost, element count is a semantic fact that exists only after extraction.
6. **No compaction, anywhere, stated as a decision.** No summarization node, no
   draft-field stripping, no truncation. With inputs bounded and
   `include_contents='none'` on every LLM node, no transcript accumulates —
   there is nothing to compact, architecturally rather than by omission.
   Compacting the critic's view was considered and rejected as actively wrong:
   step 4 of its procedure rewrites `justification` when it changes a rating,
   so stripping justifications would break a shipped judgement step. Generally,
   everything summarization would drop (source excerpts, element IDs,
   justifications) is something the report must cite — a service whose value is
   "traceable to your input" cannot afford a lossy middle step. The response to
   a too-large job is decision 2's refusal, not quiet compression.

**Shipped with this ticket:** the six reworded `## Applicability` openers, the
element cap with its `too-many-elements` issue code, the state-key invariant as
documentation, and 7 new tests (6 cap tests + 1 lint). Suite 308 passing,
1 skipped.

**Residual cost, deliberately not fixed:** an oversize model still spends one
cheap `flash` repair call before rejection, because `validate`'s `invalid` edge
goes to `repair` by topology. The wasted call is one flash call against six
`pro` analysts avoided; routing unrepairable issue codes straight to `reject`
is an edge change worth making only if it shows up in cost data.
