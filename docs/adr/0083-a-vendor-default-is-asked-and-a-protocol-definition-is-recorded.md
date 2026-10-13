# 83. A vendor's default is asked, and a protocol's definition is recorded

- **Status**: accepted
- **Date**: 2026-10-13
- **Amends**: [ADR 0082](0082-a-platform-default-that-cannot-be-disabled-is-recorded.md),
  whose vendor rows this removes

## Context

ADR 0082 recorded two kinds of default where a description is silent: a
vendor's (DynamoDB encrypts every table at rest, Stripe requires HTTPS and an
API key) and a protocol's (SFTP is encrypted, WireGuard authenticates its
peers). A vendor's default is policy. Vendors change it — S3 made encryption
mandatory in 2023 and changed its SSE-C default in 2026 — and every new service
needs a row, so the table would need upkeep for as long as it exists. A
protocol's property is its design, written in a standard that changes seldom.
The maintainer chose on 2026-10-13 to keep only what is intrinsic by design.

## Decision

**The vendor rows are removed.** A description that names DynamoDB, a Google
Cloud store, Azure Storage, Snowflake, Stripe or a hosted identity provider is
asked about encryption and authentication as any other is, unless it states
them.

**The protocol rows stand** (`protocol_defaults.PROTOCOL_DEFAULTS`): a flow
whose stated protocol is encrypted or authenticating by its own standard takes
that property, after the validity gate, with an Assumption that cites the
standard. Each row needs no upkeep while its standard holds.

Whether a model can tell a vendor default that cannot be disabled from one
that can, well enough to decide or to suggest, is measured separately
(`QA-2026-10-13-06`).

## Consequences

The four blessed reference stores that disagreed with the vendor rows (case 02's
device registry and telemetry lake, case 03's claims warehouse, case 04's
inference log) agree again. Case 02's `poll firmware` flow still states `HTTPS`
with no assumption and keeps its encryption `unknown`, so it still disagrees with
the protocol rows and awaits the maintainer's ruling.
