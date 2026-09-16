# Bootstrap → blessed corrections: 01-payments-checkout

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`,
"Bootstrapping without credentials"). Applying these corrections in reverse to
`model.json` reconstructs the bootstrap artifact exactly.

Each entry was found by working the checklist against `source.md`, not by
reading the candidate model for plausibility.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:shopper-to-storefront-api:place-order.encryption_in_transit` | `TLS` | `unknown` | The text never says the shopper connection is encrypted. `HTTPS` was inferred from the protocol field and written as fact with no assumption recorded — exactly the silent guess the extract prompt forbids. |
| 2 | `flow:card-processor-to-storefront-api:settlement-webhook.authentication` | `webhook signature` | `unknown` | Source says "I would have to check how that callback is authenticated" — an explicit non-statement. The bootstrap supplied the control a reader would expect a payment processor to have. |
| 3 | `store:orders-db.encryption_at_rest` | `Cloud SQL default encryption` | `unknown` | The text is silent on orders-db at rest; the bootstrap carried the receipt archive's CMEK statement across to the database. |
| 4 | `process:order-service.exposure` | `unknown` | `internal` + assumption | Source states "it is not exposed outside". The value is readable from the text, so `unknown` under-reports; recorded as an assumption because "not exposed outside" is a claim about intent, not a verified control. |
| 5 | `store:receipt-archive` | (absent `assets`) | `["business-critical-data"]` | Receipts are the only record that an order was captured; the bootstrap tagged only the database. |
| 6 | `flow:order-service-to-orders-db:read-write-orders.authentication` | `application account` | `single shared application account with full read/write; password from an environment variable` | The bootstrap dropped the two facts that carry the threat: shared, and full read/write. Attribute text is what analysts read, so detail lost here is invisible downstream. |

## Signal

Four of six corrections are the same failure: **a plausible security control
written where the text said nothing** (1, 2, 3) or **detail flattened out of an
attribute** (6). Under-reporting (4, 5) is the rarer direction. If this pattern
holds across cases, extraction evals should weight "invented control" errors
above "missing element" errors.

## Rulings of 2026-09-16 (#961 step 3)

The step 3 review on #961 ruled on the drafted `facts.json` and its disputed
values. The review is assistant-authored and the maintainer posted it; it is
not a human signature, and no row in `facts.json` is signed. Each edit below
follows one ruling, and `facts.json` changed with it.

| # | Path | Before | After | Ruling |
|---|---|---|---|---|
| 7 | `flow:order-service-to-receipt-archive:append-receipt.protocol` | `HTTPS` | `unknown` | "That is on TLS" establishes encryption, not the application protocol. TLS stays in `encryption_in_transit`. |
| 8 | `flow:order-service-to-orders-db:read-write-orders.protocol` | `PostgreSQL wire protocol` | `unknown` | PostgreSQL names the database technology; the connection implementation is not stated. |
| 9 | `flow:order-service-to-orders-db:read-write-orders.authentication` | `single shared application account ...` | `single application account ...` | One account used by one service does not establish that several principals hold it. |

## Signing of 2026-09-16 (#961 step 3)

The maintainer signed every row of `facts.json` in a session, ruling on each
against `source.md`. Three zones were ruled placements the schema requires
rather than facts the source states, and `model.json` records each as an
assumption:

| # | Path | Ruling |
|---|---|---|
| 10 | `process:storefront-api.trust_zone` | Internet exposure does not establish a DMZ or a separate network zone. |
| 11 | `store:orders-db.trust_zone` | The service accessing the database does not establish that they share a network. |
| 12 | `store:receipt-archive.trust_zone` | Writing to the bucket does not place it in the service's network. |

The shopper and the card processor keep an inferred public-internet
placement: it records participation from outside through the one
internet-exposed endpoint, not a hosting location.

## Alias rulings of 2026-09-16 (#961 step 6)

Seven alias rulings were drafted on `facts.json` for the subjects and scope
values the model spells otherwise. The maintainer had an assistant review
them against `source.md` and posted the decisions on #961; the review says
it is not a human signature, and `reviewed_by` on each accepted alias names
that review. A signed alias is active in the replay.

| # | Target | Decision |
|---|---|---|
| 13 | `principal:application-account` ← "single application account" | Accepted. |
| 14 | `principal:anything-that-can-reach-the-order-service` ← "anything that can reach the service", "anything that can reach it" | Accepted within this case's order-service context. |
| 15 | `principal:order-service` ← "order service's own service account" | Rejected and removed. A workload and the account it uses are not interchangeable identities. The review prefers a reference row on `principal:order-service-service-account` ("order service's own service account"), `write receipts`, scope receipt-archive/write, basis inferred, in place of the workload-level grant. That is a change to a signed row and waits for the maintainer's signature; the row stands as signed until then. |
| 16 | `credential:application-account-password` ← "password" | Accepted within the application account's rows and the orders-db flow (`within`). A bare "password" on a shopper is not rewritten. |
| 17 | resource `orders-db` ← "orders db" | Accepted. |
| 18 | operation `read-write` ← "read/write", "read and write" | Accepted. |
| 19 | operation `submit order` ← "submit orders" | Accepted for the plural only; "call" and the gRPC spellings were rejected as naming the transport, not the operation. |

## Signed row change of 2026-09-16 (#961 step 6)

The maintainer accepted the row change alias ruling 15 proposed and signed it
in a session.

| # | Path | Before | After | Ruling |
|---|---|---|---|---|
| 20 | `facts.json` row: `authorization-grant`, `write receipts`, receipt-archive/write, inferred | subject `order service` | subject `order service's own service account` | A workload and the account it uses are not interchangeable identities; the source names the service account as what performs the write. |

## Ruling of 2026-09-16 (#961 step 6): the storefront DMZ is a placeholder

The maintainer ruled that the source establishes internet exposure and not a
DMZ, and removed the zone expectation. `boundary:storefront-dmz` stays in
`model.json` because the schema requires a zone, and every member's placement
is an assumption (change 10), so the replay reads the zone as a placeholder
and not as a required element. Neither `card-processor` nor `cloud-storage`
is its alias.
