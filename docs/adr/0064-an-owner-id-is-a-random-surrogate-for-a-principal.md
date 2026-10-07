# 64. An owner ID is a random surrogate for a principal

- **Status**: accepted
- **Date**: 2026-10-07
- **Effort**: [#1527](https://github.com/mstarks01/work-agent/issues/1527), on the
  persistent storage map [#1522](https://github.com/mstarks01/work-agent/issues/1522)

## Context

A durable store keys each stored object under `<owner-id>/<job-id>/`, so the
owner ID becomes part of every key. Today each ownership check compares the raw
OIDC `sub` claim. A `sub` is unique only inside its issuer, and some identity
providers put an email address in it.

## Decision

**A principals table maps each pair of issuer and `sub` to one random UUIDv4
that the service generates. That UUID is the owner ID.** The pair has a unique
constraint. The service creates the row on the first job submission only, so a
token that only reads adds no row. The web app and the in-process engine use a
principal under a reserved issuer value, such as `local:`. An OIDC issuer is an
`https` URL, so no token can map to it. A principal holds the owner ID, the
issuer, the `sub` and a creation time, and nothing more. Log lines carry the
owner ID, and the `sub` appears only in the line that records a new principal.

## Considered options

- **The raw `sub` in the key.** Rejected: it can carry personal data into
  object keys and logs, and two issuers can give the same `sub` to two people.
- **A hash of the issuer and the `sub`.** Rejected: anyone who knows a `sub`
  can compute the hash and find that person's objects.
- **A UUIDv7.** Rejected: it shows in each key when the principal was created.

## Consequences

A key never changes when the identity provider changes the format of `sub`.
A future tenant model adds a lookup in the principals table, not a migration
of each key. A change of the issuer URL makes every existing principal
unreachable until an operator maps the old issuer to the new one.
