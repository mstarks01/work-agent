# Whether the System Model should grow past its STRIDE shape (wayfinder #483)

**Question.** The **System Model** carries five element types from the classic DFD-based
STRIDE-per-element taxonomy. **ASVS** is the second **Framework Package**, #473 proposes
deterministic rules that users write, and #484 proposes repository evidence. Does the model
need more vocabulary, and can a published taxonomy supply it better than a design of our own?

**This file records facts and a recommendation. It rules nothing.** The ruling belongs to
#483 and to an ADR after it. [ADR 0010](../adr/0010-package-cannot-extend-the-evidence-catalog.md)
governs any change that reaches the **Evidence Catalog**, and its three tests apply to every
proposal below.

**Method.** Three kinds of evidence, kept apart.

1. **The repository, at `8729415`.** `src/analysis_service/system_model.py`,
   `analysis.py`, `frameworks/stride/rules.py` and `frameworks/asvs/rules.py`.
2. **The 13 blessed corpus models**, `evals/corpus/*/model.json`, 223 elements.
   Every count comes from `probe_model_vocabulary.py` beside this file. Run it from the
   repository root with `uv run python docs/research/probe_model_vocabulary.py`.
3. **The published schemas themselves**, read at their own sources on 2026-09-04.
   CycloneDX 1.7 comes from `https://cyclonedx.org/schema/bom-1.7.schema.json`. The other
   four come from the project pages named in Part 3.

**A caution about the corpus.** #226 records that no corpus case has been read by a person.
The 223 elements below are agent-authored. They are strong evidence of what *this service's
extraction* produces, and weak evidence of what a human modeller would write.

---

## TOP-LINE VERDICT

**Recommendation: Option B, narrowly. Keep the five element types. Add controlled traits
where the corpus already shows one, and borrow the names from CycloneDX where the shape is
already the same. Reject the formal ontology.**

Five findings support that.

1. **The model is not too small. It is untyped in the places that carry the most weight.**
   54% of every ASVS lane lead comes from `description`, and 8% more comes from `notes`. One
   lead in 225 comes from `technology`, the field the design intends for it. No new node type
   fixes that, because the facts are already present. They are present as prose.

2. **One string field carries several facts, and code reads the first token of it.**
   The corpus holds 16 distinct stated `authentication` values. 12 of them fuse two or more
   separate facts: a mechanism, a credential scope, a rotation interval, a second factor, an
   authorization decision. `control_state` reads the leading token and returns `stated` for
   all 16, so a rule cannot reach any of the rest.

3. **Three of those 16 say inside themselves that a fact is not stated, and the code reads
   them as `stated`.** That is a measured defect shape, not a modelling opinion. It is the
   strongest single argument for a typed trait, because a typed trait would hold the
   `unknown` where the code already looks.

4. **CycloneDX 1.7 already carries this service's shape, under other names.** A CycloneDX
   `service` holds `trustZone`, `authenticated`, `x-trust-boundary`, `endpoints` and a `data`
   list whose entries hold `flow`, `classification`, `source` and `destination`. That is a
   **Process**, a **Trust Boundary**, a **Data Flow** and an **Asset** tag. Borrowing the
   names costs nothing and buys a later export.

5. **No candidate answers a question the service asks today, so no candidate earns adoption.**
   OSCAL, D3FEND and UCO each model something real. None of them models it as a *fact an
   extraction agent recovers from a paragraph of prose*, which is the only input this service
   has. Their value is a mapping target after extraction, never a schema for it.

**What this rejects.** Option D, a formal ontology, on the burden of proof the ticket sets.
Option C's OSCAL arm, for the core. New node types for identity, dependency and deployment.
The reason for all three is the same: the constraint on this service is what an extraction
agent can recover from prose, and every one of them raises that cost while the measured gap
sits somewhere else.

---

## PART 1 — What the model carries today

**Five element types**, in `system_model.py`. **External Entity**, **Process**, **Data
Store**, **Data Flow** and **Trust Boundary**. Each one adds its own fields to a common
`_Element` that holds identity and provenance.

**Three field classes**, and the difference decides everything below.

| Class | Fields | What code can do with one |
| --- | --- | --- |
| Closed enum | `kind` on entities and boundaries, `exposure`, `interface_kind` | Test a value directly |
| Control string | `authentication`, `encryption_in_transit`, `encryption_at_rest`, `data_classification` | Read the leading token only, through `control_state` |
| Free prose | `name`, `description`, `notes`, `technology`, `protocol`, `data_description` | Match a term with a regular expression |

**One controlled vocabulary**, the eight asset tags in `CORE_ASSET_TAGS`. A deployment
extends it in config.

**One derived fact**, the **Boundary Crossing**. `boundary_crossings()` computes it from
zones alone.

**Two rule styles over that model.** STRIDE writes 11 functions, and every one reads a typed
field or a control state. ASVS writes 23 presence tests and 5 structural rules, and every one of
the 23 matches a term against a string field. That split is the whole subject of this file.

---

## PART 2 — Six measured limitations

### 2.1 Free prose answers 62% of ASVS lane leads

The probe fires every ASVS presence test against all 13 corpus models and records which
attribute answered it. 225 answers:

| Attribute | Answers | Share | Field class |
| --- | ---: | ---: | --- |
| `description` | 122 | 54% | Free prose |
| `name` | 42 | 19% | Free prose |
| `notes` | 18 | 8% | Free prose |
| `protocol` | 17 | 8% | Free prose |
| `authentication` | 16 | 7% | Control string |
| `data_description` | 5 | 2% | Free prose |
| `encryption_in_transit` | 4 | 2% | Control string |
| `technology` | 1 | 0% | Free prose |

`technology` is the field a designer would expect to answer a question about a database or a
web framework. It answers once in 225.

### 2.2 Remove `description` and `notes`, and 18% of the leads go

The probe re-fires every test with those two fields hidden. 82 tests fire across the 13 cases
with them, and 67 without. The 15 that disappear are these:

| Lost in cases | Rule |
| ---: | --- |
| 3 | `authentication-authentication` |
| 2 | `secure-coding-and-architecture-third-party-component` |
| 2 | `authorization-privileged-role` |
| 2 | `configuration-secret-material` |
| 1 each | `file-upload`, `log-or-audit-trail`, `password-auth`, `encryption`, `cors`, `sessions` |

Read the list as a specification. The concepts that survive only in prose are dependency,
privilege, secret material and audit trail — which is, near enough, the list #483 asks
whether to add.

### 2.3 One control string carries up to four facts

16 distinct stated `authentication` values across the corpus. Counted by what a reader can
name in them:

- **12 of 16 fuse two or more facts.**
- **5 carry a key rotation or expiry fact**, such as `never rotated since the pipeline was
  set up`.
- **5 carry a credential scope fact**, such as `a shared build token, the same for every
  pipeline`.
- **4 carry an authorization fact in an authentication field**, such as `company SSO;
  dataset-wide grant with no column-level restriction`.

The richest is `the token alone: its store-manager group decides, the colleague's own store
is never checked, and nothing calls back to the broker to re-check them`. It names a
mechanism, an authorization rule, a tenant scope failure and an absent revocation check. A
rule reads the token `the`, and `control_state` returns `stated`.

### 2.4 Three values say a fact is missing, and the code calls them `stated`

The probe finds three, all on `authentication`, all on a flow:

- `the broker accepts the provider's assertion as a sign-in; which colleagues the provider
  may vouch for is not written down`
- `the dialled destination is never verified; nobody checks the fax arrived at the right
  place`
- `sign-in exists for some readers; the mechanism, its strength and how sessions are handled
  are not stated`

`is_unverified` returns `False` for each. No STRIDE rule about an unverified caller fires on
them, and the **Evidence Catalog** publishes no `unknown` entry for them either. The value is
half stated and half unknown, and the schema has one slot.

This is the sharpest finding in the file. It needs no new element type and no external
taxonomy. It needs one attribute split into a state and a description.

### 2.5 `data_classification` has a de-facto vocabulary that nothing enforces

The schema types it `str`, and `extract.md` names no vocabulary for it. The corpus holds six
distinct stated values. Three of them are schema words — `confidential`, `internal`,
`public`. Three are prose — `customer data`, `Customer name, address, and the card they paid
with`, `Delivery addresses only`.

That is a controlled vocabulary the extraction found on its own, and drifts out of half the
time. #470 assumes this field holds schema words; it holds them in 3 cases of 6.

### 2.6 Two spellings of one fact on `encryption_in_transit`

Five distinct stated values: `HTTPS`, `TLS`, `SSH transport (SFTP)`, `encrypted (HTTPS)`,
`encrypted (marked as the one encrypted link)`. Two of the five state a protocol, and two
state a state and then the protocol in a bracket. `protocol` on the same flow is a separate
field. A rule that asks "is this channel protected" and a rule that asks "what protocol runs
here" both have to read both fields.

---

## PART 3 — The five candidates

### 3.1 OWASP Threat Model Library and TM-BOM

**What it is.** The OWASP Threat Model Library publishes threat models in a JSON format it
calls TM-BOM. Its objects are Actor, Component, Data Store with Data Sets, Trust Zone, Trust
Boundary, Data Flow, Threat Persona, Threat, Risk, Control, Mitigation Plan, Assumption,
Scope and Diagram. Threat Dragon plans to adopt it and to retire its own TMF format.

**Status, and it decides the answer.** The OWASP Developer Guide states that as of January
2026 a CycloneDX working group is still defining TM-BOM, to become part of ECMA-424. It is
not released. The library's own schema page says a further version will follow once TM-BOM
ships, to stay compatible with it.

**The mapping.** It is the closest of the five, and near enough one-to-one on the
architecture half.

| TM-BOM | This service | Note |
| --- | --- | --- |
| Actor | **External Entity** | `kind` splits human from external system |
| Component | **Process** | |
| Data Store | **Data Store** | |
| Data Flow | **Data Flow** | |
| Trust Zone, Trust Boundary | **Trust Boundary** | One flat type here, two there |
| Data Set | **Asset** tags plus `data_description` | A tag list here, an object there |
| Assumption | **Assumption** | Same name, same job |
| Scope | **Scope Entry** | Ours is per framework, theirs is per model |
| Threat, Risk, Control, Mitigation Plan, Threat Persona | **Claim** and STRIDE's **Threat** | A framework conclusion, not an architecture fact |

**What is true architecture and what is a framework conclusion.** The first eight rows are
architecture. The last row is not, and the ticket's own design constraint rules it out: the
System Model says what the system is, and a framework says what those facts mean. TM-BOM
carries both in one document because it is a threat model exchange format, and this service
carries them in a **Report** for the same reason.

**Verdict. Align vocabulary, do not adopt the schema.** An unreleased schema cannot be a
dependency. Two concepts are worth borrowing now, and both are cheap: **Data Set** as a
name for a richer `data_description`, and the Trust Zone and Trust Boundary split, which
this service does not need yet and should not invent differently if it ever does. Revisit
when TM-BOM ships as part of ECMA-424, and revisit it then as an *export* format for a
finished **Report**, never as the extraction schema.

### 3.2 CycloneDX 1.7

**What it is.** A bill-of-materials specification, ratified as ECMA-424 2nd Edition in
December 2025. It covers software, services, hardware, machine-learning models, cryptography
and manufacturing inventory in one schema.

**The finding.** A CycloneDX `service` is this service's **Process** with different field
names. Read from the 1.7 schema directly:

| CycloneDX `service` field | Type | This service |
| --- | --- | --- |
| `trustZone` | string, "the name of the trust zone the service resides in" | `trust_zone` |
| `x-trust-boundary` | boolean, "use of the service crosses a trust zone or boundary" | **Boundary Crossing**, but derived rather than asserted |
| `authenticated` | boolean | `authentication`, but a boolean rather than a string |
| `endpoints` | array of IRI | Nothing. `interface_kind` answers a coarser question |
| `data[]` | `serviceData` | **Data Flow** |

And `serviceData` holds `flow` (`inbound`, `outbound`, `bi-directional`, `unknown`),
`classification`, `name`, `description`, `governance`, `source` and `destination`. That is a
**Data Flow** with `data_classification` and `data_description` on it.

**Three further parts are relevant and are not about services.**

- **`component.type`** is a closed 13-value enum: `application`, `framework`, `library`,
  `container`, `platform`, `operating-system`, `device`, `device-driver`, `firmware`, `file`,
  `machine-learning-model`, `data`, `cryptographic-asset`. That is the closed vocabulary the
  `technology` field lacks.
- **`cryptoProperties`** types a cryptographic asset as `algorithm`, `certificate`,
  `protocol` or `related-crypto-material`. `encryption_in_transit` and `encryption_at_rest`
  are unstructured versions of that.
- **`definitions.standards`** holds a standard with a name, a version, an owner,
  `requirements` and `levels`. ASVS's `catalog.json` holds exactly those fields for ASVS
  5.0.0.

**The counterweight.** CycloneDX describes an inventory somebody produced from a build or a
deployment. It does not describe a system somebody wrote a paragraph about. `endpoints` wants
a URI; a submitter writes "the checkout page". A `component` wants a name and a version; a
submitter writes "our Postgres database". Importing a CycloneDX document is realistic.
Extracting one from prose is not.

**Verdict. Borrow three vocabularies, keep the schema at arm's length.** Take
`dataFlowDirection`'s four values, `component.type`'s 13 values and `cryptoProperties`'
four asset types as *closed vocabularies for existing fields*. Note `definitions.standards`
as the shape ASVS's catalog would export to. Treat a CycloneDX document as a future evidence
input under #484, never as the System Model.

### 3.3 NIST OSCAL

**What it is.** A NIST format for control catalogs, profiles, component definitions, system
security plans and assessment results, in XML, JSON and YAML. FedRAMP requires
machine-readable authorization data from September 2026, which is what drives its adoption.

**The mapping.** Two OSCAL models touch this service.

- **Catalog and profile** hold controls and a baseline selection over them. ASVS's
  `catalog.json` and its level filter do the same job for one standard.
- **Component definition** states how a component implements a control, independent of a
  system. That is a claim about a control, which this service never has: an ASVS claim here
  never reports a pass.

**The problem with the second one.** OSCAL's centre of gravity is the *implementation
statement* — a person writes down how a control is met, and an assessor rules on it. This
service has no implementation statement and cannot produce one. Its ASVS record carries three
states, and none of them is a pass, on purpose. Adopting OSCAL's shape for controls would
give the service a field it must always leave empty.

**Verdict. Not for the core, and not for the catalog either.** A catalog format is only
worth adopting if something outside reads it, and nothing does today. If a policy framework
ever needs to consume a published control catalog, weigh OSCAL against CycloneDX
`definitions.standards` then, on which one the source publishes. Note that ASVS 5.0.0 itself
publishes neither: it publishes a flat JSON of its own, which is what
`asvs-representation.md` recorded.

### 3.4 MITRE D3FEND

**What it is.** An OWL 2 DL knowledge graph of defensive techniques, funded by the NSA
Cybersecurity Directorate and managed by MITRE. Its Digital Artifact Ontology classifies the
digital objects a defence acts on.

**The mapping.** The Digital Artifact Ontology holds classes for a process, a network
session, a file, a credential, a user account and much else. Several overlap with a
**Process** or an **Asset** tag by name.

**The problem.** The overlap is by name and not by role. D3FEND's artifacts exist to say what
a *countermeasure* senses and modifies. This service's elements exist to say what a *system
is*. A D3FEND identifier on an element would name a class that means something adjacent, and
a reader would have to know the difference to use it.

**Verdict. A downstream mapping target at most, and not now.** The natural place for a D3FEND
identifier is a mitigation on a STRIDE **Threat**, where a countermeasure is the subject. That
is a **Claim** field, not a System Model field, and it belongs to whichever ticket takes up
mitigation vocabulary. It has no bearing on extraction.

### 3.5 Unified Cyber Ontology

**What it is.** A community ontology for representing cyber information across tools, at
version 1.4.0, preparing for a backwards-incompatible 2.0.0.

**The problem, and it is the ticket's own test.** UCO's value is interoperability between
tools that already hold structured data — chiefly digital forensics. Ask this ticket's
question of it: can a competent extractor recover a UCO object reliably from ordinary prose?
For most of the ontology the answer is no, because the objects are observations from an
instrument rather than statements in a description.

**Verdict. No, at any layer.** Adopting UCO would turn extraction into ontology population,
which the ticket names as the failure it fears. Mapping *to* UCO after extraction is possible
and buys nothing, because no consumer asks for it.

### 3.6 STIX, ATT&CK, CAPEC and CWE

The ticket already scopes these as downstream mappings, and that is right. A CWE on a
STRIDE **Threat** or an ASVS ruling is a **Claim** field. None of the four describes an
architecture, so none of them touches the System Model. This file adds one caution: a CWE
identifier on a claim changes **Claim identity**, which `docs/agents/claim-identity.md`
governs, and it is a versioned rule rather than a free field. Any such work goes through that
document.

---

## PART 4 — Comparison matrix

Scored against the ticket's criteria. **High** is good in every row except the last three,
where high is the cost.

| Criterion | TM-BOM | CycloneDX | OSCAL | D3FEND | UCO | Traits and relations of our own |
| --- | --- | --- | --- | --- | --- | --- |
| Fit with the current graph | High | High for services | Low | Low | Low | High |
| Framework neutrality | Medium — carries threats and controls | High | Low — control-shaped | Low — defence-shaped | High | High |
| Extraction from prose | Medium | Low | Low | Very low | Very low | High |
| Deterministic validation | Medium | High | High | High | High | High |
| Use for #473 rules | Medium | High | Low | Low | Low | High |
| Use across STRIDE and ASVS | Medium | High | ASVS only | Neither | Neither | High |
| External structured evidence | Low | High | Medium | Low | Low | None |
| Interoperability value | High, once released | High | High in FedRAMP | Low | Low | None |
| Dependency complexity | High — unreleased | Medium | High | High | Very high | None |
| Schema and version burden | High | Medium | High | Medium | High | Low |
| Provenance impact | Neutral | Positive | Positive | Neutral | Neutral | Neutral |
| Maintenance risk | High until it ships | Medium | High | Medium | High | Low |

**Read the matrix as two answers, not one.** For *representing the system this service
extracts*, traits of our own win every row that matters, and every published schema loses the
extraction row. For *exchanging a finished artifact*, CycloneDX wins and TM-BOM will compete
once it ships. Those are different problems, and treating them as one is the mistake this
matrix exists to prevent.

---

## PART 5 — What to borrow, and what not to add

### Recommended: four typed traits, no new node type

Each one splits a fact the corpus already carries out of a string that also carries something
else. Each one keeps the string.

1. **A control state beside every control string.** A closed enum — `stated`, `absent`,
   `unknown` — set by extraction rather than parsed from a leading token. `control_state`
   becomes a field read instead of a regular expression. **This is the highest-value change
   in the file**, and 2.4 is the measured reason: three corpus values are half stated and
   half unknown today, and the schema cannot hold both.
2. **A credential scope on `authentication`.** `per-principal`, `shared`, `unknown`. 5 of
   16 stated values carry it in prose, and every one of the five is a finding a reader would
   want.
3. **A closed `data_classification` vocabulary.** `public`, `internal`, `confidential`,
   `restricted`, `unknown`, with the free text moving to `data_description`. 2.5 measures the
   drift this removes.
4. **A closed `component_type` on `Process` and `Data Store`**, from CycloneDX
   `component.type`. It gives `technology` a machine-readable half and would replace the
   `tech:database` presence test with a field test.

### Recommended: one relation, and only if a rule needs it

`runs-as`, from an element to a named identity. It is the one relation the corpus asks for
twice — `order service's own service account` and `model server's own service account` — and
the one that lets a rule ask whether two elements share a principal. **Do not add it before a
rule reads it.** A relation nothing reads is a field extraction can get wrong for free.

### Explicitly not recommended

- **A node type for an identity, a dependency, a deployment or an endpoint.** Every one
  raises the extraction cost of every job, and the measured gap is a typed trait rather than
  a missing node.
- **A nested trust zone.** The flat zone is a stated design decision, and nothing in the
  corpus or in the rules is blocked by it.
- **A control object.** This service's ASVS record never reports a pass. A control object
  would carry a state the service can never fill.
- **Human process facts** — a review cadence, a training record, a vulnerability service
  level. #415 and #484 are the right homes for these, as evidence kinds rather than
  architecture.
- **Any RDF or OWL representation.** It fails the extraction test and buys no consumer.

---

## PART 6 — Effect on extraction and validation

**The four traits add four closed enums and remove no field.** Every one has a legal
`unknown`, so a silent input produces a valid model exactly as it does today.

**The failure mode is a wrong value rather than a missing one, and that is worse.** A free
string that says too little is honest. A closed enum forces a choice, and #468 already
records what happens when extraction is told to pick: it picks, and nothing downstream can
see that the pick was contested. Every new enum needs `unknown` to be the cheap answer and
the prompt to say so.

**Prompt cost.** `extract.md` names six security-relevant attributes today. Four more traits
means four more lines and four more vocabulary lists. That is a small addition to a prompt
that already carries two worked examples, and it costs prompt tokens on every job.

**Repair cost.** The **repair rung** ([ADR 0018](../adr/0018-the-repair-rung.md)) spends one
pass on a model that fails its gate. A closed enum is a new way to fail one. Measure the
repair rate before and after, on the corpus, and treat a rise as the cost of the change.

**The measurement that decides it.** Re-run the sweep and compare recall and precision. The
number 2.4 predicts is a rise in STRIDE recall, because three flows in the corpus currently
fire no unverified-caller rule and would fire one.

---

## PART 7 — Effect on the #473 rule engine

**This is where the traits pay.** #473 asks for a declarative rule format whose operators are
`equals`, `contains`, `in`, `exists`, `unknown` and `absent` over model fields.

**A closed enum is what those operators need. A control string is not.** Today a declarative
rule over `authentication` can only do what ASVS's presence tests do — match a term. That is
the natural-language classification #473 rules out in its own non-goals. With a control state
field, `authentication.state: unknown` is a real operator on a real value, and the rule reads
the same fact `control_state` reads.

**So the order matters.** Type the traits first, then build the rule engine on them. A
declarative engine over today's fields would ship a term-matching language, and a term
matcher is the thing both tickets say they do not want.

**One caution, from `CLAUDE.md`.** A rule engine and `control_state` would be two readers of
one rule. Give the state one reader — the field — and let the engine and every native rule
call it. Do not let the engine parse a leading token of its own.

---

## PART 8 — Target architecture and staged path

**The target.** The five element types, unchanged. Four closed traits beside the strings they
split. One relation, if a rule earns it. Every external standard stays outside the model:
CycloneDX as an evidence input under #484 and as an export, TM-BOM as an export once it
ships, CWE and D3FEND as claim fields under `claim-identity.md`.

**A staged path, each stage shippable alone.**

1. **The control state field.** One enum, four attributes, and `control_state` becomes a
   field read. Fix 2.4's three corpus values. Measure the sweep.
2. **`data_classification` as a closed vocabulary.** Move the free text to
   `data_description`. Smallest change, and the one with a corpus edit attached — every path
   under `evals/corpus/` makes the whole pull request a sitting.
3. **`component_type` from CycloneDX.** Replace ASVS's `tech:database` presence test with a
   field test, and measure whether the leads change.
4. **Credential scope**, only if 1 and 3 leave a rule that wants it.
5. **`runs-as`**, only when a rule reads it.

**Every stage bumps `schema_version` and re-keys nothing.** A **Claim**'s identity composes
from a framework, a lane, element IDs and an action verb, and none of the four changes.

---

## PART 9 — What would falsify this

**Finding 1 falls** if a human reading of the corpus shows the extraction wrote facts into
`description` that it should have written into `technology`. Then the gap is a prompt gap
rather than a schema gap, and no trait is needed. #226 is the ticket that would show this,
and it is open.

**Finding 4 falls** if a caller asks to submit a CycloneDX document. Then CycloneDX stops
being a vocabulary to borrow from and becomes an input to parse, and #484 owns it.

**The TM-BOM verdict falls the day TM-BOM ships.** Recheck it against ECMA-424 rather than
against this file.

---

## Framework parity

Stated as a property, so it answers for a package nobody has written: **a framework reads the
System Model, and the System Model is the service's.** Every proposal here changes a shared
field, so every package sees the change at once and no package branches on it.

- **A framework whose rules read a typed field** gains a direct test where it had a term
  match. STRIDE already reads typed fields, so its 11 rules keep working unchanged and gain
  the three flows of 2.4.
- **A framework whose rules read free text** gains the most. ASVS is the measured case: all 23 of
  its presence tests match a term today.
- **A framework whose claims carry a catalog identifier** needs no vocabulary of its own for
  any of this, because a trait feeds its applicability test rather than its claim identity.

No proposal here reaches the **Evidence Catalog** without ADR 0010's three tests. A control
state field would pass all three: it is a pure function of the **Valid System Model**, every
lane of every framework receives it, and its ID builds from an element ID and an attribute
name the model already carries.

## Licensing

Nothing in this file reproduces a sentence from a governed source. The CycloneDX field names
and descriptions above are short identifiers and their schema `description` strings, quoted
to identify a field. CycloneDX publishes under Apache-2.0. TM-BOM is unreleased, and no text
of it appears here. If a later change imports a published vocabulary as data, that data needs
a `CONTENT_LICENSE` entry, a `THIRD_PARTY` entry and a `NOTICE` section, exactly as the ASVS
package carries.

## Provenance

Every number in Parts 1 and 2 recomputes from `probe_model_vocabulary.py` against
`evals/corpus/*/model.json` at `8729415`. The external schema facts come from the sources
named in the method, read on 2026-09-04. CycloneDX 1.7 was read from the published JSON
schema rather than from a page about it.
