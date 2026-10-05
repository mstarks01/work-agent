# 63. A conjunction names one party through its parent

- **Status**: accepted
- **Date**: 2026-10-05
- **Effort**: [#1468](https://github.com/mstarks01/work-agent/issues/1468)
- **Relates to**: [ADR 0051](0051-a-requirement-applies-by-a-rule-over-capabilities.md),
  whose rule about conjunctions this replaces

## Context

A capability is a fact about the whole application. So a conjunction such as
`all(oauth-client, authorization-code-flow)` held when one component was an
OAuth client and a different component used the code flow. The #1291 review
asked that each term of a conjunction hold on the same party: one relying
party (V10.5.5), one authorization server (V10.4) and one token system (V9.2).

The error went one way. A requirement stayed `applicable` when it applied to no
component. A wrong pairing never ruled a requirement out.

Most of the 47 conjunctions were already safe. They pair a child with its own
parent, such as `multiple-authorization-servers` under `oauth-client`, and the
child's question asks about that party. Six capabilities were not safe:

- `authorization-code-flow` and `oidc` had no role parent, so one answer
  covered both the clients and the servers.
- `token-validity-period` and `shared-signing-key-audiences` were siblings of
  the token issuer and the token consumer, so they could describe the other
  token system.
- `multiple-identity-providers` and `multiple-authorization-servers` asked a
  question that added up the providers or servers of different parties.

## Decision

**A fact that a conjunction pairs with a role sits under that role, and its
question asks about one party of that role.** A "yes" then states that one
party has both properties, which is what the conjunction needs. The System
Model, the question page and the evaluator do not change.

- `authorization-code-flow` becomes `client-code-flow` under `oauth-client` and
  `server-code-flow` under `oauth-authorization-server`.
- `oidc` becomes `oidc-relying-party` under `oauth-client` and `oidc-provider`
  under `oauth-authorization-server`. `oidc-backchannel-logout` sits under the
  relying party, and `rp-initiated-logout` under the provider.
- `token-validity-period` sits under `self-contained-token-consumer`, and
  `shared-signing-key-audiences` under `self-contained-token-issuer`.
- The questions for `multiple-identity-providers` and
  `multiple-authorization-servers` ask about one party, so the person does not
  add up the providers or servers of different parties.

**We rejected a component on each fact.** That design puts a component on each
`CapabilityFact`, asks a role question for each component, and evaluates each
conjunction for each component. It changes the System Model and the page, and
it asks more questions. Applicability is the only reader of a capability, and
it needs only to know that one party has both properties. A role-scoped
question gives that with one answer.

## Consequences

The person who answers must read "one client" or "one part" correctly. A
report names no component for a capability.

A part is hidden until its parent is "yes", so the code-flow question is no
longer asked beside two "no" answers on the same page. The benchmark's level 3
simulation with both OAuth roles absent asked `authorization-code-flow` and
settled nothing with it. It now asks no code-flow question.

A conjunction of two facts with no parent link still reads the whole
application, such as `all(browser-frontend, cookies)`. A new conjunction whose
terms describe one party must use a child under that party, not a sibling or a
fact with no parent.
