# Bootstrap → blessed corrections: 04-ml-inference-service

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `store:feature-store.encryption_at_rest` | `none` | `unknown` | The source says Redis has no *password*. The bootstrap generalized one stated absence into a second, different absence it was never told about. An invented absence is as wrong as an invented control, and it is more dangerous: analysts file confident findings on it. |
| 2 | `store:model-registry` | typed as `Process` | `DataStore` | "Registry" read as a service. It is described only as a bucket that artifacts are read from and written to. |
| 3 | `flow:model-server-to-model-registry:load-artifact` | (absent) | present | The bootstrap folded artifact loading into the model server's `description` instead of modelling it as a flow, which would have left the case's headline threat with no flow to attach to. |
| 4 | `store:inference-log.assets` | `[]` | `["pii"]` | Named as a debugging log, so the bootstrap treated it as operational data despite the text stating it holds whatever end users typed. |
| 5 | `entity:ml-engineer.trust_zone` | `boundary:public-internet` | `boundary:model-network` + assumption | Neither zone is stated. The bootstrap picked one silently; blessed picks the one the text better supports and records the inference. |
| 6 | `flow:calling-service-to-inference-gateway:submit-inference-request.authentication` | `API key` | `per-team API key in a header, never expired or rotated` | "We have never expired one" is the fact that raises this from routine to must-find. |

## Signal

Correction 1 is the first **invented absence** in the corpus and the most
worrying single data point: every other case's failures over-report controls,
this one under-reports them, and both directions produce confident analyst
findings on facts the user never gave. Correction 2 shows type assignment
following the *name* rather than the described behaviour, and correction 3 shows
an interaction being demoted into prose — the extraction failure that silently
deletes a threat surface, since analysts can only file against elements that
exist.

## Rulings of 2026-09-16 (#961 step 3)

The step 3 review on #961 ruled on the drafted `facts.json` and its disputed
values. The review is assistant-authored and the maintainer posted it; it is
not a human signature, and no row in `facts.json` is signed. Each edit below
follows one ruling, and `facts.json` changed with it.

| # | Path | Before | After | Ruling |
|---|---|---|---|---|
| 7 | `flow:calling-service-to-inference-gateway:submit-inference-request.authentication` | `... never expired or rotated` | `...; operator reports never having expired a key` | "We have never expired one" reports the operator's past actions. Neither rotation nor automatic expiry follows from it. The V13.2.1 reference claim says the same. |
| 8 | `flow:calling-service-to-inference-gateway:submit-inference-request.protocol` | `HTTPS` | `unknown` | An internet-facing FastAPI service and an API-key header establish neither HTTPS nor transport encryption. The V4.1.1 note says the same. |
| 9 | `process:model-server.exposure` | `internal` | `unknown` + note | "Meant to be reachable only from the gateway" is an intended restriction. Membership in the model network is stated; enforcement is not. |
| 10 | `entity:ml-engineer` assumption | basis said the registry "is stated to sit in the model network" | a placeholder the schema requires | Publishing to a bucket does not locate the publisher, and the source never locates the bucket. The zone stays because the schema needs one; `facts.json` reads it unknown. |
| 11 | `store:model-registry-bucket.data_classification` | `internal` | `unknown` | "Model artifacts" settles no sensitivity, and the scheme's `internal` needs a finding that disclosure harms no outside party. |
| 12 | `store:redis-feature-store.data_classification` | `confidential`, no assumption | `confidential` + assumption | Customer-specific account age and spend bands support the inference; the classification is inferred under the scheme, not quoted. |
| 13 | `store:inference-log.data_classification`, and its `assets` assumption | `confidential`; "contains personal data" | `unknown`; "may contain personal data" | Raw prompts may carry sensitive text, but their actual sensitivity is unspecified. |

Two aliases were ruled for `case.json`: "Other teams' backends" for
`entity:calling-service` and "ML engineers" for `entity:ml-engineer`. Both are
the source's own words for the aggregate actor.

## Signing of 2026-09-16 (#961 step 3)

The maintainer signed every row of `facts.json` in a session, ruling on each
against `source.md`. Four values changed in `model.json` with the rulings:

| # | Path | Before | After | Ruling |
|---|---|---|---|---|
| 14 | `flow:model-server-to-redis-feature-store:read-features.authentication` | `none` | `unknown` + note | "Redis has no password on it" establishes the absence of password authentication, not the absence of every mechanism. |
| 15 | `store:model-registry-bucket.trust_zone` | no assumption | placeholder assumption | Loading artifacts from a bucket does not establish co-location. |
| 16 | `process:inference-gateway.trust_zone` | no assumption | placeholder assumption | Internet exposure does not establish a separate serving-edge network. |
| 17 | `store:inference-log.trust_zone` | no assumption | placeholder assumption | Writing to BigQuery does not place it in the gateway's network. |

The calling service keeps an inferred public-internet placement: it records
participation from the internet, not where every calling service is hosted.

Two STRIDE reference claims, records 4 and 13, still call the Redis store
"unauthenticated". The ruling above says the source states less than that.
The wording stays because the calibration review of 2026-09-02 keys its
sample by the claim text, and a rewording would move two fixtures out from
under a signed record; the case sitting for this case reads those claims.

## Rulings of 2026-09-16 (#961 step 6)

`internet` is an alias for `boundary:public-internet`, the inferred internet
interaction zone; `external` is accepted only where its membership establishes
the same meaning. `other team's backend` is an alias for `entity:calling-service`;
`calling team's user` is not, because the backend makes the call.
