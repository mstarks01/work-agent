# Whether the System Model materially limits extraction quality (#1033)

**Question.** The System Model makes an extraction commit to graph identity,
type, placement and interaction structure. #1033 asks whether those commitments
lose facts the sources state. Where they do, it asks how much of the measured
loss they account for.

> **Superseded in part.** The corpus this rests on carried five signed cases
> and 45 required rows. A maintainer sitting on 2026-09-18 signed the other
> eight, taking it to **13 cases and 119 rows**, and every figure below
> recomputes differently on it. The recomputed stage table and what it
> changes are on
> [#1033](https://github.com/mstarks01/work-agent/issues/1033), and the placement
> finding it reopens is
> [#1052](https://github.com/mstarks01/work-agent/issues/1052). Read this file as
> the record of what was measured at `756b065`.

**This file records facts and a recommendation. It rules nothing.** The ruling
belongs to #1033 and to whatever issues it opens. No production default,
framework behaviour or public schema changed for this investigation.

**Method.** Three kinds of evidence, kept apart, because they answer different
questions and only one of them measures prevalence.

1. **The repository, at `756b065`.** `src/analysis_service/system_model.py`,
   `validation.py`, `assertions.py`, `factbundle.py`, `patch.py`, `evidence.py`,
   and the two **Framework Package** rule modules.
2. **Ten hand-authored shapes**, in `evals/harness/bottleneck.py`. Each one is a
   few sentences written by hand with the facts a careful reader finds in them
   written out beside the words. No model wrote them and no corpus holds them.
   Run them with
   `uv run python -m evals.harness.run bottleneck`, and
   `tests/test_evals_bottleneck.py` holds each to the survival it took here.
3. **The archived arm sweep** of #1003, now under
   `evals/emissions/20260917T-arms-luna-pro/`. Three reading routes over 13
   cases, one repeat, five of which carry a signed reference. Charge every
   missed reference row with
   `uv run python -m evals.harness.run bottleneck A=evals/emissions/20260917T-arms-luna-pro/arm-A.json B=evals/emissions/20260917T-arms-luna-pro/arm-B2.json E=evals/emissions/20260917T-arms-luna-pro/arm-E.json`.

**Evidence 2 proves that a shape can be lost. Only evidence 3 says how often
one is.** A hand-authored fixture is a diagnostic. One person writes its facts
and declares what must survive, so it demonstrates a mechanism and measures no
prevalence. Every prevalence figure below comes from emissions this repository
already paid for.

**A caution about the denominator.** Five cases carry a signed reference and
they hold 45 required rows between them. #226 records that no corpus case has
been read by a person, and `evals/README.md` records that the reference facts
are agent-authored with a reviewer's signature on the rulings. So the
prevalence figures are strong evidence about these five cases and weak evidence
about a population nobody sampled.

---

## TOP-LINE VERDICT

**Overall: a demonstrated minor contributor.** The System Model's commitments
account for at most 24 of 89 missed required rows. Relaxing the one commitment
that recovers anything returns **6 rows of 45 on the production route**, at a
cost of 2 new wrong claims. The reading accounts for 52 of the 89. So the
representation is a real mechanism and a small one, and #1033's hypothesis H1
is not supported as the material explanation of extraction quality within this
scope.

#1033 also asks for separate verdicts on representational capacity, adapter
fidelity and generation effects. They differ, and reporting them as one would
be wrong.

**Adapter fidelity: not supported as a material explanation within the tested
scope.** Over 89 missed required rows across three reading routes, **none** is
charged to a refusal by the resolver or by the assertion gate. The nine defects
the #1003 execution audit found are fixed and each is a regression test. The
deterministic path between a model's emission and the catalog dropped nothing a
reference row wanted.

**Representational capacity: a demonstrated contributor that reaches the
declared band and does not clear it.** Two mechanisms, and they are not the
same size.

- **Identity is a function of the name.** 18 of 89 misses (20%) are a row about
  the right thing under a name the reference spells otherwise, and 8 of those
  sit on the production route. Relaxing that one constraint over the same
  emissions recovers **6 required rows of 45 on arm A**, and introduces 2 new
  wrong claims. `validate` refuses two elements that share a name, so a source
  that names two distinct things alike has no legal model at all. **All 18 sit
  on an interaction**, whose identity carries both endpoint IDs and the label.
- **Placement costs nothing measurable on the production route.** 6 of 89
  misses (7%) are charged to a graph that held no element for the subject.
  **Every one of the six is on a facts-first route.** The graph-first route
  loses none. That agrees with
  [ADR 0038](../adr/0038-a-component-reaches-the-graph-only-in-a-zone.md), which
  measured the same contract at zero cost and stated it rather than changing it.

**Generation effects: inconclusive, and this is the open question.** 52 of 89
misses (58%) are the reading. In 29 of them no proposed row names the predicate
at all. In 23 the model proposed the predicate about something else. No offline
instrument says whether the output contract caused either, because a model that
never wrote a fact leaves no trace of why. #1033's own rule applies here:
perfect inputs surviving resolution do not rule out the output schema causing
the omission. Part 5 states the bounded comparison that would answer it.

**Consumer reach is the finding nobody asked for.** 13 of the 45 required rows
(29%) carry a predicate the graph has no field for. In a deployment that sets
nothing, those facts reach no framework rule even when they are read perfectly,
because the assertion catalog is off by default. That is a larger share of the
required facts than every representational loss measured here put together.

---

## PART 1 — Source to consumer, and what each stage requires

### 1.1 The two routes

| | graph-first (the default) | facts-first (#1003 arms B and E) |
| --- | --- | --- |
| Selected by | nothing; it is the default | `ANALYSIS_FACTS_FIRST_EXTRACTION`, `ANALYSIS_FACTS_SPLIT_EXTRACTION` |
| The model emits | a **System Model** | a **Source Fact Bundle** in local handles |
| Identity decided by | the model, then rewritten from the name | code, from the mention's text |
| Placement decided by | the model | a fact row, or the sole zone, or nothing |
| Assertion rows | a second `assert` call, under `ANALYSIS_ASSERTIONS` | composed from the bundle by `resolve_bundle` |
| Prose fields | written by the model | never written: a mention carries a name and no description |

The last row is a fact about the experiment rather than about the schema. It
matters to a reader who compares the arms. `description`, `notes` and
`technology` are empty on every element a bundle builds.
`docs/research/system-model-evolution.md` measured 62% of ASVS lane leads coming
from `description` and `notes`. An arm comparison on assertion recall does not
see that loss. An end-to-end comparison would.

### 1.2 What must be resolved before a fact survives

| Fact category | What must already be settled | Native representation | Survives as | Reader |
| --- | --- | --- | --- | --- |
| A component exists | its type, its name, **its zone** | an element | structurally | every rule |
| Two components share a name | which one this is | nothing | not at all | — |
| A thing that stores and runs | one type per thing | two elements under coined names | structurally, renamed | every rule |
| An interaction | both endpoints placed, a label | a **Data Flow** | structurally | every rule |
| Two interfaces, one endpoint pair | a distinct label each | two flows | structurally | every rule |
| A transport or storage control | the interaction or component | a control string, and a catalog row | structurally | `control_state`, 11 readers |
| A stated absence | the same | `none` in the graph, `absent` in the catalog | structurally | `control_state` |
| An unknown with a reason | the same | `unknown` in the graph, a reason in the catalog | structurally, reason dropped | `control_state` |
| A scoped permission | a principal, a resource, an operation | a catalog row, no graph field | catalog only | the **Evidence Catalog**, under `ANALYSIS_ASSERTIONS` |
| A credential fact | a credential subject | a catalog row, no graph field | catalog only | the same |
| Two sources disagreeing | nothing | two rows; `conflicts()` derives it | catalog only | the same |
| An account distinct from its owner | nothing | two principal subjects | catalog only | the same |
| Read against write | the interaction | `operations`, a closed vocabulary | structurally | STRIDE's `_unverified_write_to_store` |
| A data classification | the component | `data_classification` | structurally | ASVS's `CLASSIFIED_STORE_TEST` |

**Three commitments are required and one is not obvious.** A component's type
and name are required because the element classes demand them. Its **zone** is
required because `ZonedElement.trust_zone` must name a **Trust Boundary** the
model holds, and `validate` reports `invalid-reference` otherwise. Nothing else
is: every other attribute admits the unknown sentinel, and `unstated_fields`
raises on a required field that does not, rather than picking a value.

**Identity is where the commitment bites hardest, and it is not placement.**
`derive_element_id` composes an **Element ID** from the type and the name, and
`validate` reports `id-mismatch` where an element carries another. The
extraction pipeline normalizes IDs before the gate, so two elements sharing a
name arrive at `duplicate-id` instead. Both readings agree: **a name is an
identity**, and a source that uses one name for two things has no legal model.

### 1.3 Who actually reads a fact

`docs/research/assertion-readers.md` Part 2 is the reader inventory, read at
`f86ca62`, and nothing here re-derives it. Its finding stands: **eleven of the
fifteen production readers route on `control_state`**, which reads the leading
token of a control string. One reader renders the whole value for a **Lane
Agent** to read as prose.

What this investigation adds is the count on the other side. The predicate
registry holds 18 predicates and **11 of them project into no graph attribute**.
Over the five signed cases, **13 of the 45 required rows (29%) carry one of those
11**. Their only reader is the **Evidence Catalog**'s `assertion:` reference
(ADR 0036), and `ANALYSIS_ASSERTIONS` is off by default, so a deployment that
sets nothing never offers one.

So a scoped grant, an absent second factor, a shared credential and a rotation
interval are facts this service can read, can keep, can ground and cannot use.

---

## PART 2 — Qualifying the evaluator

#1033 asks that the instrument be qualified before it is used, with positive and
negative controls, and that a correction be isolated and frozen before any
comparison. The instrument is `evals/harness/replay.py`'s matcher.

**It was not fit when #1003's pilot ran, and the corrections are in.** The
execution audit found five scoring defects; all are fixed and each is driven by
a test. Reading the contract item by item:

| Contract item | Where it is driven | Control |
| --- | --- | --- |
| One produced row answers one reference row | `test_evals_replay.py::TestOneProducedRowAnswersOneReferenceRow` | both |
| Approved semantic equivalence without identical labels | the six signed-alias tests in `TestEveryReferenceRowTakesOneFate` | both |
| Reference-order invariance | `test_evals_bottleneck.py::TestTheMatcherDoesNotReadTheReferenceInOrder` | positive, three seeds |
| Scope | `test_a_signed_qualifier_alias_turns_a_rescoped_row_into_a_found_one` | both |
| Polarity | `test_a_stated_fact_answered_as_absent_is_a_wrong_value` | both |
| Certainty | `test_a_hedged_unknown_answered_as_silent_is_not_found` | both |
| Material exclusivity | `test_dropping_an_exclusivity_the_reference_holds_is_not_found` | both |
| Unreviewed apart from adjudicated | `test_a_dropped_row_is_omitted_and_a_new_row_is_unreviewed` | both |
| Rejected rows counted by row | `test_a_rejected_row_is_counted_by_row_not_by_issue` | positive |
| A failed job keeps its denominator | `arms.ArmRun.unusable`, driven in `test_evals_arms.py` | positive |
| An unsigned reference grades nothing | `TestAnUnsignedReferenceGradesNothing` | negative |

**One item had no test and now has one.** Reference-order invariance was
asserted by the mechanism (`_assigned` runs a level at a time) and by no
property. The new test shuffles a signed reference under three seeds and holds
every row's fate and every count fixed. It is the qualification item most likely
to rot, because it is a property of an algorithm rather than of a branch.

**Endpoint equality and quote presence are not proof, and the instrument agrees.**
`under_aliases` applies only a **signed** ruling; an unsigned alias rewrites
nothing (`test_an_unsigned_alias_rewrites_nothing`). A span that verifies says
the words are in the source and says nothing about entailment, which is #719's
open question and not this one's.

**No reference, alias or scorer was changed for this investigation.** The two
code changes made here are named in Part 6 and neither moves a score.

---

## PART 3 — The ten shapes

Every fixture is read three ways: can the schema hold the facts when a person
writes them out, does the adapter keep them, and does anything read them
afterwards. `cause` names which of the three the loss belongs to.

| fixture | asks | survival | schema holds | cause |
| --- | --- | --- | --- | --- |
| `unknown-placement` | existence and supported security facts survive an unstated placement | question | yes | adapter |
| `conflicting-placement` | no invented certainty, and the supported non-placement fact survives | question | yes | adapter |
| `store-and-process` | storage and execution behaviour stay expressible and distinguishable | question | yes | adapter |
| `distinct-subjects` | distinct subjects are not merged for a convenient binding | catalog | yes | consumer |
| `same-name` | identity does not collapse two distinct referents | lost, structural | **no** | schema |
| `parallel-interfaces` | two interfaces between one pair take distinct bindings | structural | yes | none |
| `scope-and-polarity` | scope and polarity survive, and silence is not read as absence | catalog | yes | consumer |
| `hedge-and-conflict` | uncertainty and conflict are retained rather than settled without evidence | catalog | yes | consumer |
| `operations-and-classification` | structured meaning survives into its actual consumer | structural | yes | none |
| `failed-correction` | the original supported facts survive a correction that fails | structural | yes | none |

### 3.1 The one shape the schema cannot hold

`same-name` is the only fixture whose hand-written construction the gate
refuses. Two offices each run "a scheduler"; one is internet-facing and one is
not. Written out as two elements, the gate reports `id-mismatch` twice, because
`process:scheduler` is the only legal ID for either name. Given legal IDs, the
gate reports `duplicate-id`. The facts-first resolver reaches the same place by
its own route and reports `duplicate-element`, and the second scheduler's
exposure fact falls with it.

`prompts/extract.md` already carries the workaround and carries it as two rules
that meet here: "Two elements sharing a name share an ID, which is an error:
name them apart", and "**Name it as the text does**, in the singular, with
nothing added … Attach no qualifier the text does not attach to that thing." A
model reading a source that genuinely names two things alike cannot obey both.

> **The prompt half is answered; the schema half stands.** #1040 replaced those
> two rules with one on 2026-09-19. A name still comes from the text, and two
> things of one type that the text calls by one name take the text's own
> distinguishing word in front, with the shared name in `notes`. The schema
> finding above is unchanged — it still holds one element per type and name —
> and the shape reached the gate **zero times in 520 archived extraction
> emissions**, so the rule was shipped without a paid run.

### 3.2 The three the adapter loses and the schema holds

All three are the facts-first route, and all three refuse rather than guess.

`unknown-placement` is ADR 0038 rule 2 reaching two rows further than the ADR
states. Take a licence server whose network no source names, in a bundle that
names two zones. It does not enter the graph. **The interaction to it falls
with it, and so does the transport fact on that interaction.** Before this
investigation the resolver charged those two rows `dangling-endpoint` and
`dangling-subject` — the codes for a bundle that contradicts itself. So a
reader who counted what rule 2 cost undercounted it.
They are now `unplaced-endpoint` and `unplaced-subject`, which is what the ADR
already said they were.

`conflicting-placement` is the same mechanism under a contradiction rather than
a silence, and it costs more: the storage-encryption fact the second source
states plainly is lost with the component, because a component that cannot be
placed holds no facts at all.

`store-and-process` is a workbook that holds customer addresses and runs a macro.
The System Model holds it as a **Data Store** and a **Process** under two coined
names. A bundle mention carries one role, and the resolver will not choose
between two, so the classification fact falls.

### 3.3 The four that reach a reader

`parallel-interfaces`, `operations-and-classification` and `failed-correction`
meet their property in full. A flow identity carries its label, so two
interfaces between one endpoint pair stay separately addressable. `operations`
reaches `_unverified_write_to_store` and `data_classification` reaches
`CLASSIFIED_STORE_TEST`. A review batch whose replacement the resolver refuses
is discarded whole, and the row it would have replaced still stands.

`hedge-and-conflict` keeps both readings and `conflicts()` derives the
disagreement, and the projection then writes `unknown` into the graph rather
than picking — which is right, and which means the disagreement itself exists
only in the catalog.

### 3.4 The three that reach no rule

`distinct-subjects` and `scope-and-polarity` both end in the catalog. A service
account and the person who owns it stay two subjects; a grant scoped to an
operation keeps its scope; a stated absence keeps its polarity. Nothing in a
default deployment reads any of them.

---

## PART 4 — What the archived emissions actually lost

Three reading routes, five signed cases, 45 required rows each, one repeat.
Every required row the matcher did not call `found` is charged to the earliest
stage that did not carry it.

| arm | unread | unmodelled | renamed | misattached | refused | scored | total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A (graph-first) | 5 | 0 | 8 | 12 | 0 | 3 | 28 |
| B (facts-first) | 11 | 2 | 7 | 6 | 0 | 5 | 31 |
| E (facts-first, split) | 13 | 4 | 3 | 5 | 0 | 5 | 30 |
| all | 29 | 6 | 18 | 23 | 0 | 13 | 89 |

`unread` is a predicate no proposed row names. `unmodelled` is a predicate the
run proposed and a graph that holds no element paired with the reference's
subject. `renamed` is a row about a produced element the alignment pairs with
the reference's subject. `misattached` is a row about an element that is not it.
`refused` is a row the resolver or the gate dropped. `scored` is a row that
reached the catalog and that the matcher adjudicated as something other than
found.

**Four readings, in the order they matter.**

**Nothing is charged to a refusal.** Not one row of 89, on any arm. Whatever
else is wrong, the code between a model's emission and the catalog is not
dropping facts that a reference row wanted. That is the direct measurement of
#1033's mechanism 2, and it is the strongest negative result here.

**`unmodelled` is six rows, and all six are facts-first.** The production route
loses nothing to a graph that held no element for the subject. This is the same
answer the oracle gave from the other side — a perfect reading survives the
deterministic path whole — measured now on real emissions instead of on a
ceiling.

**`renamed` is 18 rows, and it is the representational finding.** The reading
found the right thing and spelled its name otherwise. An element ID is a
function of the name. **All 18 sit on a Data Flow** — 11
`authentication-mechanism`, 6 `transport-encryption` and 1
`destination-verification`. None sits on a component. A flow ID carries both
endpoint IDs and the label. So a label in other words makes another identity,
and so does an endpoint under another name.

The archived comparison already prices the recovery. Its `aligned` column
credits this class exactly. It reads 0.553 against a recall of 0.427 on arm A,
0.443 against 0.383 on B, and 0.487 against 0.410 on E.

**The reading is 52 of 89.** 29 predicates nobody named and 23 named about
something else. No schema change and no adapter fix recovers any of them.

### 4.1 One relaxed constraint, over the same emissions

#1033 asks that the archived emissions be replayed once as they stand and once
through one isolated relaxation, with both the gains and the new errors
reported. The narrowest relaxation available is the identity finding itself:
**treat a produced element the alignment pairs with the reference's subject as
that subject.** Nothing else moves — the same emissions, the same catalog, the
same matcher, the same required rows, and a reviewer's signed alias still wins
wherever one exists.

| arm | required | found | relaxed | recovered | wrong | relaxed wrong |
| --- | --- | --- | --- | --- | --- | --- |
| A | 45 | 17 | 23 | +6 | 3 | 5 |
| B | 45 | 14 | 18 | +4 | 5 | 8 |
| E | 45 | 15 | 19 | +4 | 5 | 4 |

**The recovery is smaller than the class.** 8 rows on arm A are charged
`renamed`, and relaxing the constraint recovers 6 of them. The other two bind
and then disagree, which is the honest half of the measurement: a pairing that
takes a row onto an element also takes a wrong row onto one. Arm A gains two new
disagreements, arm B gains three, and arm E loses one.

**This is a ceiling and never a proposal.** An alignment is a comparison this
repository makes between a produced model and a blessed one, and production has
no blessed model to align to. What the number says is how much of arm A's miss
is a name rather than a fact: **6 rows of 45, at a cost of 2 new wrong claims.**

### 4.2 What this comparison is, and what it is not

The unit is a reference row and the comparison is **within** an archived run
rather than between two runs, so nothing here is a paired arm comparison and
nothing here carries an interval. #1003's own endpoint is the arm comparison,
it is unchanged by this work, and it still reads B − A = −0.045 with an interval
of −0.114 to +0.000.

**Materiality is the repository's existing band, adopted rather than invented.**
`evals/TUNING.md` step 3 prices a fix in must-finds against the run-to-run
spread, and a ceiling inside the spread earns no run. Read against the 45-row
denominator: a mechanism worth a paid run has to reach about six rows on the arm
it would be applied to. `renamed` reaches 8 on arm A and recovers 6 of them under
4.1's relaxation, so it clears the band. `unmodelled` reaches 0 on arm A and 4
at most anywhere, so it does not. `refused` reaches 0.

**Part 4 is an attribution and not a test**, so no threshold gates the numbers
themselves. The band gates the recommendation in Part 7. A reader who wants a
threshold declared ahead of the figures should read Part 4 as descriptive.

### 4.3 What is missing, and what nothing here can see

- **The #1003 pilot's own artifacts are gone.** `evals/runs/` is not tracked,
  and nobody copied the pilot's sweeps to `evals/emissions/`. So every recall,
  interval and cost in #1003's early comments is an operator report nothing
  recomputes. The arm sweep archived here is the later, reproducible one.
- **Eight of the 13 cases carry no signed reference**, so 8 of every arm's 13
  runs are graded by nothing and appear in no denominator above.
- **No arm ran arms C or D**, so the bounded review pass has no measurement at
  all.
- **A row a model considered and did not write leaves no trace.** `unread` is
  therefore a floor on the reading's cost and never a diagnosis of it.
- **An end-to-end effect is invisible here.** Every figure is an assertion row.
  What a lost fact costs a **Claim** is `evals/harness/extraction_losses.py`'s
  question and needs the paired sweep #742 asks for.

---

## PART 5 — Generation effects stay open

**The offline evidence cannot close this.** 52 of 89 misses are the reading, the
deterministic path refuses nothing, and a perfect reading survives it whole. Two
explanations fit all of that equally: the model reads badly, or the output
contract shapes what it writes down. #1033 says so itself, and the arms already
run do not separate them — arm B changes the contract and the reading order
together, and reads *worse*.

**The bounded comparison that would answer it**, stated so somebody can run it
rather than re-plan it:

- **The arms.** Arm A unchanged, against one arm that differs in a single way.
  That arm asks the reading for facts in the source's own words, with no graph
  identity, no placement and no type. It is arm B's prompt without the
  inventory arm B also asks for. The point is to move one thing.
- **The unit** is a case, and the endpoint is required-row recall over the
  signed reference, with `aligned` reported beside it.
- **The threshold.** Six required rows of 45 on the arm under test, which is the
  band Part 4.1 adopts. Below it, no adoption whatever the point estimate.
- **What must be frozen first**: the commit, the corpus digest, the signed
  references and their aliases, the prompts, the routing and the sampling. The
  archive under `evals/emissions/` is the shape to write it to.
- **The cases.** The five signed cases are development data, and this
  investigation read them.
  A confirmation needs fresh cases with a signed reference, which is #744 and
  #226, and both are open. **This comparison cannot confirm anything until one
  of them lands.**
- **The cost.** The archived sweep charged $0.52 for arm A over 13 cases,
  $0.29 for arm B and $0.44 for arm E, on `luna-pro`. A two-arm comparison is
  of that order. The spend decision is the user's and this file asks for
  nothing.

**A cheaper measurement comes first, and it is free.** The `renamed` class is 18
rows and needs no model call to study: the archived emissions hold every flow
label the three arms wrote, beside the reference's own. Reading them says
whether the labels differ by a verb, by a synonym or by a whole reading, and
that decides whether the answer is a prompt rule, an alias ruling or a change to
flow identity. That is #1015's question and the archive now answers it offline.

---

## PART 6 — Reconciling the prior audit

The audit linked from #1003 examined `a0ecbee`. Every probe in it is now a
regression test. Checked against `756b065`:

| The audit's probe | Now |
| --- | --- |
| Unknown placement removes a known component and its interactions | **Still the behaviour, and now named.** ADR 0038 states the contract; the fixture measures it; the two rows it cost are charged as questions rather than as defects (below) |
| Rejected placement evidence still sets graph topology | Fixed, PR #1035 |
| A failed assertion replacement leaves the original deleted | Fixed; `failed-correction` drives it |
| One produced flow takes credit for two reference flows | Fixed; `_assigned` |
| Reference ordering changes strict recall | Fixed; now driven as a property |
| Certainty and exclusivity differences do not affect correctness | Fixed; both have fates |
| Missing graph-reference value alignment | Fixed; an alias rewrites a value |
| Operations and classification gaps in the experimental representation | Closed: `operations` is a bundle field and `data-classification` is registry version 4 |

**Two code changes were made here, and both are accounting rather than
behaviour.**

`factbundle._interactions` now charges an interaction whose endpoint the graph
could not place as `unresolved` under `unplaced-endpoint`, and `_facts` charges
every fact about that interaction as `unplaced-subject`. Before, both read as a
bundle that contradicted itself. No row changes its outcome; what changes is
which sidecar line it appears on, and ADR 0038 rule 2 already said this is what
they are.

`replay.ROUTES_OF` replaces `NODE_OF`: a mode admits reading **routes** rather
than reading nodes, so the split `inventory` and `rows` reading is one arm over
two prompts. Without it arm E could not be placed on an arm and could not be
archived at all. `Arm.nodes` and `Arm.instructions` are tuples for the same
reason.

---

## PART 7 — Recommendations

Smallest first. Each names its expected benefit, and the band of Part 4.1
decides which earns a paid run.

**1. Rule how to name two things a source names alike.** `prompts/extract.md`
tells a model to name an element as the text does. It also tells the model to
name two same-named elements apart. It gives no rule for doing both. State one:
take the distinguishing word the text uses nearby, and record the source's own
name in `notes`. **Benefit: it removes the one shape the schema cannot hold.**
It is a prompt change, so it moves extraction and needs its own measurement
before adoption. Price it on the archived extraction sweeps first.

**2. Read the 18 `renamed` rows off the archive.** It is free and offline, and
it decides between three corrections: a prompt rule about flow labels, an alias
ruling in the reference, or a change to flow identity. Do it before anything
else, because the other two depend on what it says. **Benefit: it bounds the
one mechanism that reaches the band, at no cost.** It belongs to #1015.

**3. Decide whether an unprojected predicate may reach a rule at all.** 13 of
the 45 required rows carry one. Today those rows reach a reader only where a
deployment sets `ANALYSIS_ASSERTIONS`. **Benefit: it is larger than every
representational loss measured here.** It is a product decision rather than a
defect, and it belongs to #926 Phase 4 and to #741.

**4. Leave the placement contract alone.** ADR 0038 names three numbers that
would reopen it. This investigation measured two of them and both stayed at
zero on the production route.

**5. Do not adopt facts-first on this evidence.** It reads worse, it loses the
prose fields that answer most ASVS lane leads, and every `unmodelled` miss
measured here is one of its own.

### What would falsify this

- **A case where the graph cannot place a component that carries a required
  fact.** The fixture proves the mechanism, and the archive says it costs
  nothing on the production route. A corpus case where it costs something
  reopens ADR 0038.
- **An archive that charges a refusal.** `test_evals_bottleneck.py` fails the
  day one does, which is deliberate.
- **A reading of the 18 `renamed` rows showing the labels differ by a whole
  reading rather than by a word.** Then the class is the reading after all, and
  the identity finding shrinks.

---

## Framework parity

Stated as a property, so it answers for a package nobody wrote yet: **a
framework reads the System Model, and the System Model is the service's.** Every
finding here is about a shared field or a shared identity rule. So every package
sees each finding at once, and no package branches on a name.

- **A framework whose rules read a typed field** is unaffected by the identity
  finding until an element goes missing, and then loses every rule about it.
  STRIDE is the measured case: `_unverified_write_to_store` reads `operations` and
  `_unprotected_transit_crossing` reads `encryption_in_transit`, and the fixtures drive both.
- **A framework whose rules read free text** is affected twice over, because the
  facts-first route writes no prose at all. ASVS is the measured case: its 23
  presence tests match terms, and `docs/research/system-model-evolution.md`
  measured 62% of its lane leads coming from `description` and `notes`.
- **A framework whose claims carry a catalog identifier** needs nothing of its
  own here. A predicate states a fact about the submitted sources rather than
  about a method, so the registry answers for that package already.

**The experiment is vendor-neutral.** Every figure recomputes with no provider
call. The archived sweep ran on one model through one route, and nothing here
reads a vendor, a tier or a served identifier; a second vendor's emissions would
be charged by the same reader.

## Licensing

Nothing here reproduces a sentence from a governed file. The ASVS rule names
above are short identifiers of this repository's own code, not requirement text.

## Provenance

Every figure recomputes offline. Parts 1 and 3 come from
`uv run python -m evals.harness.run bottleneck` at `756b065`. Part 4 comes from
the same command with the three archived artifacts named, over
`evals/emissions/20260917T-arms-luna-pro/` and the five signed cases of
`evals/corpus/`. Part 2 names the tests that drive each contract item. The
`aligned` figures in 4 come from the archived comparison the sweep was written
with, recomputable with `run.py score-arms` and `run.py compare-arms`.
