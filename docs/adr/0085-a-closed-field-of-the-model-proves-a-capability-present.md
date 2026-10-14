# 85. A closed field of the model proves a capability present

- **Status**: accepted
- **Date**: 2026-10-13
- **Amends**: [ADR 0051](0051-a-requirement-applies-by-a-rule-over-capabilities.md),
  where only an answer states a capability
- **Relates to**: [ADR 0082](0082-a-platform-default-that-cannot-be-disabled-is-recorded.md),
  whose protocol table this reads, and
  [ADR 0084](0084-one-choice-answers-no-to-every-capability-question-left.md),
  after which a capability question costs a choice mostly where its answer
  is "yes"
- **Evidence**: `QA-2026-10-13-10-E1` in `evals/experiments/`, and
  `tests/test_field_capabilities.py`

## Context

A job that selects ASVS asks a capability question for each capability its
rule needs and no answer states. Since ADR 0084, one choice answers "no" to
the questions left, so the choices a page costs are mostly its "yes" answers.
Many of those the model already shows: a flow that states HTTPS between two
of the application's parts shows that it uses TLS, and an internet-facing
process with a web interface shows that it exposes an HTTP service.

ADR 0051 lets only an answer state a capability, because asking extraction
for a capability list cost element identity: aligned recall 3.5 sd lower. That
cost belongs to the model writing the list. Code that reads fields the model
already holds does not ask the model for anything.

## Decision

**Code states a capability present where a closed field of the model proves
it.** `analysis_service.field_capabilities.FIELD_CAPABILITIES` holds 17 rows.
Each row reads only fields with a closed set of values: an element's kind, a
process's exposure and interface, the asset tags, a stated protocol of a
family the protocol table names, and a control state. No row reads free text
such as a technology name. The writer runs after the gate, beside the
protocol table, and writes into the model the analysis reads, never into
what extraction emitted.

| Rows | What proves them |
| --- | --- |
| `tls-use`, `cryptographic-operations`, `cryptographic-keys` | a stated TLS-family protocol on a flow with an end in the application |
| `tls-client` | a process is the source of such a flow |
| `internal-tls-connections`, `internal-http-connections` | a stated TLS-family or HTTP-family protocol between two of the application's parts |
| `internal-services` | the same between a process and a data store |
| `sensitive-http-requests` | an HTTP-family flow of the application tagged `pii`, `financial`, `health`, `credentials` or `secrets` |
| `separate-service-connections` | a flow from a process to an external system |
| `websocket`, `xml-parsing`, `http2-messages`, `http2-request-reception` | a stated WebSocket, SOAP or XMPP, or HTTP/2 or gRPC protocol, at the right end |
| `sensitive-data`, `secret-assets` | a process or a data store with one of those tags |
| `external-facing-http-services` | an internet-facing process with a web interface |
| `encryption` | a data store whose encryption at rest is stated and not assumed |

The application's parts are the model's processes and data stores. A
protocol counts only where the flow states it, the model did not assume
it, and the field does not negate it ("HTTPS, not HTTP/2" states no HTTP/2).
The protocol table reads the same rule.

**A row only marks a capability present.** No field proves an absence, and
a wrong "present" makes a requirement apply that should not; it never rules
one out. **A statement always wins**: a capability that an answer or
another statement names is left as it is, and a later answer replaces the
statement code wrote.

**The statement says what proves it.** It quotes the first element that
proves it, so the gate checks the quote, and its `basis` names the element
and the rule. The report shows the basis beside the quote. The owner is not
asked: the maintainer chose this on 2026-10-13, because a wrong "present"
only adds checks and the owner can still amend the description.

Nine rows that held by definition were left out because no archived model
fires them: `webrtc`, `graphql`, `ldap`, `mail-sending`, `mutual-tls`,
`authentication`, the two HTTP/3 rows and `oauth`.

## Consequences

On the example at ASVS level 2, four capability questions the owner answers
"yes" leave the list (`tls-use`, `encryption`,
`external-facing-http-services` and `sensitive-http-requests`); the limit of
30 fills the list from below, and the questions off the list fall from 77
to 67. Twenty more units apply with no answer, 59 where 39 did. At level 1
the list falls from 10 to 8. Across the 58 distinct archived models, the
rows make 118 units applicable at level 1, 551 at level 2 and 927 at
level 3 (`QA-2026-10-13-10-E1`).

Three rows read the asset tags, which extraction chooses by judgement. On
the corpus it adds tags where the reference has none: 314 a sweep, 239 of
them `business-critical-data`, which no row reads (`QA-2026-10-12-01-E10`).
A wrong sensitive tag makes these rows state a capability the application
lacks, and the lanes then rule on requirements that do not apply. How often
that happens is unmeasured.
