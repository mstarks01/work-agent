---
id: 038
title: "Decide the graph's failure policy: retries, timeouts, partial results, poison inputs"
label: wayfinder:grilling
status: resolved
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Graduated on 2026-07-21 from the fog line "Error handling in the graph: partial
results, retries, timeouts, poison inputs", which has sat unspecified since
charting. It is sharp now because the graph exists: ticket 021 shipped
`stride_service.graph` and `AdkPipelineRunner`, so the failure surface is a
concrete set of nodes rather than a sketch, and it is credential-free — the
decision is about disposition, and the whole graph already runs offline against
scripted `BaseLlm` stand-ins.

**What ships today is exactly one policy: everything fails the whole job.**

- `execute_job` (`jobs.py`) catches `Exception` from the runner, transitions to
  `failed`, and stores only `GENERIC_FAILURE_MESSAGE`. The traceback is logged,
  never surfaced.
- `AdkPipelineRunner.run` documents this deliberately: "A node that raises
  propagates: the job fails loudly rather than completing with a report built on
  a check that did not run."
- Nothing anywhere sets a timeout, retries a node, or degrades. There is no
  retry count, no deadline, no partial-report path.
- `rejected` is a *different* thing and is already decided: it is the validity
  gate's verdict after the one repair pass, and it carries `ValidationIssue`s.
  This ticket does not touch it.

That policy is coherent, and the burden is on any change to beat it. But three
failure shapes now have a named node they land on, and none has been ruled on:

1. **One analyst of six fails** — a transient 5xx, a quota rejection, or an
   `output_schema` violation (ticket 021 accepted that a schema-violating
   emission fails the job rather than routing to `repair`). Six parallel
   `pro`-tier calls are the most expensive thing in the graph, and today one of
   them failing throws away the other five plus the extraction that fed them.
   A report missing one STRIDE lane is not obviously a report the service
   should emit — a threat model silently missing all Tampering findings is
   worse than no report — but "worse than nothing" is a claim, not a fact.
2. **A node hangs or is slow** — there is no deadline anywhere, so a stalled
   Vertex call holds a job in `running` indefinitely, and the API contract
   (ticket 008) has a poll loop with nothing to tell the caller.
3. **Poison input** — untrusted text that survives the 100 KiB cap and the
   150-element admission cap but reliably kills a node. The `rejected` state
   exists for input the gate can name; this is input that fails somewhere with
   no `ValidationIssue` to show.

Sitting behind all three: **is retry even the right layer?** ADK / the genai
SDK may already retry transient errors under the runner, in which case a retry
policy here would be a second one stacked on it. And every retry spends `pro`
output tokens, which ticket 024 established is the whole bill.

Resolved when the disposition of each shape is decided — including deciding
that today's fail-everything policy stands for some or all of them — and what,
if anything, that adds to the shipped code and the ticket-008 contract.

## Answer

**Six decisions, one of which is that the shipped policy was right.**

The question underneath all of them turned out to be answerable from the
installed packages rather than by argument, and it inverted the ticket's
framing. The ticket asked "is retry even the right layer — does ADK already do
this?" The answer is that **nothing retries anything anywhere**:
`Gemini.retry_options` defaults to `None`, and `google/genai/_api_client.py:535`
turns `None` into `tenacity.stop_after_attempt(1)`, documented as the "never
retry" strategy. `HttpOptions.timeout` likewise defaults to `None`, and
`_api_client.py:1068` passes that straight to httpx, which means **no deadline
at all**. So the shipped behaviour is not "the SDK handles it and we fail on
what is left" — it is a graph where a single 429 on any of nine nodes kills a
paid-for job on first contact, and a stalled call holds a job in `running`
forever. Every decision below follows from that.

### 1. No partial reports, ever

A lane is all-or-nothing. An empty Tampering section must mean *we looked and
found nothing*, never *that analyst 503'd* — the reader cannot tell those apart,
and per-element coverage is the entire value proposition. Rejected the
`degraded`-flag variant that would have salvaged five of six `pro` calls: it
puts a failure vocabulary into `StrideReport`, forces the eval scorer to decide
what a missing lane scores, and hands a client that ignores the flag a false
all-clear. A threat model that under-reports without saying so is worse than no
threat model. This is what makes decisions 2 and 3 worth their cost — with no
degraded path, every transient error is a total loss.

### 2. Retry is SDK-level, opted into via config

`resolve_model` returns `Gemini(model=..., retry_options=...)` instances instead
of bare strings. Nothing in `build_pipeline` changes: `ModelResolver` is already
`str | BaseLlm` — the type the offline stand-ins bind through — and
`_model_name()` already unwraps a `BaseLlm` via `.model`, so the report's
`nodes` array still records `gemini-2.5-pro` and ticket 026's `models` block is
unaffected.

Rejected graph-level node re-runs (ADK's `Workflow` has no node-retry primitive,
so it means re-entering the graph and reasoning about a node writing state
twice) and job-level re-runs (one analyst's 429 re-bills extraction and all six
analysts — on the eight-way `pro` fan-out that ticket 024 established is the
whole bill).

**Budget: 3 attempts** — the original plus two retries. Rides out a single blip
and a brief quota bounce without turning a Vertex incident into a nine-way
pileup of stacked backoff. The SDK's own retryable set (408/429/500/503/504) and
exponential-jitter backoff are reused rather than reimplemented. A failed request
bills nothing, so a retried 503 costs latency only.

### 3. One bounded re-ask, critic only

Retry structurally cannot see the failures that matter most here: a seam
rejection is a **200 response** whose content fails a fail-closed check.
`CriticOutputError` is the worst loss anywhere on this map — it fires at the
last node, *after* all eight `pro` calls are paid for, and its most likely
trigger is mundane: the critic must echo back every draft it was given, so on a
100-draft job one dropped ID destroys the entire run.

It gets the shape already blessed for extraction, and for the same reason —
`critic -> router -> {accept: assemble, revise: recritic} -> assemble`. One
extra pass, made **structural rather than counted**, exactly as `validate ->
repair -> revalidate` encodes the one-repair budget in the topology. It lands on
`ROUTE_REVISE`, which ticket 004 reserved and left unwired.

The consequence to implement carefully: **the mechanical check moves out of
`assemble_report` into `route_review`.** The router currently only counts; it
has to run the check to have something to route *on*, and it carries the issue
list into the re-ask, so the second pass is a precise correction rather than
"try again" — the same discipline that makes `repair` legitimate.

Analysts stay fail-closed on `DraftJoinError`: a bad draft is one lane's
problem, it cannot be corrected without re-running the lane, and decision 1
already says a lane that will not come back kills the job.

**Second failure is `failed`, not `rejected`.** `rejected` is defined as the
*input* failing the validity gate, and it carries `ValidationIssue`s the user can
act on. A critic that cannot echo IDs is our fault and has none. This keeps the
ticket-008 contract unchanged.

### 4. Per-request timeout, no whole-job deadline

`httpx.TimeoutException` is **already in the SDK's retry predicate**
(`_api_client.py:541`), so setting a per-request timeout converts a hang into a
retry rather than a wedge — decisions 2 and 4 are one mechanism, not two.
**300s per request**: generous for a `pro` call emitting a large threat list,
~15 min worst case per node, and the fan-out is parallel so a job's worst case
is roughly extraction + one analyst + the critic passes.

No second deadline. A whole-job timer is a mechanism that can kill a job which
is making progress, and it would put a new terminal reason on the ticket-008
contract; the bound here is derivable from longest path x timeout x attempts.

The timeout rides on the **per-request** `GenerateContentConfig.http_options`,
not on the client — verified that ADK merges its tracking headers and api
version into whatever we set (`google_llm.py:227-235`), so nothing is clobbered.
This is why **`config/sampling.toml` is not touched**: `_llm_node` composes the
sampling config and the resilience config into one `GenerateContentConfig`, and
the pinned-sampling rule stays exactly as ticket 023 left it.

### 5. Poison input gets no mechanism

It is not a runtime category — it is a bug to read in logs. Cost per submission
is already bounded by the retry budget, the 100 KiB request cap (ticket 008) and
the 150-element admission cap (ticket 010). `pipeline.py` already computes
`source_sha256` of the description for the report, so **log that digest on
failure** and a repeat offender is identifiable across jobs without storing the
text. Rejected digest-based short-circuiting (new cross-job state behind a
`JobStore` seam whose backend is still undecided, plus a false-positive path
where a transient outage permanently poisons a good input) and per-owner rate
limiting (duplicates what Cloud Run and the Ping gateway already provide).

### 6. New `config/resilience.toml`, env-overridable

The split that decides this: `sampling.toml` bans env override because
temperature changes **what the model produces**, so an eval number taken at one
temperature is meaningless at another. Attempts, backoff and timeout change
**how hard we try to get an answer, never which answer** — they cannot move a
score, only whether one exists. That makes them genuinely operational, and they
are the knobs you most want to turn down during a Vertex incident without a
redeploy. Its own file rather than a section of `sampling.toml`, so the override
boundary is legible from the filename instead of a per-key rule.

### Two consequences recorded rather than decided

- **The `nodes` array cannot carry attempt counts.** SDK retries happen inside
  the genai client, below the event stream the runner reads, so the runner
  cannot see them. Retry visibility is therefore an observability concern
  (client-level metrics), and belongs to the fog line that owns logging and OTel
  — not to the report schema.
- **The eval judge needs the same config.** `evals/config/judge.toml`'s
  `VertexJudge` makes its own Vertex calls outside the graph. Without resilience
  wiring, a scheduled sweep dies on one 429 after hours of work — the exact
  failure this ticket exists to stop, in the one place the graph's fix does not
  reach.

Graduates [Implement the graph failure policy](039-implement-failure-policy.md).
Nothing here is verifiable against live Vertex (same constraint as
022/023/026/028/037): the retry and timeout paths are testable offline against
raising stand-ins, but no real 429 has ever been observed here, so the attempt
and timeout numbers are reasoned defaults that
[Establish baselines and promote the gates](032-establish-baselines.md) is the
first thing able to challenge.
