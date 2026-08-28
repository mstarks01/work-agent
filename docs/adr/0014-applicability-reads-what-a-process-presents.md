# 14. A framework's applicability reads what a process presents, never what a flow carries

- **Status**: accepted
- **Date**: 2026-08-19
- **Effort**: [#219 — Should a source that says 'web app' reach the ASVS
  precondition?](https://github.com/mstarks01/work-agent/issues/219)
- **Relates to**: [ADR 0013](0013-asvs-rules-applicability-and-never-a-pass.md),
  which settled what an ASVS claim may assert. This one settles what decides
  whether the package runs at all, and amends neither of 0013's rulings.

## Context

ASVS defines security requirements for "web applications and services" in its
first sentence, and no requirement asks whether the target is one. That tier-0
test is the **Precondition**, and ASVS is the first **Framework Package** here
whose precondition can answer no.

It answered that question by reading every **Data Flow**'s `protocol` and asking
whether any of them named a web protocol. Six of the 13 corpus cases came back
`undecidable`. Among them:

| case | a process it carries | every flow's `protocol` |
|---|---|---|
| `11-sparse-shift-scheduling` | `scheduling web app` | `unknown` |
| `12-overclaiming-supplier-portal` | `supplier portal` | `unknown` |
| `06-cookbook-online-game` | `moderation website` | `unknown` |
| `08-sso-identity-broker` | `identity broker`, `store admin console` | `unknown` |

Case 11's own source reads *"Store managers use a scheduling web app to build the
weekly rota"* and then, four paragraphs later, *"Nobody has documented … whether
any of it is encrypted."* Both facts are recorded correctly. The model says what
the system is and says that nobody stated its transport.

**The extraction was right and the precondition was wrong.**
`prompts/extract.md` states the rule the transcriber followed: *"`unknown` is the
default, not the fallback."* A source that never mentions TLS produces
`protocol: unknown`, and any other value would be a guess recorded as a fact —
the failure that prompt exists to prevent. The defect was reading that field to
answer a question it was never about.

This matters beyond ASVS. It is the shape
`docs/agents/framework-parity.md` describes: a rule that was plausible when one
framework existed, silently wrong afterwards, and reporting a smaller number
rather than raising. Any framework that scopes itself inherits the same exposure
the moment it picks an attribute to scope on.

## Decision

**A framework's applicability reads what a process presents. It never reads what
a connection carries.**

`Process.interface_kind` records the first fact: `web` for the HTTP family as an
application presents it — a browser UI, a REST, GraphQL or SOAP API, a websocket
— `non-web` for anything else, `unknown` when the input does not say. It sits
beside `exposure` and obeys the same rule every other attribute does: `unknown`
is the default, and an inference goes in `assumptions` with its basis.

`DataFlow.protocol` keeps recording the second fact, unchanged.

**Neither field is ever read off the other**, and `prompts/extract.md` states
that in both directions, because a transcriber who derives one from the other
destroys the distinction in the model rather than in the reader:

- A process presenting a web interface does not make its flows HTTPS. The
  supplier portal is a web application whose transport nobody wrote down.
- A flow stating HTTPS does not make its endpoint a web application. A backup
  agent shipping files over HTTPS to object storage is not one.

A stated web protocol still **satisfies** the ASVS precondition, because a flow
that says HTTPS says the same thing by another route. It can no longer **refuse**
on its own, and it can no longer hold the answer open: a model whose every
process states `non-web` has answered, whatever its flows leave unsaid.

## Consequences

**The three states now separate for the right reason.** Under the corpus as it
stands: 11 cases `satisfied`, `03-batch-data-pipeline` `refuted` because both its
processes state Airflow and Spark, and `07-cicd-store-deploy` `undecidable`
because its source genuinely never says what the deploy controller presents.
That last one is worth keeping — #219's own body asserts the controller is
"polled over HTTPS by 1,200 store servers", and **the source does not say that**.
The precondition holding the answer open is the correct outcome, and the remedy
is to submit more about the system.

**Four cases now need a reference set.** `06`, `08`, `11` and `12` satisfy the
precondition and carry no ASVS records. They are named in
`PENDING_REFERENCE_SETS` in `evals/verify_corpus.py`, which fails a case that
newly satisfies and is not listed, and fails a listed case that has since
declared the framework. Writing those four sets is a reading session over each
`source.md` (`evals/BLESSING.md` step 3), not a mechanical edit, which is why the
mechanism ships with the gap named rather than with four agent-authored sets
nobody has read.

**Every ASVS number in the suite still measures 7 cases.** The reference sets are
what a sweep grades, so the numbers move when somebody writes the four sets
above, not when this lands.

**A model is not portable across this change.** `interface_kind` is required and
has no default, so every `model.json` and every constructed `Process` states it.
There is no shim: a model written before this is re-extracted or edited, which is
the same hard cutover every schema change here takes.

## Alternatives considered

**Backfill the protocols.** #219's first reading: if the source says HTTPS, the
model is wrong and should carry it. Rejected as the *general* answer, because it
is not true of these cases — the sources are silent about transport, and writing
a protocol in to make a different framework run is exactly the guess-as-fact
failure the extraction rule forbids. Where a source *does* state transport the
model should carry it, and that stays a corpus-review finding rather than a
precondition question.

**Read `Process.exposure`.** Satisfied when any process is internet-facing. No
schema change, and it would flip the same four cases. Rejected: an
internet-facing MQTT broker or SFTP server is internet-facing and is not a web
application, so this trades one wrong attribute for another that is wrong less
often.

**Match the prose already in the model.** Element names and descriptions carry
the words — `supplier portal`, `moderation website`. Rejected: it is keyword
matching dressed as a mechanical test, and it puts a judgement in code. The
judgement belongs to the extraction agent, which reads the prose; the code then
reads a field, which is the division this repo already applies everywhere else.
