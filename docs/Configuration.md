# Configuration

The service reads its behaviour from versioned files in `config/` and from a set
of environment variables. A missing, malformed, or unsupported configuration
stops startup. The loaders also reject incomplete tier selections, unknown node
names, invalid framework packages, and model/sampling combinations that the
pinned capability checks know will fail.

This page is reference material. For a first setup, use
[First run](First-Run.md). For definitions, use [Concepts](Concepts.md).

Both [`Engine.from_config(frameworks, env=...)`](Integration-Guide.md) and the
[HTTP app](HTTP-API.md) take the same environment; the tables below apply to both.

## Config files

| File | Purpose |
| --- | --- |
| `config/model_tiers.toml` | Maps each LLM node to a tier, and each tier to a `(vendor, model)` pair. |
| `config/sampling.toml` | Decoding parameters, shared by production and evals. |
| `config/resilience.toml` | Retry attempts and budget, per-request timeout, input bounds, and the job deadline. |
| `config/blessed-fingerprints.toml` | The execution identities this deployment has blessed. |
| `config/frameworks.toml` | Which framework packages this deployment carries. |
| `frameworks/` | One directory per framework package: its lanes, critic, disclaimer, rubric and local corpus. |
| `domains/` | The shared domain packs, selected from the system model rather than by any framework's rules. |
| `prompts/` | The framework-neutral prompt Markdown (`extract`, `repair`, `analyze`, `critic`, `recritic`). |

### Models and vendors

The code has registry entries for Vertex AI, Anthropic, and OpenAI. It reaches
all three through ADK's LiteLLM adapter. `model_tiers.toml` selects **nothing**:
both tier tables are absent, so startup fails until both tiers are configured.

“Supported vendor” does not mean every model from that vendor is usable. The
selected model must also pass the model-name, sampling, output-capacity, native
structured-output, and credential checks described below.

Three tiers, each selecting a `(vendor, model)` pair **independently**, so they
may run different vendors at once:

| Tier | What runs on it |
| --- | --- |
| `base` | extraction and repair |
| `strong` | every framework's lane agents, and by default its critic and re-ask |
| `review` | nothing, until you move criticism onto it |

`base` and `strong` are named on a capability axis. **`review` names a place
rather than a capability** — it is not stronger or cheaper. It exists so a
critic can run on a model other than the one it is checking, which two tiers
could not express: the only other place to put criticism was `base`, and a
re-ask on a cheaper model than the pass it corrects is refused.

Every tier must select a pair, including one nothing runs on. Only a tier the
node map **binds** builds an adapter, so selecting `review` costs no credential
until something runs on it. See
[Review independence](#review-independence).

```toml
version = 6
review_independence = "shared"

[tiers.base]
vendor = "vertex"
model = "gemini-2.5-flash"

[tiers.strong]
vendor = "anthropic"
model = "claude-opus-5"
```

Supported vendors are `vertex`, `anthropic` and `openai`. Every one is reached
through a single adapter (LiteLLM); there is no per-vendor code path, and Gemini
reaches Vertex the same way everything else does. The pair above is deliberately
mixed, because that is an ordinary configuration rather than an advanced one.

**"Gemini support" means Vertex-hosted Gemini.** `vendor = "vertex"` is the only
route to a Gemini model here, and it carries Vertex's ADC credential mode; the
Gemini Developer API is not a binding this service offers. If it is added later
it arrives as its own vendor rather than as a second credential mode on
`vertex` — a vendor owns exactly one credential mode, and `vertex_ai/` already
means "through Vertex" to the router prefix LiteLLM dispatches on.

**Auth is derived from the vendor, never configured alongside it.** Each vendor
owns its credential mode, so an unrepresentable pairing like `vertex` + an API
key cannot be written down at all:

| Vendor | Credential mode | Required environment |
| --- | --- | --- |
| `vertex` | ADC | `ANALYSIS_VERTEX_PROJECT`, `ANALYSIS_VERTEX_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` |
| `anthropic` | API key | `ANALYSIS_ANTHROPIC_API_KEY` |
| `openai` | API key | `ANALYSIS_OPENAI_API_KEY` |

Keys are read **only** from these vendor-scoped variables. LiteLLM's ambient
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` pickup is deliberately unused, so a
credential this deployment did not declare cannot authenticate a run. Keys are
never logged, never in the report, and never in a fingerprint; errors name the
variable, never its value.

**Pinning** means naming a model specifically enough that it won't quietly
change under you. The check is per model *family* and deliberately loose: it
rejects names that openly float (`-latest`, `-preview`, `-exp`) and, where the
vendor publishes a canonical form, requires that shape. Gemini 2.5 and later
ship no numbered builds, so there the bare name is the most specific identifier
available; the same is true of OpenAI's o-series.

Claude is the family with a published form:

```text
claude-<name>-<major>[-<minor>]     e.g. claude-opus-5, claude-sonnet-4-6
```

The code applies that shape to every Claude model name. It does **not** enforce a
minimum Claude generation. Dated identifiers such as
`claude-sonnet-4-5-20250929` do not match the accepted shape, while a dateless
name that matches it is allowed to proceed to the capability checks. Passing
the name check does not prove that the provider still serves the model.

A loose rule is the right one for the rest because it runs against three
vendors' catalogs at once, and its predecessor — an allowlist of numbered
Gemini builds — broke outright when Google retired them. Claude's half avoids
that trap by matching a shape rather than enumerating builds: a model released
tomorrow already satisfies it. The name check is only a proxy either way.
Stronger evidence comes from the **served build read back from each response**,
when the provider supplies it, and recorded for that node execution, described
under
[Architecture → Provenance and certification](Architecture.md#provenance-and-certification).

### Node keys, and the frameworks this deployment carries

`model_tiers.toml`'s `[nodes]` table carries **three keys per framework** beside
the two neutral ones:

```toml
[nodes]
extract = "base"
repair = "base"
"analyze/asvs" = "strong"
"critic/asvs" = "strong"
"recritic/asvs" = "strong"
"analyze/stride" = "strong"
"critic/stride" = "strong"
"recritic/stride" = "strong"
```

**Every framework this build can spell needs its three keys, whatever this
install carries.** The node list derives from the closed framework vocabulary
rather than from `frameworks.toml`, so a deployment running STRIDE alone still
names ASVS's three. A file that omits them fails the loader's completeness check,
and the message names the three missing keys — that message is the fix. Adding a
framework does not move `model_tiers.toml`'s version, because adding a node key
is a data edit under the same schema.

One knob per framework rather than one per lane. Every lane of one framework
runs the same judgement on the same tier, so six `analyze/<category>` keys that
always held one value had no reader; an operator choosing to run one framework
cheaper than another is the choice that has a purpose. The loader **checks that
a framework's `recritic` key resolves to the same tier as its `critic`** — a
re-ask on a cheaper model than the pass it corrects is the failure a comment
used to warn about, and at 2N keys a comment drifts.

**What the extra keys cost.** ASVS runs one lane per chapter of its standard, so
an ASVS job makes 17 `strong`-tier lane calls against STRIDE's 6, and a job
naming both makes 23. That is the knob these keys exist for: an operator who
wants ASVS cheaper than STRIDE points `analyze/asvs` at `base`.

`config/frameworks.toml` names which packages this install runs. Three sets have
to agree: what the code can spell, what this build carries, and what this
install selects. The first two agree at import; the third is checked when a
`Deployment` is constructed, and a name this build does not carry stops startup
rather than failing on the first job. A deployment missing an
`analyze/<name>` key for a framework it carries fails the same way.

### Sampling

`config/sampling.toml` (`version = 4`) pins decoding parameters **per tier**, in
`[tiers.base]` and `[tiers.strong]` tables that reuse the node→tier map from
`model_tiers.toml`. The eval harness and production read this same file, on
purpose: grading a configuration you don't actually ship is how a test suite
stays green while production quietly drifts.

The file lists **every** decoding parameter the surface admits, each either
pinned or left as a *commented* line explaining why. An omitted key is a typo the
loader rejects, never a silent fallback.

| Param | Shipped state | Notes |
| --- | --- | --- |
| `temperature` | **unset** | No value is legal on every model: Claude 4.7+ rejects the parameter, and OpenAI's reasoning families take only their own default of `1`. Set one per tier if you want a stated value — see below. |
| `max_output_tokens` | pinned `16384` base / `64000` strong | Must be pinned: silence means a *vendor-derived* cap. Sized against measured output — see below. |
| `candidate_count` | pinned `1` | Reserved; the loader **rejects any value ≠ 1**. |
| `constrain_output` | pinned `true` | Send this tier's node schema to the provider. Set `false` where the provider's schema compiler won't take it — see below. |
| `top_p`, `presence_penalty`, `frequency_penalty` | **unset** | No verified per-tier constant to pin. |
| `seed` | **unset** | Buys consistency, not reproducibility — and Anthropic does not accept it at all. |
| `thinking` | **unset** | Leaves the model's own preset. |

`thinking` is a uniform `"low"` / `"medium"` / `"high"` enum. It reaches all
three vendors, which is why there are no longer per-tier legal ranges: LiteLLM
maps it to adaptive `thinking` plus `output_config.effort` on Anthropic,
`thinkingConfig` on Gemini, and passes it through on OpenAI o-series. `"auto"`
and `"off"` are **not** accepted —
`"auto"` raises on two vendors, and `"off"` is worse than unportable, since
Gemini accepts it at build time and then fails the request.

The two `max_output_tokens` values differ because the tiers emit different
things, and they are **sized against measured output rather than chosen round**.
`extract` produces one System Model — a median of 1,810 tokens across the twelve
corpus cases, max 2,565. The critic emits one *ruling* per draft — an ID, a
verdict, a confidence, and a replacement severity only where it corrected one —
rather than the draft re-transcribed, which is roughly 60–90 tokens per ruled
threat against the ~400 the old whole-draft shape measured at. Its 32,768 is
therefore generous rather than tight: reasoning tokens are spent against this
same cap, and on the strong tier that is where nearly all of it goes.

That matters more than a bound usually does, because **truncation at the cap is
silent**. A completion cut off mid-generation comes back with no text part, so
the node writes no output key, no validator sees anything malformed, and the
next deterministic node fails to bind its parameter. Sizing these two values is
what prevents that; the graph routing a missing critic ruling into its re-ask is
what makes it legible when it happens anyway.

A tier asking for more than its model will serve now **fails the build**: every
provider accepts `max_output_tokens`, so the supported-param gate cannot see an
over-ceiling value and the serving model rejects it at request time instead. The
ceiling is read from the pinned model map per `(vendor, model)` — 16,384 on
`gpt-4o`, 64,000 on Claude 4.6+, 65,535 on Gemini 2.5 — and a model the map does
not know is not gated.

> **`top_k` is gone from the surface** (it was removed in version 3). It is the
> one parameter the build-time check provably cannot cover — LiteLLM re-injects
> it into the request *after* validation — so a wrong value would be silent while
> the fingerprint attested to it.

Parameters that break the structured-output contract are **never** in the file
and never overridable: `response_schema` (the SDK *raises*), `response_mime_type`
(silently discarded), `stop_sequences` (would truncate mid-token), and
`http_options` (owned by `resilience.toml`).

### The startup parameter check

Vendors do not accept the same decoding parameters. At startup, every tier's
`(vendor, model, sampling)` combination is run through the provider library's
own check, so **an unsupported parameter stops startup rather than failing the
first request** — otherwise it would raise partway through a job, after earlier
nodes had already been paid for. For example:

- `seed` on Anthropic, or on Vertex-hosted Claude, is a startup error — but the
  same `seed` on Vertex-hosted Gemini is fine.
- `temperature = 0.0` on an OpenAI o-series model is a startup error, because
  o-series models constrain temperature to exactly `1`.

The check asks the library itself rather than consulting a table this repo
maintains, so it cannot drift away from the behaviour that actually fires at
request time. The library's model data is read from the installed copy, so the
answer never depends on a network fetch during startup.

**Its one blind spot, and the check that covers it.** Asking the library means
inheriting the limits of the library's model data: a model released after the
pinned copy is unknown to it and falls back to the provider's *base* config,
where anything the provider generally accepts passes. That is usually harmless
— it is a name check, not an existence check — but one case is not. Anthropic
removed `temperature` from **Claude 4.7 onward**: only the model's own default
is accepted, and a request carrying the parameter is rejected. A tier that
states a temperature on a Claude newer than the pinned library would sail
through startup and die on the first node of a paid job.

So a second startup check runs beside the first: a tier on Claude 4.7 or later
with `temperature` set is a startup error naming the tier and both knobs that
hold the value. Three deliberate limits on it:

- **It gates the parameter, never the model.** The shipped sampling states no
  temperature, so this check has nothing to fire on until a deployment sets
  one. Every Claude generation runs here; what a generation decides is which
  parameters it accepts.
- It keys on the **model**, not the vendor. Vertex-hosted Claude is the same
  model under the same removal, and `vendor = "vertex"` must not be a way around
  it.
- The floor is **4.7, not 4.6**. Claude 4.6 still accepts `temperature`, so a
  stated value survives there rather than being swept up by a vendor-wide ban.

Unset the parameter for that tier and the model runs on its own default, which
is the only value these generations serve. This is a floor, not a re-introduced
support table: when the pinned library's model data catches up, the first check
starts catching the same case and this one becomes redundant rather than
contradictory.

### The startup schema check

Every LLM node in the graph binds an output schema, so a third check runs per
tier: **can this `(vendor, model)` be constrained to a schema *natively*?**

Where a provider cannot, the library does not fail — it *emulates* the
constraint by synthesising a single tool whose input schema is the response
schema and forcing a call to it. The two paths are not equivalent. The native
path resolves `$ref`/`$defs` before sending, because providers do not resolve
external schema references; the emulated path forwards the schema as-is. A
schema with nested types — which every Pydantic model here produces — therefore
arrives unusable, and the model answers in a shape of its own invention.

That failure is the most expensive one available: the request is well-formed,
the response is well-formed, and the job dies at the node's own output
validation partway through. Neither of the other two checks can see it. So a
tier whose model would take the emulated path is a startup error naming the
tier.

Like the supported-param check, this is asked as a **call**, not a table — the
check inspects whether the library had to synthesise its internal
response-format tool for this pair. That matters more than it sounds: under the
pinned library, the same Claude generation can be native on one vendor and
emulated on another, so a rule keyed on the model alone would pass a
configuration that does not work.

`supports_structured_output` is a **different and weaker question** — whether a
schema is honoured at all — and answers yes for models on both paths. It cannot
substitute for this check.

**The check is scoped to tiers that send a schema.** A tier running
`constrain_output = false` sends none, so how its provider *would* have
constrained one is not a fact about anything that happens, and checking it there
would reject a configuration on the strength of a request it never makes.

### When the provider won't take the schema at all

The check above asks *how* a provider constrains output. It cannot ask whether
that provider's schema compiler will accept **this** schema. Anthropic rejects
`SystemModel`'s with *"the compiled grammar is too large"* — a limit on the
schema, not on structured output as such, and an unpublished one, so nothing
computes it at build time.

That is what `constrain_output` is for. Set it `false` on the affected tier and
the schema stops going on the wire. It is **per tier, not per vendor**, because
it is not a fact about the vendor: the same provider takes a smaller schema
happily, and the same schema goes to another provider fine.

**Setting it `false` is not currently a working configuration.** An earlier
version of this page said it gives up constrained *generation* only, leaving
validation and the repair loop to cover the difference. Measured live against
`claude-sonnet-4-6` with the extraction schema suppressed, that is not what
happens: the model fences its JSON in a ```` ```json ```` block, which ADK hands
to validation unstripped so it fails before anything reads the content, and it
omits required fields (every `trust_boundaries[*].kind`). `repair` sits on the
same tier and is equally unconstrained, so the repair loop fails the same way
and the job dies.

The field is kept because the *mechanism* is right — the schema genuinely stops
going on the wire — but a tier that turns it off needs the graph to tolerate a
fenced response first. Where a provider will not compile a schema, the working
answer today is to make the schema smaller, not to stop sending it.

Every LLM node carries a schema the adapter can convert, so this setting is the
only thing deciding whether one is sent. (That was not always true: the six
category agents and both critic passes once bound bare `list[...]` schemas, which ADK
cannot convert — it sent none and they generated unconstrained, silently. They
now carry wrapper models, and a test asserts every node's schema survives the
conversion.)

It enters the execution identity, so a sweep measured with constrained output
does not certify a run made without it. It is deliberately **not** promotable: a
sweep tunes decoding values, and this is a deployment's answer about its
provider.

### Review independence

How far each framework's critic must sit from the analysis it checks. Required,
with **no default**: inheriting `shared` is how an install that meant to review
itself independently ends up not doing so, and reporting nothing unusual.

| Value | What it requires |
| --- | --- |
| `shared` | Nothing. Criticism may run on the very model it checks. |
| `distinct_model` | Every framework's critic runs a different `(vendor, model)` pair from its own analysis. |
| `distinct_provider` | The vendor must differ too. |

**None of these makes a review more accurate.** Independence bounds *correlated*
failure — an analysis and a critic on one model share that model's blind spots,
so the critic improves consistency and cannot notice what the model does not
know. It does not make a second opinion a better one, and no measurement in this
repository says a second provider finds more. Choose it as a risk control, and
weigh it against a second credential, a second quota and a dearer job.

Asking for more than `shared` while the node map leaves `critic/*` on the tier
`analyze/*` runs on is a **load-time error naming the framework**, not a warning
on the report. A deployment that asked for an independent reviewer and did not
get one has a configuration to fix, not a run to annotate — annotating it would
put the finding in an artifact somebody already paid for.

To get an independent reviewer, point criticism at `review` and give that tier a
different selection:

```toml
review_independence = "distinct_provider"

[tiers.review]
vendor = "anthropic"
model = "claude-opus-5"

[nodes]
"critic/stride" = "review"
"recritic/stride" = "review"
```

A `recritic` must resolve to its own `critic`'s tier, so the two move together.
The same pair under two tier names is not independence and is refused: the check
reads the selection, never the tier's label.

Every report states the policy in `execution.review_independence`, so a reader
of a `shared` run sees the review was same-domain rather than inferring it from
two node rows naming one model.

### Provider capabilities

Providers legitimately differ, and the service reports the difference rather
than smoothing it over. To see what a pair supports before selecting it — no
credentials, no network:

```sh
uv run python -m analysis_service.conformance
```

Every cell is one of three words, and the third one is the point:

| | meaning |
| --- | --- |
| `supported` | the provider accepts it, and the pinned model map is what says so |
| `unsupported` | the provider rejects it |
| `unknown` | the pinned model map has no entry for this model — nothing here knows |

`unknown` is not a polite `unsupported`. LiteLLM answers for a model it has never
heard of out of the provider's *base* config, which is frequently right and is
not a fact about that model; reporting the fallback as though it were checked is
how an open-world gap becomes a false assurance. A pair that reports `unknown`
still binds — refusing to run a model the map has not caught up with would be
worse — but nothing has verified it.

Differences the matrix shows today, none of them defects:

- `seed` is accepted on Vertex-hosted Gemini and on OpenAI, and rejected by
  Anthropic — including Vertex-hosted Claude, since the constraint belongs to the
  model rather than the host.
- `reasoning_effort` reaches Gemini, Claude and the OpenAI reasoning models, and
  is rejected by `gpt-4o`.
- Output ceilings differ by roughly eight times across the profiled pairs
  (16,384 on `gpt-4o`; 128,000 on `gpt-5.6` and `claude-opus-5`).

None of these fails conformance. What conformance requires is that the
*application* behaves identically given the same capability: the same
build-time refusal naming the same tier, the same fingerprint rule, the same
report schema. Capability parity is not a goal — the issue that introduced this
matrix lists "force all providers to expose identical sampling controls" as an
explicit non-goal.

The suite behind it is `tests/test_conformance.py`, which runs in the offline
lane on every pull request for all three vendors equally. It proves what each
provider *would be asked for*. It is not evidence that any vendor has served a
request; that is the smoke below.

### Checking that a provider actually serves the graph

The matrix costs nothing and proves nothing about a live provider. This does the
opposite — it needs credentials, and it is the only thing here that shows a
vendor answering:

```sh
uv run python -m analysis_service.smoke
```

One small system, once, through the shipped graph, on whichever pair your tiers
select. Roughly eight model calls on a ~600-character input, which is what makes
it cheap enough to run on every merge to `main` rather than before a release. It
runs there and not on a pull request because it holds live provider
credentials — see
[the trust model](../.github/WORKLOAD_IDENTITY.md#the-trust-model). It reports
eight answers:

| | |
| --- | --- |
| model binding | each node reached the provider its tier selects |
| structured extraction | a model came back and cleared the validity gate |
| analyst structured output | every lane of every framework parsed |
| critic structured output | the ruling parsed and reached the report |
| sampling parameter validation | the provider accepted this tier's params |
| served-model capture | what actually answered, where the provider said |
| execution fingerprint generation | the Execution Identity that implies |
| provenance generation | the record is complete and recomputes from itself |

Cells read `passed`, `failed` or `unknown`, and `unknown` means the *provider*
left the question unanswered — a response carrying no served build has no
identity to hash. That does not fail the run: the application did the right
thing with what it was given. A failure is the application's, never the model's;
nothing here scores threat-model quality, which is `evals/`'s job and is expected
to differ between vendors.

In CI this is `.github/workflows/provider-smoke.yml`, one lane per vendor on the
same trigger. A lane whose credentials are absent reports itself **unexercised**
in its job summary rather than passing quietly, so a green check list never
implies a provider was tried.

### Resilience

`attempts = 3`, `timeout_ms = 300000`, `max_source_bytes = 102400`,
`max_sources = 10`, `job_deadline_ms = 900000`, `retry_budget_ratio = 0.1`,
`max_active_jobs = 3` (`version = 5`). On library
defaults the LLM nodes never retry and never time out, so a single 429 kills a
paid-for job; two more bound what one job may carry, the deadline bounds how
long one may run, and the ceiling bounds how many one caller may run at once.
Unlike sampling, all of them **are** environment-overridable —
none can move an eval score, because retry and timeout change how hard
the service tries, the input bounds and the ceiling decide only whether a
submission is accepted at all, and the deadline only whether an answer arrives
in time.

`max_active_jobs` is the only bound here that is per **caller** rather than per
job, and the others are why it has to exist: a caller who respects every one of
them and simply keeps submitting is inside the contract while spending the
deployment's whole provider quota. Each accepted job fans out one lane
agent per lane of every framework it runs, in parallel on the `strong` tier:
six for STRIDE, seventeen for ASVS, twenty-three for a job that names both. The
shipped `3` is therefore sixty-nine concurrent `strong`-tier requests on that
selection — the burst a per-minute quota actually sees, and the arithmetic to
redo before raising the ceiling or carrying another framework. A submission past
the ceiling is refused with `429`, never queued: a queued job holds the caller's
place in the quota anyway, so only a refusal sheds load. It counts jobs **in
flight** (`queued` plus `running`), not submissions per interval, so it is
self-clearing — finishing a job is what buys the next one, and it needs no
window, no timer and no state the job store does not already hold.

The ceiling is exactly **as shared as the job store is**, which is why it is
enforced through `JobStore.reserve` rather than out of process memory. Behind
two instances of the per-instance `memory` store, each instance enforces the
number and the effective ceiling is the sum — the same defect `ANALYSIS_JOB_STORE`
[fails closed](HTTP-API.md#job-storage) over. Size it against one instance's share, or
register a shared store and get a shared ceiling with no config change. Where
the edge (a load balancer or API gateway) already enforces a per-caller quota,
this is the backstop behind it, not a duplicate of it. See
[ADR 0007](adr/0007-per-caller-concurrency-ceiling.md).

`job_deadline_ms` is the only bound on a **job as a whole**, and the per-call
knobs cannot substitute for it. `timeout_ms` bounds one request at 300 s,
`attempts` allows 3 of them per node, and the graph runs five LLM stages in
series on its longest path — 75 minutes, with every individual bound respected
the whole way. It was over two hours before the retry amplification below was
removed, which is the point: the product moves whenever any factor does, and
only a deadline states the answer directly. 900 s is a **backstop, not an
SLO**: one observed clean run is ~119 s, the longest legitimate path with a
repair pass and a critic re-ask is ~200 s, and one transient retry on the
slowest node puts it near 260 s. It fires on runs that are wedged, not merely
slow. Turn it *down* to shed load mid-incident — a job killed at the deadline
costs only what it had already spent, while one that hangs holds a worker.

A job that exceeds it becomes `failed` with a message that says so, rather than
the generic internal-error text: the deadline is a fact about this deployment's
configuration, not a detail of how the pipeline is built, and "internal error"
would send a caller straight back to retry an identical submission against an
identical bound. The nodes that had completed go to the log, never to the
caller — that is the evidence for sizing `timeout_ms`, which is still oversized
against measured latency but needs a p99 across real submissions rather than a
single trace.

`attempts` is a **total** count, and it is now literally the request count per
node. It did not used to be, and that gap was the 429 storm. On the OpenAI/Azure
path LiteLLM sets the provider SDK's own `max_retries` from the retry count it
is given, so the first attempt retried at the SDK level too and the worst case
per node was `2 * attempts - 1` requests — five at the shipped `3`, and up to
thirty in the seconds a framework's lane agents run in parallel. Passing `max_retries` on
the adapter did not close it, because the retry count LiteLLM is given overwrites
it.

So the library's retry layer is **off** (`num_retries = 0`, one request per call)
and the loop runs a level up, in `analysis_service.retry`, where it can be bounded.
Two things become possible there that could not exist below the adapter:

- **A shared budget.** `retry_budget_ratio` is one process-wide token bucket: a
  retry costs a token, a successful request credits `0.1` of one. Retries are
  capped at a share of *working traffic* rather than at a count per node — and a
  count per node is precisely the wrong response to a provider-wide failure,
  since it hands every node its full allowance regardless of what the other
  lanes are seeing. Correlated failure empties the bucket once for everyone and the
  service stops retrying; an isolated failure finds it full and is retried
  exactly as before.
- **Decorrelated timing.** Lane agents that start together fail together, and
  on any fixed curve retry together, reconverging on the quota they just
  tripped. Retries use full jitter — a uniform draw across the whole interval,
  not a delay with noise added — and a provider's `Retry-After` overrides the
  curve outright whenever one is sent.

Lowering `ANALYSIS_RETRY_ATTEMPTS` still works and is still the blunt instrument.
The budget is what makes reaching for it rare.

`max_source_bytes` is the **total across all of a job's sources**, not a bound
on any one of them: there is deliberately no per-source cap, since it would
forbid only shapes the total already permits. Both bounds are in UTF-8 bytes
rather than tokens, so the public contract does not change when a deployment
changes vendor.

Version 3 **added** the two input bounds. Version 2 **removed** version 1's four
backoff knobs (`initial_delay`, `max_delay`, `exp_base`, `jitter`): the adapter
picks its backoff curve internally from the exception type, so as configuration
they read as a knob and connected to nothing. Both are hard cutovers — a file on
an older version fails to load, so every deployment edits its file rather than
inheriting a default for a contract its callers can see.

## Environment variables

### Config paths (override where files are read from)

| Variable | Overrides |
| --- | --- |
| `ANALYSIS_DOMAINS_DIR` | `domains/` |
| `ANALYSIS_PROMPTS_DIR` | `prompts/` |
| `ANALYSIS_FRAMEWORKS_DIR` | `frameworks/` |
| `ANALYSIS_TIERS_FILE` | `config/model_tiers.toml` |
| `ANALYSIS_SAMPLING` | `config/sampling.toml` |
| `ANALYSIS_RESILIENCE` | `config/resilience.toml` |
| `ANALYSIS_BLESSED_FINGERPRINTS` | `config/blessed-fingerprints.toml` |
| `ANALYSIS_FRAMEWORKS_FILE` | `config/frameworks.toml` |

**Three text roots, not four.** `frameworks/<name>/` holds one framework
package's text — its lanes' skills and exemplars, its critic, its disclaimer, its
output contract, its severity rubric where its record grades harm, and the
reference notes and worked cases its own rules retrieve. The output contract says
what one claim is and which fields carry it, which is why it is the package's and
not the shared `analyze.md`'s: a record that grades nothing cannot read a field
list naming `severity`. `domains/` holds the shared domain packs,
which stay the service's because their retrieval key reads the neutral system
model rather than any package's rules. `ANALYSIS_KNOWLEDGE_DIR` is **gone**: the
corpus it pointed at moved into the package whose rules select it. See
[ADR 0011](adr/0011-package-text-follows-its-retrieval-key.md).

A variable **picks which file is read**; it never layers a second file over the
first. A set-but-empty value is a deploy mistake and raises rather than
falling back to a default. Unset, the default is this checkout's top-level
copy when running from a clone, or the copy bundled into the wheel
(`analysis_service/_bundled/`) when installed via `pip` — whichever the
installation has. Either way exactly one file exists at a resolved default
path; nothing merges the two.

These apply to the **whole deployment**, not only the service: an eval sweep
reads the same files, and promoting a sweep winner re-pins the same
`sampling.toml` and blesses into the same manifest. Redirect a path and
everything follows it — which is what makes grading a configuration you do not
run impossible rather than merely discouraged.

**Keep a local edit outside the repository.** Every file in the table is
tracked, so an edit to `config/model_tiers.toml` in a clone blocks the next
`git pull` until it is stashed or reverted. Copy the file to a path outside
the checkout, point the variable at that path, and restore the tracked file:

```bash
cp config/model_tiers.toml ~/.config/analysis-service/model_tiers.toml
git checkout -- config/model_tiers.toml
export ANALYSIS_TIERS_FILE=~/.config/analysis-service/model_tiers.toml
```

Set the variable in whatever starts the process: the shell profile, the
service unit, the container environment. Outside the checkout rather than an
ignored file inside it, because a `git clean` deletes an ignored file and a
`.gitignore` line for one machine does not belong in a shared file. The cost
is that an upstream change to the tracked file no longer reaches the copy:
after a pull, diff the tracked file against the copy and carry over what you
want. The same pattern covers `sampling.toml` and `resilience.toml`.

### Model overrides (deploy-time, no image rebuild)

| Variable | Effect |
| --- | --- |
| `ANALYSIS_MODEL_BASE_VENDOR` / `ANALYSIS_MODEL_BASE_MODEL` | Overrides the `base` tier's pair. |
| `ANALYSIS_MODEL_STRONG_VENDOR` / `ANALYSIS_MODEL_STRONG_MODEL` | Overrides the `strong` tier's pair. |
| `ANALYSIS_MODEL_REVIEW_VENDOR` / `ANALYSIS_MODEL_REVIEW_MODEL` | Overrides the `review` tier's pair. |

`_MODEL` **alone** is the ordinary case — retune a tier's model on a deployed
revision. `_VENDOR` alone is a **startup error**: a mismatched pair such as
`anthropic` + `gemini-2.5-pro` passes every downstream check and would only die
on the first node of a paid-for job.

Any unrecognised `ANALYSIS_MODEL_*` variable also raises. That is deliberate: a
deployment still carrying version 2's `ANALYSIS_MODEL_FLASH` must fail loudly
rather than have it silently ignored while the tier quietly runs the file's
model.

### Sampling overrides

`ANALYSIS_SAMPLING_{TIER}_{PARAM}` retunes one tier's decoding at deploy time,
validated **identically** to a file value. `{TIER}` is `BASE` or `STRONG`.

| Variable | Effect |
| --- | --- |
| `ANALYSIS_SAMPLING_{TIER}_TEMPERATURE` | Overrides the tier's `temperature`. |
| `ANALYSIS_SAMPLING_{TIER}_TOP_P` | Overrides the tier's `top_p`. |
| `ANALYSIS_SAMPLING_{TIER}_SEED` | Overrides the tier's `seed`. |
| `ANALYSIS_SAMPLING_{TIER}_THINKING` | Overrides the tier's `thinking` (`low`/`medium`/`high`). |
| `ANALYSIS_SAMPLING_{TIER}_MAX_OUTPUT_TOKENS` | Overrides the tier's `max_output_tokens`. |
| `ANALYSIS_SAMPLING_{TIER}_CONSTRAIN_OUTPUT` | Overrides the tier's `constrain_output`. Only the literals `true` and `false` are accepted — anything else raises. |

Only these are overridable. A variable naming a reserved (`candidate_count`),
removed (`top_k`) or forbidden param raises `not overridable`. Treat this as a
temporary escape hatch: an override changes the run's fingerprint, so a run using
one reads as **uncertified**. To change sampling for real, edit the file and back
it with a measurement — see [Tuning the models](../evals/TUNING.md).

### Resilience overrides

| Variable | Effect |
| --- | --- |
| `ANALYSIS_RETRY_ATTEMPTS` | Total attempts per LLM call. |
| `ANALYSIS_TIMEOUT_MS` | Per-request timeout, milliseconds. |
| `ANALYSIS_MAX_SOURCE_BYTES` | Total UTF-8 bytes across all of a job's sources. |
| `ANALYSIS_MAX_SOURCES` | How many sources one job may carry. |
| `ANALYSIS_JOB_DEADLINE_MS` | Wall-clock budget for one whole job, milliseconds. Turn it down to shed load. |
| `ANALYSIS_RETRY_BUDGET_RATIO` | Retries as a share of successful requests. Turn it down to give up sooner under sustained failure. |
| `ANALYSIS_MAX_ACTIVE_JOBS` | Jobs one token subject may have in flight. Turn it down to shed load; raising it multiplies by the lane count of the frameworks a job names. |

### How strictly each override family is checked

The three families do **not** treat an unrecognised variable the same way, and
the difference is worth knowing before you debug a setting that appears to have
no effect.

| You set | Result |
| --- | --- |
| `ANALYSIS_MODEL_BASE_MODLE` (typo) | **Startup fails**: `unrecognised model override(s)` |
| `ANALYSIS_MODEL_FLASH` (stale v2 name) | **Startup fails**: same check |
| `ANALYSIS_SAMPLING_BSAE_SEED` (typo'd tier) | **Startup fails**: `unknown tier 'BSAE'` |
| `ANALYSIS_SAMPLING_BASE_TOP_K` (removed param) | **Startup fails**: `TOP_K is not overridable` |
| `ANALYSIS_RETRY_ATEMPTS` (typo) | **Silently ignored** — the file's value stands |
| `ANALYSIS_TIMEOUT_MSEC` (typo) | **Silently ignored** — the file's value stands |

The reason is naming, not intent. `ANALYSIS_MODEL_*` and `ANALYSIS_SAMPLING_*` are
namespaces those two loaders own, so each can enumerate every variable it
accepts and reject anything else in its namespace. The resilience knobs are bare
`ANALYSIS_`-prefixed names, and `ANALYSIS_` belongs to the whole application — it
also holds the config paths, the provider credentials and the job-store
selector. No single loader can claim it, so the resilience loader reads the
names it knows and cannot see that you meant another.

What makes the weaker guarantee tolerable is the **consequence**, which is also
why the two families differ in the first place:

- Model and sampling change *what* the model produces. A silently missed
  override there means a run recorded as using one configuration actually used
  another, which invalidates every measurement taken against it — so those
  fail closed, hard.
- Retry and timeout change only *how hard we try*, never which answer comes
  back. A silently missed override costs you resilience, never correctness, and
  cannot move an eval score.

So: **when a retry or timeout change appears to do nothing, suspect the variable
name first.** Nothing echoes these values back, and a set-but-empty value *is*
caught (`ANALYSIS_RETRY_ATTEMPTS=` raises `is set but empty`) — it is only a
misspelled *name* that passes unnoticed.

### Provider environment

Each tier's vendor determines what credentials it needs; see
[Models and vendors](#models-and-vendors) for the table. If a selected vendor's
credentials are missing, startup stops with an error, so a misconfigured
deployment never reaches its first request. Offline tests and the in-memory
[stub runner](Integration-Guide.md) need none of this.

## Input limits

Bounds enforced before or during analysis:

| Limit | Value | Where |
| --- | --- | --- |
| `max_source_bytes` | 100 KiB (UTF-8), total across all sources | Rejected at both entry points; deployment config. |
| `max_sources` | 10 | Rejected at both entry points; deployment config. |
| `job_deadline_ms` | 900 s per job, wall clock | `failed` at the deadline; deployment config. |
| `max_active_jobs` | 3 jobs in flight per token subject | `429` at submission; deployment config. |
| Source `label` | 200 characters, single-line, unique per job | Rejected as a malformed source. |
| `MAX_SYSTEM_NAME_CHARS` | 200 | Rejected by the engine / API. |
| `MAX_ELEMENTS` | 150 | A larger model is a `too-many-elements` [rejection](Report-Schema.md). |
