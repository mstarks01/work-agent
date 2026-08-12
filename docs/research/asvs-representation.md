# What ASVS 5.0 needs a system representation to say (wayfinder #160)

**Question.** What does ASVS 5.0 require of a system representation before it can rule a
requirement applicable? This file records facts. It rules nothing. The taxonomy ruling is
[#162](https://github.com/mstarks01/work-agent/issues/162) and the package contract is
[#164](https://github.com/mstarks01/work-agent/issues/164).

**Method.** Two kinds of evidence, kept apart:

1. **The standard itself**, at tag `v5.0.0` of `github.com/OWASP/ASVS`. Every count below comes
   from `5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.flat.json`, the
   machine-readable form the project publishes. Every quotation comes from `5.0/en/`, chapters
   `0x03-What-is-the-ASVS.md`, `0x04-Assessment_and_Certification.md` and
   `0x05-For-Users-Of-4.0.md`.
2. **A hand classification** of all 70 Level 1 requirements, recorded beside this file as
   `asvs-l1-subjects.csv`. Each row names what a verifier must find before a pass or fail
   decision, when the requirement applies, and whether today's `SystemModel` carries the fact
   the applicability test reads. The tallies below recompute from that file. The judgement in
   it is mine; the requirement text it classifies is the standard's.

The repo side of every comparison is `src/stride_service/system_model.py` at `7c77fe6`.

---

## TOP-LINE VERDICT

**Six findings.**

1. **ASVS 5.0 asks a system representation for almost nothing, because it is a standard about
   an application's behaviour rather than about a system's shape.** Of the 70 L1 requirements,
   **2 address a data flow** and **6 address an addressable component**. **33 address one
   security control's configuration parameter** — a password minimum length, a TLS version, a
   cipher mode. That class is 47% of L1 and it is the whole question #162 must answer.

2. **The standard removed its architecture chapter on purpose.** ASVS 4.x carried a V1
   Architecture chapter. Version 5.0 deleted it, and says its first section "contained
   requirements that were out of scope". No chapter replaced it. A framework package for ASVS
   inherits a standard that decided architecture requirements were not verifiable.

3. **An applicability test reads technology presence or feature presence, and never an element
   type or a relation.** I derived 16 distinct predicates across the 70 L1 requirements. All 16
   ask whether the application *has* a thing — cookies, a database, OAuth, a file upload, a
   session. **None reads a boundary crossing, an element type, a zone, or a count.** 22 of the
   70 apply always.

4. **ASVS publishes no applicability metadata in any format.** A requirement carries exactly
   four fields: chapter, section, identifier, description, plus its level. Applicability is
   prose in a chapter introduction. Whatever selects requirements for a system is this repo's
   to author and to own.

5. **The level is an input to a job, never a fact this service can derive.** ASVS 5.0 sets
   levels by "priority-based evaluation" — risk reduction against implementation effort — and
   tells the organization to pick one. No property of a system representation selects L1, L2 or
   L3. #161 made `frameworks` a required list on the submission; ASVS needs a level beside the
   name, and #164 owns that.

6. **This service can rule an ASVS requirement applicable or unknown. It cannot rule one
   passed.** ASVS verification needs "access to documentation, source code, configuration, and
   the people involved in the development process". A job here carries prose about a system.
   The three-state shape this repo already has — a value, the `unknown` sentinel, and a
   needs-info **Verdict** — is the right shape for an ASVS finding. A pass claim is not
   available from the input.

---

## 1. The shape of the standard

| Fact | Value |
|---|---|
| Requirements | 345 |
| Chapters | 17 (`V1`–`V17`) |
| Sections | 80 |
| L1 requirements | 70 (20%) |
| L2 requirements | 183 (53%) |
| L3 requirements | 92 (27%) |
| Identifier format | `<chapter>.<section>.<requirement>`, e.g. `1.2.5` |
| Version-safe reference | `v<version>-<chapter>.<section>.<requirement>`, e.g. `v5.0.0-1.2.5` |

A requirement belongs to exactly one level. The levels are cumulative in use: an application at
L2 satisfies the L1 and the L2 sets together.

Per chapter:

| Chapter | Name | Total | L1 | L2 | L3 |
|---|---|---:|---:|---:|---:|
| V1 | Encoding and Sanitization | 30 | 8 | 19 | 3 |
| V2 | Validation and Business Logic | 13 | 4 | 7 | 2 |
| V3 | Web Frontend Security | 31 | 8 | 11 | 12 |
| V4 | API and Web Service | 16 | 2 | 8 | 6 |
| V5 | File Handling | 13 | 4 | 5 | 4 |
| V6 | Authentication | 47 | 13 | 22 | 12 |
| V7 | Session Management | 19 | 6 | 12 | 1 |
| V8 | Authorization | 13 | 4 | 3 | 6 |
| V9 | Self-contained Tokens | 7 | 4 | 3 | 0 |
| V10 | OAuth and OIDC | 36 | 5 | 24 | 7 |
| V11 | Cryptography | 24 | 3 | 11 | 10 |
| V12 | Secure Communication | 12 | 3 | 6 | 3 |
| V13 | Configuration | 21 | 1 | 12 | 8 |
| V14 | Data Protection | 13 | 2 | 7 | 4 |
| V15 | Secure Coding and Architecture | 21 | 3 | 10 | 8 |
| V16 | Security Logging and Error Handling | 17 | 0 | 16 | 1 |
| V17 | WebRTC | 12 | 0 | 7 | 5 |

Two chapters hold no L1 requirement at all: **V16 Security Logging and Error Handling** and
**V17 WebRTC**. A proof that runs L1 only touches 15 of the 17 chapters.

### What a requirement is

The standard states four scope tests, one per word of its name. Three of them bound what a
framework package can do here:

- **Security** — "The absence of a requirement must result in a less secure application".
- **Verification** — "The requirement must be verifiable, and the verification must result in a
  'fail' or 'pass' decision."
- **Requirement** — "The ASVS only contains requirements (must) and does not contain
  recommendations (should) as the main condition."

The standard also draws its own boundary around the application: "if an external process
interacts with the application or its data, it is considered out of scope for ASVS."

### The architecture chapter is gone

> The former V1 Architecture chapter has been removed. Its initial section contained
> requirements that were out of scope, while subsequent sections have been redistributed to
> relevant chapters, with requirements deduplicated and clarified as necessary.
>
> — `0x05-For-Users-Of-4.0.md`

This is the single most load-bearing fact in this file. The chapter that a DFD-shaped
representation would serve best is the chapter ASVS deleted. What survives asks about output
encoding, password rules, token validation and header values.

---

## 2. What each L1 requirement addresses

I read all 70 L1 requirements and recorded the concrete thing a verifier must find first.
Full table in `asvs-l1-subjects.csv`. The tally:

| Subject class | Count | What a verifier must find |
|---|---:|---|
| `control-config` | 33 | one security control's configuration parameter |
| `code-practice` | 14 | a coding behaviour across the application, with no single subject |
| `component` | 6 | one addressable part (an authorization server, a public folder, a dependency) |
| `identity` | 5 | an account, a consumer, or its permissions |
| `document` | 4 | an artifact outside the running system |
| `trust-layer` | 3 | which side of a trust boundary enforces a control |
| `data-class` | 3 | a class of data and where it travels |
| `flow` | 2 | one connection between two parts |

Read the top two rows together. **47 of 70 L1 requirements — 67% — address either one control's
configuration or a coding practice.** Both are properties of code, not positions in a graph.

### The control class is about parameters, not addresses

The 33 `control-config` requirements name about ten control families: a password policy, a
session token, a self-contained token, an OAuth authorization server, browser response headers,
a cookie, a cipher, a hash function, TLS, and a file upload limit. Each requirement reads a
**different property** of its control:

- `V6.2.1` reads a password's **minimum length** ("at least 8 characters").
- `V6.2.4` reads whether a **breached-password check** runs, against "the top 3000 passwords".
- `V7.2.3` reads a reference token's **entropy** ("at least 128 bits").
- `V9.1.2` reads a token's **algorithm allowlist**, and whether it excludes `None`.
- `V10.4.3` reads an authorization code's **lifetime** ("up to 10 minutes for L1 and L2").
- `V11.3.2` reads a cipher's **name and mode** ("AES with GCM").
- `V12.1.1` reads a TLS **version** ("TLS 1.2 and TLS 1.3").

An ID for a control answers none of these. A control node that carries only an ID and a name
tells an ASVS rule exactly what a string attribute tells it today. **The cost driver is the
control's typed properties, not the control's address.** #162 owns that distinction.

### Today's model carries no L1 applicability fact outright

The last column of the CSV compares each requirement's applicability test against the
`SystemModel` at `7c77fe6`. The result:

| Model carries the fact | Count |
|---|---:|
| yes | **0** |
| partial | 22 |
| no | 48 |

`partial` means a field exists that would hold the signal, but the field is free text, so the
read is a string match rather than a lookup. The five control attributes are
`DataFlow.authentication`, `DataFlow.encryption_in_transit`, `DataStore.encryption_at_rest`,
`DataStore.data_classification` and `Process.exposure`. Each is a `str` capped at 200
characters and carries the `unknown` sentinel as its leading token. `Process.technology` and
`DataStore.technology` are free text too, and `DataFlow.protocol` as well.

No row reads `yes`, because no attribute in the model is a closed enum that an ASVS predicate
can test.

---

## 3. What an applicability test reads

### The standard's own rule is chapter-wide

> The aim of the chapter and section division is to simplify choosing or filtering out chapters
> and sections based on the what is relevant for the application. For example, for a
> machine-to-machine API, the requirements in chapter V3 related to web frontends will not be
> relevant. If there is no use of OAuth or WebRTC, then those chapters can be ignored as well.
>
> — `0x03-What-is-the-ASVS.md`

That is the whole of the standard's applicability guidance. It operates on a **chapter**, it
tests **presence of a technology**, and it is prose.

Nine chapters need a presence test. Eight apply to any web application:

| Applicability | Chapters | Requirements | L1 |
|---|---|---:|---:|
| Always | V1, V2, V4, V8, V12, V13, V15, V16 | 143 | 25 |
| Presence test | V3, V5, V6, V7, V9, V10, V11, V14, V17 | 202 | 45 |

**64% of the standard sits behind a presence test.** The test asks about a browser frontend, a
file upload, authentication, sessions, self-contained tokens, OAuth, cryptography, sensitive
data and WebRTC.

### The 16 predicates, per requirement

Per requirement the tests are finer than the chapters, and they split three ways:

| Kind | Count | Predicates |
|---|---:|---|
| `always` | 22 | — |
| `tech:*` | 21 | browser frontend, cookies, CORS, database, OAuth, self-contained tokens, WebSocket, XML parser |
| `feature:*` | 27 | authentication, client-side code, encryption, file upload, multi-step flow, password auth, rich text input, sessions |

**Every predicate is a presence test.** Not one of them reads an element type, a trust zone, a
boundary crossing, a fan-in count, or any relation between two elements. This is the sharpest
contrast with the 12 STRIDE candidate rules, which read exactly those things.

Two predicates deserve a note:

- **`feature:client-side-code`** and the three `trust-layer` requirements (`V2.2.2`, `V7.2.1`,
  `V8.3.1`) are the only L1 requirements that read anything a trust boundary would answer. Each
  asks the same question: does an untrusted side enforce this control? `trust_zone` plus
  `ExternalEntity.kind` approximates it.
- **`tech:database`** is the presence test for `V1.2.4`, parameterized queries. A `DataStore`
  exists in the model, so the *element* is there; its `technology` field is free text, so the
  test is a string match.

### ASVS ships no machine-readable applicability field

Every published format — JSON, flat JSON, CSV, XML — carries the same five columns:

```
chapter_id, chapter_name, section_id, section_name, req_id, req_description, L
```

There is no "applies when" field, no tag, no component reference and no technology list. The
CWE and NIST mappings that 4.x carried were **removed** in 5.0. So a framework package for ASVS
must author its applicability rules itself, and this repo owns every one of them.

---

## 4. Documentation requirements: a class no system model can rule

31 of the 345 requirements — 4 at L1 — verify that a **document** exists. `V2.1.1` needs
documented input validation rules. `V6.1.1` needs documented anti-automation controls. `V8.1.1`
needs documented authorization rules. `V15.1.1` needs documented remediation time frames.

Three facts about the class, all verified against the data:

1. **Every one sits in section `.1` of its chapter.** All 31 identifiers match `V<n>.1.<m>`.
   The standard states the rule — "Documentation requirements are always in the first section
   of a chapter" — and the data agrees.
2. **Each pairs with an implementation requirement.** `V6.1.1` documents the anti-automation
   controls and `V6.3.1` verifies that they run "according to the application's security
   documentation". The standard is explicit that these are two separate activities.
3. **The subject is outside the system.** No representation of a system under review can hold
   the answer, because the answer is an artifact of the organization that builds it.

This class is why finding 6 above matters. A pair like `V6.1.1`/`V6.3.1` is a needs-info
question by construction, and needs-info is a **Verdict** this repo already has.

---

## 5. The level is chosen, not derived

ASVS 4.x tied levels to application risk. ASVS 5.0 broke that tie:

> The use of prescriptive, risk-based levels that mandate a specific level for certain
> applications has proven to be overly rigid.
>
> — `0x05-For-Users-Of-4.0.md`

> Rather than the ASVS prescriptively stating what level an application should be at, an
> organization should analyze its risks and decide what level it believes it should be at.
>
> — `0x03-What-is-the-ASVS.md`

Levels now rank requirements by "risk reduction with the effort to implement the requirement".
L1 shrank from about 128 requirements in 4.0.3 to 70 here, and its stated goal is "as few
requirements as possible, to decrease the barrier to entry".

The consequence for this repo is one line: **a job that names ASVS must also carry a level.**
Nothing in a **Valid System Model** picks one, and a default would repeat the mistake #161
already ruled out — one submission meaning different things on two installs.

---

## 6. What verification needs, and what a job here has

> Aside from penetration testing (using valid credentials to get full application coverage),
> verifying ASVS requirements may require access to documentation, source code, configuration,
> and the people involved in the development process.
>
> — `0x04-Assessment_and_Certification.md`

The standard also rules on how a report treats a requirement that does not apply:

> Some requirements may be non-applicable (e.g., session management in stateless APIs), and
> this must be noted in the report.

And on scope:

> The verifier should make the scope of the verification clear including which Level the
> organization is attempting to achieve and which requirements were included. This should be
> from the perspective of what was included rather than what was not included.

Three facts follow for a framework package here, and each is evidence rather than a ruling:

- A job carries **Sources** — prose, and often an interview transcript. It carries no source
  code, no configuration and no live application. So the pass half of the pass/fail decision is
  not reachable from the input.
- **A non-applicable requirement must still appear in the output.** ASVS demands it. That is a
  report-shape fact for #168, and it is not how a **Threat** behaves.
- **Scope reports positively.** The report says what was included. This repo's **Coverage**
  already computes denominators and calls them out for exactly this reason.

---

## What this file does not answer

- **Whether a control becomes a node.** The evidence says the expensive part is a control's
  typed properties, not its address. The ruling is #162's.
- **Whether one extraction serves both frameworks.** Nine of the 16 predicates ask about a
  feature — a file upload, a multi-step flow, rich text input — that extraction does not look
  for today. Whether extraction should look, or whether an ASVS package should ask, is #162's
  and #165's.
- **The content of an ASVS ruleset.** Out of scope on the map. This file classifies 70
  requirements to measure a representation, and it writes none of them as a rule.
- **L2 and L3.** I classified L1 only, per the ticket. The chapter and level counts above cover
  all 345, and the `control-config` share is unlikely to fall at higher levels, but I did not
  measure that and it is not a finding.

## Sources

All at tag `v5.0.0` of `https://github.com/OWASP/ASVS`:

- `5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.flat.json` — every count.
- `5.0/en/0x03-What-is-the-ASVS.md` — scope, levels, identifiers, the chapter filter rule.
- `5.0/en/0x04-Assessment_and_Certification.md` — verification mechanisms, scope reports, non-applicable requirements.
- `5.0/en/0x05-For-Users-Of-4.0.md` — the removed architecture chapter, the level rethink, the removed CWE and NIST mappings.
