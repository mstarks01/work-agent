# Report Schema

A successful analysis returns a `StrideReport` (from `stride_service.report`).
It is **self-contained**: every element a threat references resolves inside the
embedded system model, so a consumer needs nothing but the one payload. The
model validators enforce that on construction — a report that does not hold
together cannot be built.

Get one from either [the engine](Integration-Guide.md) (`outcome.report`) or the
[`/v1/jobs/{id}/report`](HTTP-API.md) endpoint.

## Seeing one rendered

Run the [web app](Web-App.md) — [First-Run](First-Run.md) step 3. It renders a
real report of your own: threat cards, a severity summary, the extracted DFD,
and the per-node provenance panel.

There is no checked-in sample report in this repository. One would be a second
description of this schema, drifting from the schema itself every time it moved,
and the fields below are the authoritative account.

## Top-level shape

```python
class StrideReport:
    schema_version: str          # "2.8"
    disclaimer: str              # AI-generated, not human-reviewed
    job: Job                     # id, status="completed", timestamps, revise_rounds
    input: InputRef              # system_name + one ref per submitted source
    nodes: list[NodeRun]         # per-node model, sampling fingerprint, duration_ms, token usage
    sampling: dict[str, dict]    # per-tier resolved decoding params (provenance)
    system_model: SystemModel    # the canonical model the analysis ran on
    boundary_crossings: list[BoundaryCrossing]
    threats: list[Threat]        # confirmed + needs-info, severity-ordered
    rejected_threats: list[Threat]
    unverified_grounds: list[UnverifiedGround]  # quote grounds not found in their source
    unresolved_mentions: list[UnresolvedMention]  # element IDs a description cites that do not exist
    missing_mitigations: list[MissingMitigation]  # threats offering no countermeasure, unexcused
    shared_element_names: list[SharedElementName]  # different-typed elements sharing one name slug
    coverage: list[CategoryCoverage]             # per-lane account of what each agent was offered
    analysis_context: AnalysisContext | None     # what informed the analysis (never what proves it)
    summary: Summary
```

`threats` holds the actionable findings; `rejected_threats` is the audit trail
of drafts the critic ruled out. Both are the same `Threat` type — placement is
decided by the verdict.

**How `schema_version` moves.** Adding a field is a **minor** bump: a consumer
reading the fields it already knows is unaffected. Changing the *meaning* or the
*spelling* of something that already exists — renaming a field, renaming a value
inside an existing enum, or narrowing what a field may contain — is a **major**
bump, because that is the change a consumer cannot detect by reading its own
fields and will otherwise misread silently. One bump covers a whole cutover,
however many changes it carries.

> **`schema_version` 2.0** added [`grounds`](#grounds--why-the-finding-was-raised)
> on every threat and `unverified_grounds` on the report — both additive, and
> minor on their own. What earns the major is that `nodes[].node` changed the
> *values* it carries: the six category nodes are now `analyze_<category>`, not
> `analyst_<category>`. A consumer keying on `analyst_spoofing` does not error;
> it matches nothing, silently.

## A threat

`InputRef` ties the report to what was submitted without carrying the text:

```python
class SourceRef:
    kind: str                    # description | transcript
    label: str                   # the citation key an element's source_label names
    sha256: str                  # digest of that one source's text

class InputRef:
    system_name: str
    sources: list[SourceRef]     # one per submitted source, in submitted order
    source_sha256: str           # aggregate, taken over the refs above
```

The aggregate is computed **over the refs**, not over the concatenated text, so
it stays recomputable from the report alone — the refs are in the report, the
text never is.

Every element in `system_model` carries `source_excerpt` (a verbatim quote),
`source_label` (which source the *quote* came from — it always names one of the
refs above), and an optional `source_speaker` (who said it, where the text
attributes it). `source_speaker` is a separate field precisely so a name can be
stripped; a name inside a verbatim excerpt cannot be. An element's `notes` may
also quote submitted words — a speaker's hedge, or two sources disagreeing —
so both fields ship raw caller text in the report JSON, as does every `quote`
ground on a threat.

```python
class Threat:
    id: str                          # "<letter>-<NN>", e.g. "S-01" (see letters below)
    category: StrideCategory         # spoofing | tampering | repudiation | ...
    title: str
    description: str
    affected_element_ids: list[str]  # element IDs in system_model — always resolve
    grounds: list[Ground]            # why it was raised — at least one, never empty
    severity: Severity
    mitigations: list[Mitigation]    # {summary, detail}
    confidence: Rating               # low | medium | high (critic-calibrated)
    verdict: Verdict
```

Category letters: `S` spoofing, `T` tampering, `R` repudiation,
`I` information-disclosure, `D` denial-of-service, `E` elevation-of-privilege.
A threat's `id` must carry its category letter.

### Grounds — why the finding was raised

Every threat carries at least one `Ground`: the evidence the category agent that
drafted it says justifies it. `grounds` is never empty, and has no maximum.

**Chosen by an agent, constructed by the service.** A category agent names the
facts it relied on — an ID from the evidence catalog the service derives from
the validated model, or a quoted span and the source it came from — and the
service builds these records from that selection. No model writes a `Ground`,
which is why the flat encoding below is safe: the conditional relationship
between `kind` and the fields it requires never has to survive a schema
compiler. See [ADR 0004](adr/0004-evidence-references.md).

```python
class Ground:
    kind: "quote" | "unknown-attribute" | "derived-fact"
    text: str                    # quote: the verbatim span, ≤1000 chars
    source_label: str            # quote: names one of input.sources
    element_id: str              # unknown-attribute: resolves in system_model
    attribute: str               # unknown-attribute: the attribute that is `unknown`
    flow_id: str                 # derived-fact: a data flow in system_model
```

| `kind` | Carries | Reads as |
| --- | --- | --- |
| `quote` | `text` + `source_label` | the submitter's own words said this |
| `unknown-attribute` | `element_id` + `attribute` | this fact was never stated, so the threat stands unrefuted |
| `derived-fact` | `flow_id` | this flow's boundary crossing is the fact relied on |

**One flat model, not a discriminated union.** Fields belonging to the other two
kinds are empty strings, and a record carrying a field its own kind does not
claim is rejected on construction rather than tolerated — so a consumer may read
the fields its `kind` names and ignore the rest. Why the shape is flat rather
than a tagged union, which it should be, is
[ADR 0002](adr/0002-finding-level-attribution.md).

A `derived-fact` names the flow and **never copies the zones it crosses**: they
recompute from `boundary_crossings`, which the report already carries, so a
renderer resolves them locally and there is no second copy to disagree with the
first. A `quote`'s `source_label` always names one of the `SourceRef`s in
`input.sources` — but the report never carries the source *text*, so a quote is
checkable against its origin only by whoever holds the submission.

The service does that check itself, at merge time, against the bytes that were
submitted: a quote is matched to the source it names through a fixed ladder
(whitespace runs collapse, typography and case fold, `…` marks an elision,
inline markdown is ignored). It proves a quote is **present**, never that it
supports the finding — that judgement stays the critic's.

```python
class UnverifiedGround:
    threat_id: str               # the threat carrying it
    index: int                   # position in that threat's `grounds` list
    reason: str
```

A quote that could not be found is **marked, not removed**: the entry still
renders, and `unverified_grounds` points at it by threat and index. A threat
where *nothing* verified never reaches the report at all — the job fails
instead. So an entry in this list means "one citation on an otherwise justified
finding did not match", which is worth showing a reader and is not grounds for
hiding the finding. The list is also empty when no source text was available to
check against, so it is evidence of a *failed* check, never of a check having
run.

## `unresolved_mentions` — IDs the prose cites that do not exist

A threat names the elements it acts on twice: structurally in
`affected_element_ids`, and in prose inside `description`, which the analyze
prompt asks agents to write with element and flow IDs cited inline. The two are
checked differently on purpose.

```python
class UnresolvedMention:
    threat_id: str               # the threat whose description cites it
    mention: str                 # the ID as written, e.g. "process:web-api"
```

A structural reference that does not resolve **fails the job** — it is the
threat's claim about what it acts on, and a claim about a missing element is
not a finding. The same ID written into the argument is **marked** instead, for
the reason an unfindable quote is: merge has no re-ask path, and a whole report
is too much to trade for a mistyped ID in a sentence.

The mark is worth more than typo-catching. The analyze prompt is built around a
single worked exemplar system and spends a section telling the agent never to
cite that system's IDs. A description arguing about `process:web-api` in a job
whose model has no such element is that contamination reaching the one field a
reader reads as ordinary analysis — and nothing checked it before 2.2.

Detection is deliberately narrow: a token has to open with one of the five real
type prefixes and a colon, a shape ordinary English does not produce
(`"Process: the web app"` has a space and is not a mention). It errs toward
missing a citation rather than flagging prose, which is the right way round for
a mark that annotates a finding a human will read.

## `missing_mitigations` — findings with nothing to do about them

`mitigations` may be empty, but the prompt licenses that for exactly one case:
the threat is conditional on an `unknown`, and no countermeasure can be named
without first learning that fact.

```python
class MissingMitigation:
    threat_id: str               # the threat offering no countermeasure
```

That case is mechanically recognizable, so the report distinguishes it. A
threat triggered by an `unknown` carries an `unknown-attribute` ground —
the trigger dictates the branch — so an empty `mitigations` list *with* such a
ground is the licensed case and is not marked. An empty list *without* one is.

This is a **completeness** signal, not a correctness one, which is why it is a
mark rather than a failure: a finding with no recommended action is still a
finding. What it buys a reader is the ability to see which findings arrived
with nothing to do about them, and to tell those apart from the ones that
correctly said "answer this unknown first".

## `shared_element_names` — one name, two types

An element ID is `type:name-slug`, so a model that records one real thing twice
— once as `process:web-app`, once as `store:web-app` — holds two *distinct*
IDs. The validity gate's `duplicate-id` rule compares whole IDs and passes the
pair. `extract.md` tells the transcriber "nothing gets two types", and this is
what that rule is about.

```python
class SharedElementName:
    name_slug: str               # the slug both elements normalize to
    element_ids: list[str]       # the two or more IDs that share it
```

**Why a mark and not a gate failure.** The gate has exactly one severity —
every `ValidationIssue` is fatal, and an empty list *is* the definition of
ready-for-analysis the graph routes on — and this does not deserve that,
because it is not always wrong. A system can legitimately run a process and
keep a store of the same name. Failing extraction on the pair would reject a
valid model, and would spend `repair`'s single pass telling a transcriber to
rename something correct. So it is reported to the reader and to nobody else:
it never reaches `repair`, which is told to change nothing the issues do not
cite.

Only zoned elements are compared — external entities, processes and data
stores. A trust boundary is a zone rather than a thing in the system, and a
flow's ID is built from its endpoints rather than from a bare type-and-name
pair. Same-type collisions never appear here: two elements of one type sharing
a name hold the same ID, which is `duplicate-id`'s to report.

This is the first mark about the **model** rather than about the threats, which
is why it carries element IDs and no `threat_id`. It is recomputable from the
report's own embedded `system_model`, by design.

## `coverage` — what each lane was offered

A threat count says how much a category agent found. It cannot say whether a
lane that found nothing had examined the system and cleared it, or had never
looked at half of it. `coverage` carries one row per STRIDE category — always
all six, including a lane that filed nothing.

```python
class CategoryCoverage:
    category: StrideCategory
    drafts: int                  # threats this lane filed, before the critic ruled
    rules: int                   # deterministic triggers defined in this lane
    rules_fired: int             # of those, how many produced a candidate here
    candidates: int              # structural leads handed to this agent
    candidates_cited: int        # leads whose every element the drafts cite
    elements: int                # elements in the system model
    elements_cited: int
    boundary_crossings: int
    boundary_crossings_cited: int
    unknown_controls: int        # attributes stating no verified control
    unknown_controls_cited: int
```

Every number is computed in code, from the system model, the deterministic
candidate triggers and the agent's own drafts. None of it is asserted by a
model, which is the reason it is worth recording at all.

**Read `*_cited` as cited, not as considered.** An agent that examined a flow
and correctly concluded there was no threat cites nothing, and is
indistinguishable here from one that never read it — there is no observable
that separates them, since a model's own claim to have looked is exactly the
assertion this design declines to trust. The fields are named for what they
measure. The honest use is the aggregate: a category citing two of forty
structural leads across a corpus is a signal, one agent's zero on one case is
not.

Counted over the **drafts**, not the ruled threats: coverage is a fact about
what the six agents did with the system, and a draft the critic later rejects
was still part of the system being examined.

## `analysis_context` — what informed the run

```python
class AnalysisContext:
    instruction_sha256: str   # digest of every LLM node's composed instruction
    domain_packs: list[str]   # the reference packs this model earned, in selection order
    fired_rules: list[str]    # the deterministic rules that matched, sorted
    knowledge_docs: list[str] # local-corpus documents those rules retrieved
```

The report records what each node *ran on* (`nodes`, `sampling`) and what each
finding *rests on* (`grounds`). This is the third thing: what the service put in
front of the agents.

- **`instruction_sha256`** digests every LLM node's composed instruction with the
  `{placeholders}` still unexpanded, so it identifies the repo-authored text —
  prompts, category skills, the shared rubric — and carries no submitted bytes.
  The submission's own digest is `input.source_sha256` and stays separate. A
  generation-identity fingerprint says nothing about the instructions, so
  without this two runs with identical fingerprints and completely different
  prompts are indistinguishable.
- **`domain_packs`** are selected per job from the model's own technology fields,
  so the same deployment gives two submissions different reference material.
- **`fired_rules`** names the deterministic triggers that matched, where
  `coverage` counts them.
- **`knowledge_docs`** names what those rules retrieved from the local corpus —
  `notes/<id>` for reference material, `cases/<id>` for a worked judgement,
  unioned across the six lanes. Retrieval is local and deterministic, so this
  list plus the checkout reproduces exactly the text the agents were shown. See
  [ADR 0008](adr/0008-retrieval-by-fired-rule.md).

**None of it is evidence, and the separation is the point.** A pack named here
did not ground anything and a rule named here did not find anything; what
supports a finding is its `grounds`, unchanged. Reference material informs
reasoning and is citable by nothing — a reader treating an entry here as support
for a threat has it exactly backwards.

`null` on a report from a runner that composes no analysis, which is the same
absence an empty `coverage` records.

## What is checked but never reported

Threat IDs are asked to run `01..N` within each category. A lane that emits
`S-01, S-02, S-05` has drifted from that, and the service **logs it and changes
nothing** — the IDs are unique, their letters match, and every downstream check
passes, so a gap costs a reader nothing and renumbering would move a finding's
identity between two runs of the same input. It is a fact about the agents, and
it lives in the run's logs rather than in this payload.

Two things `grounds` is deliberately not:

- **Not an element's `source_excerpt`.** An excerpt says why an *element* is in
  the model; a ground says why a *threat* was raised. They quote the same
  submission and answer different questions, and the category agents cannot see
  the excerpts — those fields are stripped from the model rendered to them.
- **Not `verdict.related_unknowns`.** An `unknown-attribute` ground is
  backward-looking (the unknown that prompted the finding) and agent-authored;
  `related_unknowns` is forward-looking (the unknown that must be answered
  before the finding can be ruled on) and critic-authored. When they name the
  same attribute, that is agreement, not duplication.

### Severity is derived, never asserted

`Severity` carries a qualitative `likelihood` and `impact` (`low|medium|high`);
`level` (`low|medium|high|critical`) is computed from a fixed matrix, so a model
cannot inflate it. `justification` explains the two inputs.

```python
from stride_service import derive_severity_level

derive_severity_level("high", "high")     # "critical"
derive_severity_level("medium", "low")    # "low"
```

### Verdict

The critic's ruling on the threat:

```python
class Verdict:
    status: "confirmed" | "needs-info" | "rejected"
    reason: str                          # required unless confirmed
    related_unknowns: list[UnknownRef]   # required iff needs-info
```

- `confirmed` — grounded in the model's facts.
- `needs-info` — plausible but hinges on an `unknown` attribute; each such
  attribute is named in `related_unknowns` (`{element_id, attribute}`). Stays in
  `threats`.
- `rejected` — not grounded. Moves to `rejected_threats`.

Those three conditional rules hold on every verdict in a report, but they are
**not enforced on the critic's output** — that shape is checked at the review
seam, where a violation routes to a bounded re-ask instead of failing the job.
See [ADR 0005](adr/0005-verdict-shape-is-re-askable.md).

## Summary

Counts a UI can render without walking the threat list:

```python
class Summary:
    threat_count: int
    by_category: dict[StrideCategory, int]
    by_severity: dict[SeverityLevel, int]
    needs_info_count: int
    rejected_count: int
    elements_analyzed: int
```

`summary` is computed from the report's own contents and must match them, so it
is safe to trust without recounting.

## Provenance

The report records exactly which models and decoding parameters produced it, so
a result stands on its own without trusting any outside record.

```python
class NodeRun:
    node: str                        # graph node name: "extract", "analyze_spoofing", "critic", …
    model: str | None                # the SERVED build, vendor-prefixed; None for code-only nodes
    requested_model: str | None      # the CONFIGURED route this node asked for
    sampling_fingerprint: str | None # 64-hex identity hash of (served route, decoding params)
    duration_ms: int
    usage: TokenUsage | None         # what the provider says the call cost; None if unmetered

class TokenUsage:
    prompt_tokens: int
    cached_prompt_tokens: int        # the part of prompt_tokens served from cache
    completion_tokens: int
    reasoning_tokens: int            # spent against max_output_tokens, absent from the output
    total_tokens: int
```

- **`sampling`** lists the decoding parameters each tier actually used, once per
  tier: `{"base": {"temperature": 0.0, ...}, "strong": {...}}`. A parameter left
  to the model's default shows as `null`. Values are whatever the param resolves
  to — a number, a count, a boolean, or a string for `thinking`, whose values are
  `"low"`, `"medium"` and `"high"` — so a consumer reading this block must not
  assume numbers.
- **`model`** is what *answered* — the build the provider reported, joined to its
  vendor prefix, e.g. `vertex_ai/gemini-2.5-pro-002`.
- **`requested_model`** is what was *asked for* — the configured route, e.g.
  `vertex_ai/gemini-2.5-pro`.
- **`sampling_fingerprint`** is a hash of the served route plus those parameters
  — one value identifying exactly how a node generated its output. It can be
  recomputed from the node's `model` and its tier's entry in `sampling`, so
  anyone can verify it. Code-only nodes (like `assemble`) have all three as
  `null`.

- **`usage`** is what the provider reported the call cost, in vendor-neutral
  field names. `null` for code-only nodes, and for any LLM node whose provider
  metered nothing — an *unmeasured* call is never recorded as a free one.
  Nothing here is derived: `total_tokens` is recorded rather than summed, and
  the parts are not cross-checked, because vendors disagree on whether
  `reasoning_tokens` sits inside `completion_tokens` or beside it. Sum them
  yourself only if you know who answered.

> **`schema_version` 1.1** added `requested_model` and redefined `model` as the
> served build rather than the configured string. A consumer keying on `model`
> now reads what answered.

> **`schema_version` 2.1** added `nodes[].usage`. Purely additive; a 2.0
> consumer that ignores unknown fields is unaffected.

> **`schema_version` 2.2** added `unresolved_mentions`. Purely additive — a new
> optional top-level list of the same shape `unverified_grounds` already had —
> so a 2.1 consumer that ignores unknown fields is unaffected.

> **`schema_version` 2.3** added `missing_mitigations`, a third optional
> top-level list of service-owned marks. Additive on the same argument.

> **`schema_version` 2.4** added `coverage`. Additive again: a fourth optional
> top-level list, service-owned and computed in code.

> **`schema_version` 2.5** added `shared_element_names`, a fifth optional
> top-level list of service-owned marks. Additive on the same argument — minor
> although it is the first mark describing the model rather than the threats,
> since what a consumer must do with an unknown field does not depend on what
> the field describes.

> **`schema_version` 2.6** widened the *values* the `sampling` block can carry
> to every type a resolved decoding param holds, which includes `thinking`'s
> enum string. No field was added, removed or renamed, and nothing a 2.5
> consumer already parses changed meaning — the block was typed to numbers, so a
> report carrying a string here could never be produced. The first entry in this
> list that is a fix rather than an addition: a deployment that set `thinking`
> ran its whole graph and then failed to assemble a report.

> **`schema_version` 2.7** added `analysis_context`: the instruction digest, the
> domain packs the job's model earned, and the deterministic rules that fired.
> Optional, service-owned and computed in code — additive on the same argument
> as the four lists before it, and the first block describing what *informed*
> the analysis rather than what it found.

> **`schema_version` 2.8** added `knowledge_docs` to that block: the local-corpus
> notes and cases the fired rules retrieved for the agents. Additive and
> service-owned like the rest of it, and under the same rule — a document
> informed the analysis and grounds nothing.

The report records both model fields and **compares neither**. It doesn't need
to: if the build moves, the fingerprint moves with it, and the run stops
matching any list of blessed fingerprints — so the drift surfaces there rather
than through comparison logic here.

The fingerprint is computed **per node execution**. One analysis cannot loop, so
a node appears once in it; across an eval sweep a node appears once per case, and
a build that moved partway through gives that one node two different
fingerprints. That is the signal, not a defect.

The fingerprint, not `seed`, is what makes a result reproducible to reason
about: `seed` is best-effort, and some vendors don't accept it at all. The
report carries fingerprints as-is. Whether they match a baseline that a
*particular deployment* has blessed is a separate question, answered against
that deployment's `config/blessed-fingerprints.toml` and recorded on the job —
never on the report, which travels as portable evidence. See
[Architecture → Provenance and certification](Architecture.md#provenance-and-certification).

A report produced without live models (the in-memory stub runner, or eval
fixtures) simply has an empty `sampling` and no fingerprints.

## Serialising

`StrideReport` is a Pydantic model. For JSON (dates as ISO strings, the shape a
front end consumes):

```python
report.model_dump_json()          # str
report.model_dump(mode="json")    # dict
```

## Rendering this report

**Every string in this document is untrusted.** It is either derived from the
prose someone submitted or supplied by the caller, and none of it is escaped for
your medium. The service does not sanitise it, because it cannot know whether
you are rendering to HTML, a terminal, a PDF or a Slack message.

So, wherever a value from this report reaches a user interface:

- Render it as **text**, never as markup — `textContent`, a templating engine
  with autoescape on, or your platform's equivalent.
- Never assign it to `innerHTML`, `outerHTML`, `insertAdjacentHTML`, or
  concatenate it into a string you then parse as markup.
- Set attributes by **property assignment** (`node.title = value`), not by
  building an attribute string.

This is a blanket rule rather than a per-field trust table, deliberately: a
table has to be maintained on every schema change or it silently starts lying,
and a field added later is exactly the one that gets missed.

`webapp/report_view.html` is a worked example — it builds DOM nodes throughout
and carries no escape helper at all, so there is nothing to forget to call.

## The other outcome

An input that cannot be modelled yields no report — the engine returns a
`PipelineRejected` carrying `list[ValidationIssue]` instead (see
[Integration-Guide](Integration-Guide.md)). Each issue has a `code`, a human `message`, and
optionally the `element_id` / `field` it concerns.
