# What reads a control attribute, and what one attribute cannot hold (wayfinder #926)

**Question.** #926 proposes a source-backed assertion catalog beside the **System
Model**. Phase 1 asks two things first. Which code reads the facts that would
move into that catalog? And how much of a source's statement does the one string
a control attribute holds actually carry?

**This file records facts. It rules nothing.** The ruling is
[ADR 0034](../adr/0034-an-assertion-is-a-scoped-fact-with-a-support-span.md).

**Method.** Two kinds of evidence, kept apart.

1. **The repository, at `f86ca62`.** The reader inventory in Part 2 comes from
   reading the modules named there. Each row cites a file and a line.
2. **The 13 blessed corpus models**, `evals/corpus/*/model.json`, 227 elements.
   Every count in Part 1 comes from `probe_assertion_facts.py` beside this file.
   Run it from the repository root with
   `uv run python docs/research/probe_assertion_facts.py`.

**A caution about the corpus.** #226 records that no corpus case is read by a
person. The 227 elements are agent-authored. They are strong evidence of what
this service's extraction produces, and weak evidence of what a human modeller
would write.

---

## Part 1 — One attribute, several facts

### What the five control attributes hold

| attribute | stated | absent | unverified |
| --- | ---: | ---: | ---: |
| `authentication` | 15 | 4 | 70 |
| `encryption_in_transit` | 5 | 3 | 81 |
| `encryption_at_rest` | 1 | 0 | 33 |
| `exposure` | 23 | 0 | 10 |
| `data_classification` | 12 | 0 | 22 |

`control_state` classifies each value by its leading token. The **Evidence
Catalog** publishes an entry for every `unverified` value and for every `absent`
one. It publishes nothing for a `stated` value, by design: a catalog entry says
a fact is missing from the model, and a stated control is a fact the model
holds.

### The 21 stated mechanism values

`basis.IN_SCOPE` names the three attributes that hold a mechanism:
`authentication`, `encryption_in_transit` and `encryption_at_rest`. The corpus
states 21 values across them. The probe marks each value two ways. `A` marks a
value that also states an absence, by the negation list
`analysis_service.grounding._NEGATIONS` already uses. `S` marks a value that
carries a separator, so a writer joined two clauses.

| marks | case | attribute | value |
| --- | --- | --- | --- |
| | 01 | `encryption_at_rest` | customer-managed key (CMEK) |
| `AS` | 01 | `authentication` | session cookie issued after email and password login; no MFA |
| `S` | 01 | `authentication` | single shared application account with full read/write; password from an environment variable |
| | 01 | `authentication` | order service's own service account |
| | 01 | `encryption_in_transit` | TLS |
| `AS` | 02 | `authentication` | fleet-wide pre-shared key, shared by every device and never rotated |
| | 02 | `authentication` | company SSO |
| `AS` | 03 | `authentication` | static per-partner key issued at onboarding, never rotated |
| | 03 | `encryption_in_transit` | SSH transport (SFTP) |
| `AS` | 03 | `authentication` | company SSO; dataset-wide grant with no column-level restriction |
| `AS` | 04 | `authentication` | per-team API key in a header, never expired or rotated |
| | 04 | `authentication` | model server's own service account |
| | 05 | `encryption_in_transit` | encrypted (marked as the one encrypted link) |
| `AS` | 07 | `authentication` | a shared build token, the same for every pipeline, never rotated since the pipeline was set up |
| `AS` | 08 | `authentication` | the broker accepts the provider's assertion as a sign-in; which colleagues the provider may vouch for is not written down |
| `AS` | 08 | `authentication` | the token alone: its store-manager group decides, the colleague's own store is never checked, and nothing calls back to the broker to re-check them |
| `AS` | 10 | `authentication` | sign-in exists for some readers; the mechanism, its strength and how sessions are handled are not stated |
| | 10 | `encryption_in_transit` | HTTPS |
| `S` | 12 | `authentication` | username and password issued by the vendor |
| | 13 | `encryption_in_transit` | encrypted (HTTPS) |
| `AS` | 13 | `authentication` | an API token issued when the importer was built and never rotated since |

**Ten of the 21 state an absence. The evidence catalog offers nothing for any of
them.** The probe resolves each of the ten against the catalog its own case
derives, and finds zero entries. Each of the ten is a flow's `authentication`.
The absence reaches a **Lane Agent** as prose inside the model, and it reaches
no rule, no **Evidence Reference** and no figure.

That is the audit's MFA probe, already present in the blessed reference ten
times. `tests/test_evals_modes.py::TestWhatNoFigureHereReaches` pins the same
gap from the scorer's side: reverse `no MFA` into `MFA enforced for every
shopper` and every extraction figure still reads a perfect score.

**Twelve of the 21 carry a separator.** A separator is a floor under the count
of facts, never the count. Read the ten marked `AS` and the facts are of four
kinds the graph has no field for:

- **A second control's absence.** "no MFA", "no column-level restriction".
- **A credential's sharing scope.** "shared by every device", "single shared
  application account", "the same for every pipeline", "per-partner".
- **A credential's lifecycle.** "never rotated" appears six times, "never
  expired" once.
- **An authorization grant.** "full read/write", "dataset-wide grant", "its
  store-manager group decides".

Case 10's value is the clearest instance: `sign-in exists for some readers; the
mechanism, its strength and how sessions are handled are not stated`. One string
holds a scoped positive fact and three unknowns. `control_state` reads it as
`stated`, so the catalog offers nothing and every candidate rule about a missing
authentication control stays quiet.

### What a reader can and cannot recover

A value's leading token is the only position any rule reads. So a reader
recovers three things from these 21 values and nothing else: that the attribute
names a mechanism, which element it sits on, and which attribute it is. It
cannot recover which principal the mechanism applies to, which operation it
guards, whether a second control is absent, or which words of the source support
any part of it.

---

## Part 2 — Every production reader of the migrated facts

Read at `f86ca62`. "The fact" column names what the reader takes from the value.

| # | Reader | Site | The fact it takes | A value with two facts |
| --- | --- | --- | --- | --- |
| 1 | Extraction prompt | `prompts/extract.md` | Writes the value. Asks for one string per attribute. | Produces one. |
| 2 | Validity gate | `validation.py:263`, `:294` | Refuses a blank control, and refuses a value whose leading token is an ambiguous negation. | Passes. Both checks read the leading token. |
| 3 | Repair pass | `prompts/repair.md`, driven by `ValidationIssue` | Rewrites a value the gate refused. | Never sees one. |
| 4 | Control classification | `analysis.py:84` (`CONTROL_ATTRIBUTES`), `analysis.py:control_state` | `unverified`, `absent` or `stated`, from the leading token. | Reads `stated`. |
| 5 | Evidence catalog | `evidence.py:191` | An `unknown:` or `absent:` **Evidence Reference**, or nothing. | Offers nothing. |
| 6 | STRIDE candidate rules | `frameworks/stride/rules.py:99`, `:121`, `:139`, `:184`, `:207`, `:239`, `:267`, `:288`, `:355`, `:377` | Whether the control is unverified, through `is_unverified`. Ten of the eleven rules read a control attribute; seven route on `is_unverified`. | Stays quiet. |
| 7 | ASVS candidate rules | `frameworks/asvs/rules.py:695` | The same question for `encryption_in_transit`. | Stays quiet. |
| 8 | ASVS precondition | `frameworks/asvs/rules.py:814` | `interface_kind` and `protocol`, to decide whether the framework runs at all. | Not applicable: both hold closed or near-closed values. |
| 9 | Domain selection | `domains.py:48` (`_SCANNED_FIELDS`) | Word matches over `technology`, `protocol`, `authentication` and `name`. | Matches on any clause. This is the one reader that reads past the leading token, and it reads for a term rather than for a fact. |
| 10 | Basis diagnostic | `basis.py:175` (`CONTROL_SCOPE`) | Whether a stated value shares a content token with the source it cites. | Passes on one clause's words. |
| 11 | Coverage | `coverage.py:190` | How many unstated controls a lane cited. | Counts nothing: the pair is not unstated. |
| 12 | Report | `report.py` | Renders the value verbatim. | Renders both facts. |
| 13 | Critic re-ask check | `critic.py:236` | Whether a critic's `related_unknowns` names an attribute the element declares. | Accepts the pair. The check stops at the attribute's existence. |
| 14 | Extraction scorer | `evals/harness/modes.py:244` (`_SCORED_ATTRIBUTES`) | `control_state`, compared against the blessed model's. | Agrees with any other value of the same state. |
| 15 | Token caps | `token_caps.py:86` | Bounds the extraction prompt that asks for the value. | Not applicable. |

**One reader decides for eleven of the fifteen.** Rows 4 to 7, 10, 11 and 14 all
route on `control_state`, and rows 2 and 3 route on the same leading-token read
through `leading_word`. So the assertion layer has one seam to feed, not eleven.

**One reader renders the whole value, and it does not read it as a fact.** The
report renders the string, and so does the model block every lane agent and
every critic reads. A **Lane Agent** therefore reads "never
rotated" and can raise a claim about it — but the only **Ground** it may cite is
a `quote`, because the catalog holds no entry for the fact. Row 9 matches terms
for pack selection, which is not a fact either.

---

## Part 3 — What the graph has no field for

The four kinds of fact in Part 1 map onto #926's proposed predicates. This table
says where each one lands today.

| Proposed predicate | The field today | What a projection loses |
| --- | --- | --- |
| authentication mechanism | `DataFlow.authentication` | The scope. One flow carries one value for every principal that uses it. |
| credential presented | `DataFlow.authentication` | Shares the field with the mechanism. |
| MFA requirement | none | Everything. A stated absence reads as a stated control. |
| authorization grant | none | Everything. Case 03 and case 08 record grants inside `authentication`. |
| credential sharing scope | none | Everything. Four values record one inside `authentication`. |
| credential lifecycle | none | Everything. Seven values record one inside `authentication`. |
| transport encryption | `DataFlow.encryption_in_transit` | The scope, and any exclusivity claim. Case 05's value states one. |
| storage encryption | `DataStore.encryption_at_rest` | The scope. |
| signature verification | none | Everything. |
| destination verification | none | Everything. #925 rules case 09's fax leg to `unknown` for this reason. |
| network membership | `ZonedElement.trust_zone` | Ownership and authority. One flat zone holds one of the three. |
| administrative authority | `TrustBoundary.kind` | The zone kind holds one value, so a zone that is both a tenant zone and a privilege zone states one. |
| tenant ownership | `TrustBoundary.kind` | The same. |

Six of the thirteen have no field at all. Two share one field with two other
predicates. Three share the zone kind, which holds one value.

---

## Recommendation

**Add the assertion catalog. Do not widen the element schema to hold these six
predicates.** Three findings support that, and this file rules none of them.

1. **The six predicates with no field are not six missing columns.** Each needs a
   scope and a support span to be worth anything, and a column holds neither. A
   `mfa_requirement` column repeats the defect one attribute lower: it would hold
   one value per element, read by its leading token, supported by nothing.
2. **The seam is narrow.** `control_state` decides for eleven of the fifteen
   readers, so a projection that feeds it keeps every reader working while the
   catalog grows beside it.
3. **The corpus already writes assertions.** Twelve of the 21 stated values are
   joined clauses, and ten state an absence. Extraction produces the facts today
   and has nowhere to put them, which is why they arrive as prose in a field that
   reads as one mechanism.
