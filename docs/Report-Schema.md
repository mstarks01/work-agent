# Report Schema

A successful analysis returns a `Report` from `analysis_service.report`. The
report keeps the result and the system model it was based on in one payload.
Code verifies that referenced element IDs exist, boundary crossings match the
embedded model, summary counts match the claims, and the framework blocks match
the job's request. A report that fails those checks cannot be constructed.

This structural consistency is useful but limited. It does not establish that
the extraction or security judgement is correct. Start with
[Concepts](Concepts.md) for the plain-language meaning of the fields below.

Get one from either [the engine](Integration-Guide.md) (`outcome.report`) or the
[`/v1/jobs/{id}/report`](HTTP-API.md) endpoint.

## Seeing one rendered

Run the [web app](Web-App.md) — [First-Run](First-Run.md) step 3. It renders a
real report of your own: finding cards, a severity summary, the extracted DFD,
and the per-node provenance panel.

There is no checked-in static sample report. The local app renders the current
schema directly from a real run.

## One envelope, N framework blocks

A job names the security frameworks it wants (`frameworks`, required and
non-empty — see [HTTP-API](HTTP-API.md)), and the report answers **exactly that
set**. The envelope carries what the job and the shared system model are; each
framework's own findings sit in its own block under `analyses`.

```python
class Report:
    schema_version: str  # "3.0"
    disclaimer: str  # the SERVICE's: AI-generated, not human-reviewed
    job: Job  # id, status="completed", timestamps, revise_rounds, frameworks
    input: InputRef  # system_name + one ref per submitted source
    nodes: list[
        NodeRun
    ]  # per-node model, sampling fingerprint, duration_ms, token usage
    sampling: dict[str, dict]  # per-tier resolved decoding params (provenance)
    system_model: SystemModel  # the ONE canonical model every framework ran on
    boundary_crossings: list[BoundaryCrossing]
    shared_element_names: list[
        SharedElementName
    ]  # different-typed elements sharing one name slug
    elements_analyzed: int
    analysis_context: (
        AnalysisContext | None
    )  # what informed the analysis (never what proves it)
    analyses: list[FrameworkAnalysis]  # one block per framework, in the job's own order
```

**A field sits where the thing it describes sits.** Nine fields describe the job
or the shared model and stayed on the envelope. Eight describe one framework's
output and moved onto the block. `analysis_context` split on the same rule: the
instruction digest describes the built graph and the domain packs describe the
model, so both stayed, while `fired_rules` and `knowledge_docs` name *one
package's* rules and what they retrieved, so both moved.

**`analyses` is an ordered list, not a map.** It is ordered by the job's own
selection, and the envelope re-checks that the blocks answer exactly the
frameworks the job named — a report that answered half a selection would make
every consumer check which half it holds.

**One shared model, never a second copy of it.** The alternative — one whole
report per framework — was refused because it duplicates nine fields including
the largest block after the findings, and nothing could check that two embedded
copies of one model agreed.

### A framework block

```python
class FrameworkAnalysis:
    framework: str  # "asvs" | "stride" — the name the job selected
    framework_version: str  # the ruleset version that produced these claims
    disclaimer: str  # the PACKAGE's: what this framework's claims assert
    claims: list[RuledClaim]  # the actionable findings
    rejected_claims: list[RuledClaim]  # the audit trail of drafts the critic ruled out
    scope: list[ScopeEntry]  # units this framework considered and raised nothing about
    coverage: list[LaneCoverage]  # per-lane account of what each agent was offered
    unverified_grounds: list[UnverifiedGround]
    unreconciled_rulings: list[
        str
    ]  # how the first critic pass failed, before the re-ask
    repaired_quotes: list[RepairedQuote]
    unresolved_mentions: list[UnresolvedMention]
    unresolved_evidence: list[UnresolvedEvidence]
    unknown_claim_identities: list[UnknownClaimIdentity]
    dropped_claims: list[DroppedClaim]
    fired_rules: list[str]  # this package's deterministic rules that matched
    knowledge_docs: list[str]  # local-corpus documents those rules retrieved
    summary: BlockSummary
```

`claims` holds the actionable findings; `rejected_claims` is the audit trail.
Both are the same type — placement is decided by the verdict.

**Two disclaimers, and they say different things.** The envelope's is about the
*service*; a block's is about *that framework* — what its claims assert. The two
stop being one sentence the moment a report carries a framework that rules on
requirement applicability rather than on attacks.

**A block may narrow its arrays.** STRIDE's block is a `StrideAnalysis`, whose
`claims` are `Threat`s and whose `summary` carries `by_category` and
`by_severity`. A consumer that does not know a framework reads the base shape —
an ID, the `(framework, version)` pair, a title, a description, the elements and
the grounds — which is the honest outcome and what the viewer's fallback card
renders.

### A claim

`Claim` is the neutral supertype: **exactly what the service constructs on an
agent's behalf, plus what says which framework the conclusion is of.** Anything
a framework *judges* — a category, a severity, a mitigation — belongs to that
framework's own record.

```python
class Claim:
    id: str  # unique within its own block
    framework: str  # which framework this is a conclusion of
    framework_version: str  # required, non-empty
    title: str
    description: str
    affected_element_ids: list[str]  # element IDs in system_model — always resolve
    grounds: list[Ground]  # why it was raised — at least one, never empty


class RuledClaim(Claim):
    verdict: Verdict  # the critic's ruling
```

**`(framework, framework_version)` is one pair and both halves are required.** A
framework identifier with no version is uninterpretable one release later — the
standard a claim cites may renumber every requirement between major releases, and
nothing recovers the intent from an old report.

**`id` has no shared grammar.** Each package composes its own from its own ID
rule, so it is unique within its block and says nothing across blocks: two
packages composing `S-01` are naming two different claims, not colliding.

**`affected_element_ids` may be empty on the base**, because a framework can
raise a claim about a property of code rather than a position in the graph. The
referential check still runs over whatever IDs are there. STRIDE narrows it to
require at least one.

### STRIDE's own record

```python
class Threat(RuledClaim):
    category: StrideCategory  # spoofing | tampering | repudiation | ...
    severity: Severity
    mitigations: list[Mitigation]  # {summary, detail}
    confidence: Rating  # low | medium | high (critic-calibrated)
```

Category letters: `S` spoofing, `T` tampering, `R` repudiation,
`I` information-disclosure, `D` denial-of-service, `E` elevation-of-privilege.
A threat's `id` carries its category letter — **composed by the service** from
the package's own ID rule, and stamped with the lane in the same call, so the
letter and the lane cannot disagree. Nothing re-validates the composed string:
there is no longer a pattern to check it against, because a check would hide a
bad composition rather than catch it.

`StrideAnalysis` adds `missing_mitigations` to the block, and its `summary` adds
`by_category` and `by_severity` to the neutral three counts.

### ASVS's own record

```python
class RequirementRuling(RuledClaim):
    chapter: AsvsChapter  # encoding-and-sanitization | authentication | ...
```

One field, and nothing else. **ASVS grades nothing**: no severity, no confidence
and no mitigation, so the package carries no `severity_rubric.md` and the block's
`summary` adds `by_chapter` alone. A claim's `id` is the standard's own
version-safe reference — `v5.0.0-1.2.5` — composed by the service from the lane's
chapter number and the `<section>.<requirement>` key the agent supplied, so the
chapter in the ID, in the lane and in the record cannot disagree.

`affected_element_ids` is **empty on most ASVS claims**, and that is correct
rather than a gap: the majority of the standard's requirements address a coding
practice with no position in the graph, which is why the neutral `Claim` allows
an empty list where STRIDE narrows it.

**An ASVS claim never reports a pass.** `confirmed` means the requirement applies
and the input does not show it satisfied; `needs-info` means the input does not
settle it; `rejected` means the requirement does not apply. Verification needs
source code, configuration and the people who built the system, and a job here
carries prose. See
[ADR 0013](adr/0013-asvs-rules-applicability-and-never-a-pass.md).

`AsvsAnalysis` adds one more field to the block:

```python
    level: 1 | 2 | 3  # the requirement set this run was ruled against
```

It arrives from the job's own options, which the report also carries on `job`,
and it makes the block self-contained: a reader holding one block can tell which
requirement set produced the answer, and the block's own checks verify that every
requirement in that set appears exactly once. **A level-filtered run is a fork of
ASVS rather than ASVS**, and no output of one is a compliance result.

### `scope` — what a framework considered and raised nothing about

```python
class ScopeEntry:
    unit: str  # the requirement, lane or unit considered
    state: "applicable" | "not-applicable" | "needs-other-evidence"
    reason: str  # required unless applicable
    needs: (
        str  # the kind of evidence that would settle it; set iff needs-other-evidence
    )
```

A framework whose own presence tests rule a unit out has to **say so**: dropping
it silently leaves a reader unable to tell "considered and cleared" from "never
looked". The complement is not derived — every unit appears.

**Three states, and the third is not a weaker second.**

- `not-applicable` — the unit does not apply to a system of this shape. A
  finished answer, with the reason: either the framework's **Precondition**
  refused the model, or the package's own rules ruled the unit out before its
  lane ran (a chapter whose deciding presence test fired nowhere, or a
  requirement whose own technology is named nowhere). A draft the lane files
  on such a unit anyway is refused at the fan-in and listed in
  `dropped_claims`.
- `applicable` — the framework considered the unit and raised nothing.
- `needs-other-evidence` — the unit applies, a lane raised it, and the service
  withheld the claim because settling it needs evidence of a kind the job does
  not carry: `code`, `config` or `people`, where a job carries `prose`. The
  kind sits in `needs` as a field rather than a phrase in `reason`, so a reader
  can group by it. The answer is actionable by supplying that kind of input.

Both non-`applicable` states must state a reason, which is the rule `Verdict`
already applies to its two non-confirmed states. `needs` is set if and only if
the state is `needs-other-evidence`.

**The unit is the framework's own.** The neutral answer is the lane, because a
lane is the only unit the service knows without reading a catalog it does not
own. A package that holds one answers in its own units instead: an ASVS block
lists requirement identifiers, which at level 1 is 70 entries and at level 3 is
345. Every requirement in the selected level is either a claim in the block or an
entry here.

A framework whose **Precondition** refuses the system fills this list and nothing
else. Its block carries no claims and no coverage, and every unit appears here as
`not-applicable` with the reason: either the framework does not apply to a system
of this shape, or the input never said. The two reasons stay apart because the
remedy differs. A refusal is not a job failure — a job naming two frameworks, one
of them refused, still carries the other's analysis.

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

## What ties a report to its submission

`InputRef` ties the report to what was submitted without carrying the text:

```python
class SourceRef:
    kind: str  # description | transcript
    label: str  # the citation key an element's source_label names
    sha256: str  # digest of that one source's text


class InputRef:
    system_name: str
    sources: list[SourceRef]  # one per submitted source, in submitted order
    source_sha256: str  # aggregate, taken over the refs above
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
ground on a claim.

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
    kind: "quote" | "unknown-attribute" | "absent-attribute" | "derived-fact"
    text: str  # quote: the verbatim span, ≤1000 chars
    source_label: str  # quote: names one of input.sources
    element_id: str  # either attribute kind: resolves in system_model
    attribute: str  # either attribute kind: the attribute relied on
    flow_id: str  # derived-fact: a data flow in system_model
```

| `kind` | Carries | Reads as |
| --- | --- | --- |
| `quote` | `text` + `source_label` | the submitter's own words said this |
| `unknown-attribute` | `element_id` + `attribute` | this fact was never stated, so the threat stands unrefuted |
| `absent-attribute` | `element_id` + `attribute` | the input states this control is not there |
| `derived-fact` | `flow_id` | this flow's boundary crossing is the fact relied on |

**One flat model, not a discriminated union.** Fields belonging to the other
kinds are empty strings, and a record carrying a field its own kind does not
claim is rejected on construction rather than tolerated — so a consumer may read
the fields its `kind` names and ignore the rest. Why the shape is flat rather
than a tagged union, which it should be, is
[ADR 0002](adr/0002-finding-level-attribution.md).

**The two attribute kinds carry identical fields and different facts**, so a
consumer must switch on `kind` rather than on which fields are populated.
`unknown-attribute` is a question the submission left open — the threat resting
on it is conditional and typically routes to `needs-info`. `absent-attribute`
is the submission answering that question with *no*: `authentication: "none;
accepted by network position"`. Folding the two would report a control the
input described as missing as a gap in the description
([ADR 0012](adr/0012-the-catalog-carries-a-stated-absence.md)).

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
    claim_id: str  # the claim carrying it, unique within this block
    index: int  # position in that claim's `grounds` list
    reason: str
```

A quote that could not be found is **marked, not removed**: the entry still
renders, and `unverified_grounds` points at it by claim and index. A claim
where *nothing* verified is dropped and recorded in
[`dropped_claims`](#dropped_claims--claims-the-service-dropped-for-a-fault-in-one-entry)
instead. So an entry in this list means "one citation on an otherwise justified
finding did not match", which is worth showing a reader and is not grounds for
hiding the finding. The list is also empty when no source text was available to
check against, so it is evidence of a *failed* check, never of a check having
run.

## `unresolved_evidence` — references the catalog does not hold

```python
class UnresolvedEvidence:
    claim_id: str  # the claim that cited it
    reference: str  # the reference as written, e.g. "unknown:flow:ghost:authentication"
```

The [evidence catalog](#grounds--why-the-finding-was-raised) is closed and
derived, so a reference outside it names no fact and **no ground can be built
from it**. That is what separates this from `unverified_grounds`: an unverified
quote is a real ground whose text the service could not find, and it still
renders. Here there is nothing to render, so the entry is dropped and this mark
is the only trace.

**Marked per reference, dropped per claim.** A threat citing three facts, one
of them composed, is still justified by the two that resolve. A threat whose
evidence resolves to *nothing at all* has no justification left and is dropped
into [`dropped_claims`](#dropped_claims--claims-the-service-dropped-for-a-fault-in-one-entry),
because `grounds` is `min_length=1` and a finding resting on nothing is what
this schema refuses to represent. A dropped claim gets no entry here: this list
names a claim the block carries, and the groundless mark names the references
instead.

This narrowed a whole-job failure in 2.9. Agents compose well-formed references
— correct grammar, plausible element IDs, absent from the set — and a live sweep
lost 2 of 12 jobs to it. Discarding six lanes of analysis because one threat
named one fact that does not exist is the trade `unresolved_mentions` already
refused to make. See [ADR 0009](adr/0009-a-bad-reference-costs-its-entry.md).

A consumer that read "the job returned" as "every citation resolved" was relying
on an absence; this list is where that guarantee now lives.

## `repaired_quotes` — quotes rewritten to the source's own span

```python
class RepairedQuote:
    claim_id: str  # the claim carrying it
    index: int  # position in that claim's `grounds` list
    written: str  # what the agent wrote
    similarity: float  # the ratio that licensed the replacement
```

The ladder refused what the agent wrote, and the repair rung
(`analysis_service.grounding.repair_quote`) found a window of the named source
near enough to hand back. The ground now carries that window — the submitter's
words, whitespace collapsed — and this mark carries the agent's, so the
substitution is on the record. A repaired quote verifies, so an entry here is
never also in `unverified_grounds`.

The rung never accepts the agent's words: it replaces them. What can go wrong
is a replacement that says something different from what the claim rests on.
The critic reads the replaced span, and the mark shows the difference to a
reader. See [ADR 0018](adr/0018-the-repair-rung.md).

## `dropped_claims` — claims the service dropped for a fault in one entry

```python
class DroppedClaim:
    claim_id: str  # the claim the service dropped
    title: str  # what the agent called the finding
    reason: str  # which fault, in the agent's own words
```

One list for every reason a claim is dropped. `reason` says which, bounded to
500 characters, and repeats what was wrong — the quote not found, the references
not held, the element IDs not in the model, the schema error — because nothing
else persists a draft:

- the proposal failed its own schema: a verb outside the closed set, a severity
  value the enum does not hold, neither a reference nor a quote;
- every reference it cited is outside the catalog;
- its only grounds are quotes the source it names does not contain, or names a
  source the job does not carry;
- every element it named in `affected_element_ids` is absent from the model;
- its ID repeats an earlier draft's in the same framework.

Like `unknown_claim_identities`, the `claim_id` is deliberately absent from
`claims` and `rejected_claims`, and `title` is the only trace of what was found.
A proposal whose ID key was unreadable is keyed `<framework>:<lane>:proposal-<n>`.

Each of these used to fail the whole job. See
[ADR 0017](adr/0017-a-groundless-claim-costs-its-entry.md) and
[ADR 0019](adr/0019-one-entry-never-costs-the-job.md).

## `unresolved_references` — element IDs a claim named that do not exist

```python
class UnresolvedReference:
    claim_id: str  # the claim that named it
    element_id: str  # the ID as written, e.g. "process:web-api"
    reason: str  # empty: not in the model; otherwise why an existing ID was dropped
```

The structural twin of `unresolved_mentions`: an ID in `affected_element_ids`
rather than in prose. The reference is dropped from the claim and recorded here,
and the claim stands on the elements that resolved. A claim that named elements
and lost every one is in `dropped_claims` instead.

`reason` is empty when the model does not contain the ID. It reads `more than
one hop from every place the claim's grounds name` when the ID exists and the
claim's own grounds do not reach it: a cited flow reaches its two endpoints, a
cited element reaches its flows and their far ends, and nothing further. A
claim resting on quotes alone is bounded by the IDs its own description cites,
with no hop. Reach belongs in the description; `affected_element_ids` is what
the action lands on.

## `unresolved_mentions` — IDs the prose cites that do not exist

A threat names the elements it acts on twice: structurally in
`affected_element_ids`, and in prose inside `description`, which the analyze
prompt asks agents to write with element and flow IDs cited inline. The two are
checked differently on purpose.

```python
class UnresolvedMention:
    claim_id: str  # the claim whose description cites it
    mention: str  # the ID as written, e.g. "process:web-api"
```

A structural reference that does not resolve is dropped from the claim and
recorded in `unresolved_references`; a claim left naming nothing is dropped. The
same ID written into the argument is **marked** here, for the reason an
unfindable quote is: merge has no re-ask path, and a whole report is too much to
trade for a mistyped ID in a sentence.

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
    claim_id: str  # the threat offering no countermeasure
```

That case is mechanically recognizable, so the report distinguishes it. A
threat triggered by an `unknown` carries an `unknown-attribute` ground —
the trigger dictates the branch — so an empty `mitigations` list *with* such a
ground is the licensed case and is not marked. An empty list *without* one is,
and an `absent-attribute` ground does not license one: the fact is already in
hand, so a countermeasure can always be named.

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
    name_slug: str  # the slug both elements normalize to
    element_ids: list[str]  # the two or more IDs that share it
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
is why it carries element IDs and no `claim_id` — and why it sits on the
envelope beside the model it describes rather than on any block. It is
recomputable from the
report's own embedded `system_model`, by design.

## `coverage` — what each lane was offered

A claim count says how much a lane agent found. It cannot say whether a lane
that found nothing had examined the system and cleared it, or had never looked
at half of it. `coverage` carries one row **per lane** — every lane the package
declares, including one that filed nothing. A lane is a package's own unit, so
for STRIDE that is the six categories; the row is keyed by the lane slug rather
than by a category, because the six are one package's lane list rather than a
fact about the report.

```python
class LaneCoverage:
    lane: str  # the lane slug the package declares
    drafts: int  # claims this lane filed, before the critic ruled
    rules: int  # deterministic triggers defined in this lane
    rules_fired: int  # of those, how many produced a candidate here
    candidates: int  # structural leads handed to this agent
    candidates_cited: int  # leads whose every element the drafts cite
    elements: int  # elements in the system model
    elements_cited: int
    boundary_crossings: int
    boundary_crossings_cited: int
    unknown_controls: int  # attributes stating no verified control
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

Counted over the **drafts**, not the ruled claims: coverage is a fact about what
the lane agents did with the system, and a draft the critic later rejects was
still part of the system being examined.

## `analysis_context` — what informed the run

```python
class AnalysisContext:  # on the ENVELOPE
    instruction_sha256: str  # digest of every LLM node's composed instruction
    domain_packs: list[str]  # the reference packs this model earned, in selection order
```

Two more fields answer the same question **per framework**, and sit on the block
for the reason the split gives above — they name one package's own rules:

```python
fired_rules: list[str]  # this package's deterministic rules that matched, sorted
knowledge_docs: list[str]  # local-corpus documents those rules retrieved
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
  unioned across that framework's lanes. A note and a case live **inside the
  package** whose rules select them, while a domain pack stays shared, because
  the retrieval key decides the home. Retrieval is local and deterministic, so
  this list plus the checkout reproduces exactly the text the agents were shown.
  See [ADR 0008](adr/0008-retrieval-by-fired-rule.md) and
  [ADR 0011](adr/0011-package-text-follows-its-retrieval-key.md).

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

### What a `needs-info` may name

`related_unknowns` holds at least one entry, in one of two spellings. Which one
is true of the fact, never a matter of taste:

```python
class UnknownRef:
    element_id: str  # with attribute: the fact has a place in the model
    attribute: str
    subject: str  # alone: the fact has no place in the model
```

**The element spelling** points at a field a submitter can fill in, and is the
ordinary case for a claim about a specific element.

**The `subject` spelling** carries the question itself, for a fact the System
Model holds no slot for — whether a policy is documented, or what code does.
A framework ruling on requirements answers mostly that kind, so with only the
first spelling available its commonest verdict was inexpressible.

A reader should treat an entry pointing at an attribute that exists on every
element type — `notes`, say — with suspicion. Before the second spelling
existed, that was the only legal way to express a question about something the
model does not describe, and it passes every check while saying nothing.

### `unreconciled_rulings`

How the *first* critic pass failed to reconcile with its drafts, one message
per problem, before the bounded re-ask repaired it. **Empty means the first
pass was clean**, which is the reading that matters: a run that repaired itself
is a successful run but not a clean one, and the two were previously
indistinguishable in every artifact the service keeps.

A framework whose first pass never reconciles is running on its single retry.
That is worth knowing from a report rather than from a live run.

### Severity is derived, never asserted

`Severity` carries a qualitative `likelihood` and `impact` (`low|medium|high`);
`level` (`low|medium|high|critical`) is computed from a fixed matrix, so a model
cannot inflate it. `justification` explains the two inputs.

```python
from analysis_service import derive_severity_level

derive_severity_level("high", "high")  # "critical"
derive_severity_level("medium", "low")  # "low"
```

### Verdict

The critic's ruling on the threat:

```python
class Verdict:
    status: "confirmed" | "needs-info" | "rejected"
    reason: str  # required unless confirmed
    related_unknowns: list[UnknownRef]  # required iff needs-info
```

- `confirmed` — grounded in the model's facts.
- `needs-info` — plausible but hinges on an `unknown` attribute; each such
  attribute is named in `related_unknowns` (`{element_id, attribute}`). Stays in
  its block's `claims`.
- `rejected` — not grounded. Moves to `rejected_claims`.

**The service owns the shape; each package owns the question.** The three states
and the rules binding their fields are the service's, and every framework's
claims are ruled by its own critic — but what a state *asserts* is the
framework's. For STRIDE, `confirmed` means the threat holds. A framework that
rules on requirement applicability rather than on attacks uses the same three
words to answer a different question, which is why one shared rubric cannot
serve both.

Those three conditional rules hold on every verdict in a report, but they are
**not enforced on the critic's output** — that shape is checked at the review
seam, where a violation routes to a bounded re-ask instead of failing the job.
See [ADR 0005](adr/0005-verdict-shape-is-re-askable.md).

## Summary

Counts a UI can render without walking a block's claim list:

```python
class BlockSummary:  # on every framework block
    claim_count: int
    needs_info_count: int
    rejected_count: int


class StrideSummary(BlockSummary):  # what STRIDE's block carries
    by_category: dict[StrideCategory, int]
    by_severity: dict[SeverityLevel, int]
```

`summary` is computed from its own block's contents and must match them, so it
is safe to trust without recounting — a mismatched summary does not validate.

`elements_analyzed` is **on the envelope**, not here: it counts the one shared
model every framework ran against, so a per-block copy would be N copies of one
number.

## Authenticity

Every hash in this schema is **unkeyed**. `input.source_sha256`,
`analysis_context.instruction_sha256` and each node's `execution_fingerprint`
let a reader recompute a value and notice when the two disagree — and anyone who
edits the report recomputes them too. They establish internal consistency, and
nothing about who produced the report.

A **detached signature** answers that separately. See
[Report attestations](Report-Attestation.md). A valid signature says this report
came from that deployment and no covered byte has moved; it says nothing about
whether the findings are correct, and nothing about whether the run was
certified.

## Provenance

The report records the configured and provider-reported model identifiers, the
resolved decoding parameters, and related run metadata. These values let a
consumer audit the generation setup without an outside log. They are not a
complete causal record of the output: input and instruction identity are stored
in separate fields, and provider behavior remains probabilistic.

```python
class NodeRun:
    node: (
        str  # graph node name: "extract", "analyze_stride_spoofing", "critic_stride", …
    )
    model: str | None  # the SERVED build, vendor-prefixed; None for code-only nodes
    requested_model: str | None  # the CONFIGURED route this node asked for
    instruction_sha256: (
        str | None
    )  # 64-hex digest of the instructions the graph this node ran in carried
    execution_fingerprint: str | None  # 64-hex hash of the execution identity
    duration_ms: int
    usage: TokenUsage | None  # what the provider says the call cost; None if unmetered


class TokenUsage:
    prompt_tokens: int
    cached_prompt_tokens: int  # the part of prompt_tokens served from cache
    completion_tokens: int
    reasoning_tokens: int  # spent against max_output_tokens, absent from the output
    total_tokens: int
```

- **`sampling`** lists the decoding parameters each tier actually used, once per
  tier: `{"base": {"temperature": null, "max_output_tokens": 16384, ...},
  "strong": {...}}`. A parameter left to the model's default shows as `null`,
  which is what the shipped `temperature` looks like here. Values are whatever the param resolves
  to — a number, a count, a boolean, or a string for `thinking`, whose values are
  `"low"`, `"medium"` and `"high"` — so a consumer reading this block must not
  assume numbers.
- **`model`** is what *answered* — the build the provider reported, joined to its
  vendor prefix, e.g. `vertex_ai/gemini-2.5-pro-002`.
- **`requested_model`** is what was *asked for* — the configured route, e.g.
  `vertex_ai/gemini-2.5-pro`. Gemini is the example because one had to be, and
  because it is the one profiled family whose two values differ. On Claude,
  both fields hold the same string.
- **`instruction_sha256`** is the digest of every instruction the graph this
  node ran in carried, with the job-varying placeholders unexpanded. It contains
  no submitter bytes. On a report it repeats `analysis_context.instruction_sha256`,
  because a report holds one graph — but a `NodeRun` also travels alone in an
  eval sweep that folds several graphs into one list, and there the node's own
  copy is the only thing that says which instruction set produced its hash.
- **`execution_fingerprint`** is a hash of the versioned **execution identity**:
  the requested route, the served route, the tier's decoding parameters, the
  instruction digest, and the installed versions of the distributions between
  the node and its provider. One value identifying everything that decided what
  this call could answer. It can be recomputed from the artifact alone — the two
  routes and the digest are on the node, the parameters are in `sampling`, and
  the build versions are in `execution`. It does not hash the input or the
  output. Code-only nodes (like `assemble`) have all four as `null`.

  Both routes are in it because the served one is the *provider's claim* and
  nothing verifies it. With the served build alone, a compromised translator
  could name a build the deployment already blesses and certify whatever it
  liked; with the pair, the requested half comes from the deployment's own
  configuration and the translator has no say in it.

- **`execution`** is one block per report: `identity_version` (the schema the
  fingerprints hash), `served_model_trust` (always `"provider_reported"` — the
  provider named the build on its own event stream and nothing independent
  confirmed it), and `build` (the version of each distribution between a node
  and its provider). It also carries `review_independence` — how far this
  deployment required each framework's critic to sit from the analysis it
  checks, so a reader of a `shared` run sees the review was same-domain rather
  than inferring it from two node rows naming one model. That field is a
  statement and never a warning: a deployment whose selections do not satisfy
  its own policy fails to load, so no report exists to warn on. It is `null`
  only on a report with no LLM provenance at all.

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

> **`schema_version` 2.9** added `unresolved_evidence`, a sixth optional list of
> service-owned marks. Additive by the same rule as the marks before it. What
> moved beside it is a *behaviour*: an evidence reference the catalog does not
> hold used to fail the whole job, and is now dropped and marked, with only a
> groundless threat still failing.

> **`schema_version` 2.8** added `knowledge_docs` to that block: the local-corpus
> notes and cases the fired rules retrieved for the agents. Additive and
> service-owned like the rest of it, and under the same rule — a document
> informed the analysis and grounds nothing.

> **`schema_version` 3.0** is the framework cutover, and it is major on every
> count the rule names: fields move, a field changes its spelling, and one
> changes what it carries.
>
> - `threats` and seven other top-level fields became `analyses[].claims` and
>   their per-framework siblings. A consumer reading `report.threats` reads
>   nothing.
> - The four mark classes renamed `threat_id` to `claim_id`.
> - `coverage[].category` became `coverage[].lane`, and `CategoryCoverage`
>   became `LaneCoverage`.
> - Every claim gained the required `(framework, framework_version)` pair, and
>   `Summary` split into a neutral `BlockSummary` per block with
>   `elements_analyzed` left on the envelope.
> - The `StrideReport` type is now `Report`, and `analyses` is an ordered list
>   rather than a map.
> - Each block carries `unknown_claim_identities` and `dropped_claims`, two
>   further lists of service-owned marks. Each records a claim the service
>   *dropped*, so unlike the marks before them their `claim_id` names no claim
>   in the block. What moved beside `dropped_claims` is a behaviour: a claim
>   that lost every ground, every element, its ID to an earlier draft, or its
>   own schema used to fail the job. `unresolved_references` records an
>   element ID dropped from `affected_element_ids`.
> - Each block carries `repaired_quotes`. A quote ground's `text` is no longer
>   always what the agent wrote: where the ladder refused it and the source
>   held a near span, the text is that span and this list carries the agent's.
>
> **There is no version gate and none is needed.** `Report` forbids unknown
> fields, so a 2.10 payload carrying `threats` at the top level is refused by
> this model, and a 3.0 payload carrying `analyses` is refused by the old one.
> The no-shim behaviour falls out of the shapes rather than out of anything
> reading `schema_version`.

> **`schema_version` 2.10** corrected what `coverage[].elements_cited` counts,
> and holds every `*_cited` half to the total beside it. The definition above is
> unchanged; the computation counted prose citations raw, so an ID a description
> named that the model does not contain was counted as a cited element and the
> numerator could exceed its denominator. Minor by the rule above — no field is
> added, removed or renamed, and none changes meaning. Read it anyway if you
> stored rows: one carrying more cited than offered no longer validates, and a
> citation rate you computed off such a row was above 1.0 and is now correct.

The report records both model fields and **compares neither**. It doesn't need
to: if the build moves, the fingerprint moves with it, and the run stops
matching any list of blessed fingerprints — so the drift surfaces there rather
than through comparison logic here.

The fingerprint is computed **per node execution**. One analysis cannot loop, so
a node appears once in it; across an eval sweep a node appears once per case, and
a build that moved partway through gives that one node two different
fingerprints. That is the signal, not a defect.

The fingerprint makes model-and-sampling drift visible; it does not make the
result reproducible. `seed` is best-effort, and some vendors do not accept it.
Whether the observed fingerprints match a baseline that a *particular
deployment* has blessed is a separate question, answered against that
deployment's `config/blessed-fingerprints.toml` and recorded on the job—never
on the report. See
[Architecture → Provenance and certification](Architecture.md#provenance-and-certification).

A report produced without live models (the in-memory stub runner, or eval
fixtures) simply has an empty `sampling` and no fingerprints.

## Serialising

`Report` is a Pydantic model. For JSON (dates as ISO strings, the shape a
front end consumes):

```python
report.model_dump_json()  # str
report.model_dump(mode="json")  # dict
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
