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
