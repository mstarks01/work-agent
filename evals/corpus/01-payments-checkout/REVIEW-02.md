# Review sitting 02 — is the control case's threat list right?

`evals/BLESSING.md` step 6, run for the first time, on
`evals/corpus/01-payments-checkout`.

**Why this case.** It is the control. Every far-domain recall figure in the suite
is a delta against it, so an error in its 21 reference claims moves every
comparison the corpus exists to make. It is also 242 words of source text, which
is the shortest path to a first sign-off.

**What you are checking.** Not whether two write-ups are the same threat — that
was sitting 01. This asks the question underneath: **do these reference sets
describe what could actually go wrong with this system?** If a set misses a whole
class of attack, the tool scores full marks for missing it too, and nothing in the
repo would ever say so.

**Two sets, one sitting.** This case declares both frameworks, so Part 2 is the
21 STRIDE threats and Part 3 is the 17 ASVS records. They share one **System
Model**, and the property being established — that a set is exhaustive against
that model — is not something you can check for one framework and infer for the
other. Reviewing them apart is what step 6 forbids.

**The ASVS half carries an extra weight.** Nothing anywhere scores those 17
records. Both blocks are now scored by a sweep:
`evals/harness/instruments.py` maps `asvs` to `applicability.score_case` in
`PACKAGE_SCORERS`, which closed
[#200](https://github.com/mstarks01/work-agent/issues/200), and the ASVS half
comes back as a confusion matrix over the catalog, matched by string. So both
sets get exercised eventually. What no sweep can do is ask whether a set is
*exhaustive* — a requirement nobody wrote down is one the matrix scores nothing
for missing. **That question is this sitting's alone.**

## The one rule

**Read Part 1 and write your own list before you open Part 2.** If you read the
recorded threats first you will find them reasonable, and the sitting measures
nothing. Your list does not have to be good or complete — it only has to be
yours, written first.

Roughly an hour.

---

## Part 1 — the system, and your own list

### The submitted text

Exactly what the service would receive.

> Checkout and order-capture path for our storefront.
> 
> Shoppers browse the storefront and place orders through the storefront API, a
> Node service running on Cloud Run. It is the only thing we expose to the
> internet. Shoppers sign in with email and password and get a session cookie;
> we have not rolled out MFA for shopper accounts yet.
> 
> When a shopper submits an order the storefront API calls the order service
> over gRPC. The order service is a Python worker in our core network; it is
> not exposed outside. There is no auth on that gRPC call — the order service
> accepts anything that can reach it, which is meant to be only the storefront
> API. Orders, shopper addresses and card-last-four live in a PostgreSQL
> database (orders-db) that the order service reads and writes with a single
> application account that has full read/write. The password comes from an
> environment variable on the worker.
> 
> Every completed order also gets a receipt written to a Cloud Storage bucket
> we call the receipt archive. That is on TLS with the order service's own
> service account, and the bucket is encrypted with a customer-managed key. The
> receipt records which order was written and that the order service wrote it.
> 
> Our card processor is a third party. After they settle a payment they POST a
> webhook back to the storefront API to tell us the order is paid. I would have
> to check how that callback is authenticated.

### What the model says is in it

Not part of the question, but the threats cite these names, so you need them.

**External entities**

| id | kind | zone | authentication |
|---|---|---|---|
| entity:shopper | human | boundary:public-internet | — |
| entity:card-processor | external-system | boundary:public-internet | — |

**Processes**

| id | exposure | zone | authentication |
|---|---|---|---|
| process:storefront-api | internet-facing | boundary:storefront-dmz | — |
| process:order-service | internal | boundary:core-services | — |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:orders-db | boundary:core-services | unknown | confidential |
| store:receipt-archive | boundary:core-services | customer-managed key (CMEK) | internal |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:shopper-to-storefront-api:place-order | entity:shopper | process:storefront-api | HTTPS | session cookie issued after email and password login; no MFA | unknown |
| flow:card-processor-to-storefront-api:settlement-webhook | entity:card-processor | process:storefront-api | HTTPS POST | unknown | unknown |
| flow:storefront-api-to-order-service:submit-order | process:storefront-api | process:order-service | gRPC | none; accepted by network position | unknown |
| flow:order-service-to-orders-db:read-write-orders | process:order-service | store:orders-db | PostgreSQL wire protocol | single shared application account with full read/write; password from an environment variable | unknown |
| flow:order-service-to-receipt-archive:append-receipt | process:order-service | store:receipt-archive | HTTPS | order service's own service account | TLS |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:storefront-dmz | network |
| boundary:core-services | network |

### Your list

Write what could go wrong. Anything: an attack, a missing control, a question the
text does not answer. Bullet points, in any order, no need to sort by category.

```
-
-
-
```

---

## Part 2 — the 21 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real threat against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a threat here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of Part 3, note anything on **your** list that is not on either
of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker who obtains a shopper's session cookie replays it against the storefront API and places orders as that shopper.

- cites: `flow:shopper-to-storefront-api:place-order`, `entity:shopper`
- tier: must-find · severity: high/high
- recorded note: Single-factor session with no MFA on an internet-facing endpoint; the canonical finding for this flow.

> mark:

**2.** An attacker POSTs a forged settlement webhook to the storefront API, impersonating the card processor to mark an unpaid order as paid.

- cites: `flow:card-processor-to-storefront-api:settlement-webhook`, `process:storefront-api`
- tier: must-find · severity: high/high
- recorded note: Callback authentication is unknown on an internet-facing endpoint; must be reported as unverified, not assumed present.

> mark:

**3.** Any workload that can reach the order service impersonates the storefront API on the unauthenticated gRPC channel and submits orders.

- cites: `flow:storefront-api-to-order-service:submit-order`, `process:order-service`
- tier: must-find · severity: medium/high
- recorded note: Authentication is stated as none, accepted by network position — a stated absence, not an unknown.

> mark:

**4.** An attacker holding the shared application password connects to orders-db as the order service.

- cites: `flow:order-service-to-orders-db:read-write-orders`, `store:orders-db`
- tier: expected · severity: medium/high
- recorded note: Static shared secret from an environment variable; identity is the password.

> mark:

### tampering

**5.** An attacker positioned in the storefront tier modifies order contents in flight on the gRPC channel, whose transport protection is unverified.

- cites: `flow:storefront-api-to-order-service:submit-order`
- tier: must-find · severity: medium/high
- recorded note: Crosses dmz to core with authentication none and encryption_in_transit unknown.

> mark:

**6.** An attacker with the shared full read/write database account alters order rows, changing prices or payment status directly.

- cites: `store:orders-db`, `flow:order-service-to-orders-db:read-write-orders`
- tier: must-find · severity: medium/high
- recorded note: No least privilege: the same credential that reads can rewrite every row.

> mark:

**7.** An attacker replays or edits a settlement callback to flip the recorded payment state of an order they do not own.

- cites: `flow:card-processor-to-storefront-api:settlement-webhook`, `store:orders-db`
- tier: expected · severity: medium/high
- recorded note: Distinct from the spoofing entry: the target is the persisted order state, not the sender identity.

> mark:

**8.** An attacker holding the order service's service account overwrites or deletes archived receipts to erase evidence of an order.

- cites: `store:receipt-archive`, `flow:order-service-to-receipt-archive:append-receipt`
- tier: expected · severity: low/medium
- recorded note: The model states CMEK encryption but says nothing about object immutability or retention locks.

> mark:

### repudiation

**9.** A shopper denies having placed an order, and the receipt archive cannot contradict them because receipts record only the order service as the writer.

- cites: `store:receipt-archive`, `entity:shopper`
- tier: must-find · severity: medium/medium
- recorded note: The one audit record in the system carries the service identity, never the authenticated shopper.

> mark:

**10.** The processor disputes a settlement the storefront recorded, and no verifiable sender identity on the webhook lets either side prove who sent it.

- cites: `flow:card-processor-to-storefront-api:settlement-webhook`
- tier: expected · severity: medium/medium
- recorded note: Follows from unknown callback authentication; a financial dispute path.

> mark:

**11.** An operator makes a change through the shared application account and no record attributes that change to a person.

- cites: `flow:order-service-to-orders-db:read-write-orders`, `store:orders-db`
- tier: expected · severity: medium/medium
- recorded note: Shared credential collapses every actor into one database identity.

> mark:

### information-disclosure

**12.** An attacker who reaches the database storage or its backups reads shopper addresses and card last-four, because protection at rest is unverified.

- cites: `store:orders-db`
- tier: must-find · severity: medium/high
- recorded note: encryption_at_rest is unknown on a store tagged pii and financial — report as unverified, and a needs-info verdict is acceptable here.

> mark:

**13.** An attacker with access to the internal network reads order contents and shopper identifiers off the gRPC channel, whose encryption is unverified.

- cites: `flow:storefront-api-to-order-service:submit-order`
- tier: must-find · severity: medium/high
- recorded note: Same flow as the tampering entry; the lane difference is read versus modify.

> mark:

**14.** An attacker observing the database connection reads PII in transit because transport encryption on it is unverified.

- cites: `flow:order-service-to-orders-db:read-write-orders`
- tier: expected · severity: medium/high
- recorded note: Intra-zone flow, so lower exposure than the crossing above, but the same unknown.

> mark:

**15.** An attacker who can read the order service's process environment, crash dumps or logs recovers the database password held in an environment variable.

- cites: `process:order-service`, `flow:order-service-to-orders-db:read-write-orders`
- tier: expected · severity: medium/high
- recorded note: The credential's storage location is stated in the model, so this is grounded rather than speculative.

> mark:

### denial-of-service

**16.** An attacker floods the unauthenticated settlement webhook endpoint until the storefront API stops serving shoppers.

- cites: `flow:card-processor-to-storefront-api:settlement-webhook`, `process:storefront-api`
- tier: must-find · severity: medium/medium
- recorded note: An internet-facing endpoint with no verified caller identity is the cheapest flood target in the model.

> mark:

**17.** An attacker drives enough order submissions to exhaust the order service's database connections and halt order capture.

- cites: `process:order-service`, `store:orders-db`
- tier: expected · severity: medium/high
- recorded note: Order service is tagged availability-critical and has one datastore dependency.

> mark:

**18.** An attacker floods the checkout path from the public internet and prevents shoppers from placing orders.

- cites: `flow:shopper-to-storefront-api:place-order`, `process:storefront-api`
- tier: expected · severity: medium/medium
- recorded note: Generic but grounded: the storefront API is the single internet-facing component.

> mark:

### elevation-of-privilege

**19.** An attacker with any foothold in the storefront tier gains order-writing privilege in the core zone, because the order service grants it on network position alone.

- cites: `flow:storefront-api-to-order-service:submit-order`, `process:order-service`
- tier: must-find · severity: high/high
- recorded note: The boundary crossing plus authentication none is the highest-signal fact in this model.

> mark:

**20.** An attacker who compromises the order service inherits full read/write over every order record, because the service holds one unscoped database account.

- cites: `flow:order-service-to-orders-db:read-write-orders`, `store:orders-db`
- tier: must-find · severity: medium/high
- recorded note: Blast radius of a single compromise; distinct from the tampering entry, which assumes the credential is already held.

> mark:

**21.** An attacker who compromises the internet-facing storefront API pivots from the DMZ into the core zone.

- cites: `process:storefront-api`, `process:order-service`
- tier: expected · severity: medium/high
- recorded note: The pivot itself, stated against the two processes rather than the flow between them.

> mark:

---

## Part 3 — the 17 recorded ASVS records

Same marks: `agree`, `doubt`, `dup`.

An ASVS record asserts that a requirement **applies and the input does not show
it satisfied**. It never asserts a pass — verification needs source code and the
people who built the system, and a job here carries prose. So the question for
each is narrower than in Part 2:

> Does this requirement genuinely apply to this system, and is it true that the
> submitted text does not settle it?

A `doubt` here means one of two things, and say which: the requirement does not
apply to this system at all, or the text *does* settle it and the record should
not be raised.


### api-and-web-service

**A1.** `V4.1.1` — The storefront API's response content types are never described.

- cites: `process:storefront-api`
- tier: expected
- recorded note: An HTTP surface exists on two flows; the header policy is unstated.

> mark:

### authentication

**A2.** `V6.2.1` — No password length policy is stated for shopper accounts.

- cites: `entity:shopper`, `process:storefront-api`
- tier: must-find
- recorded note: feature:password-auth fires. The mechanism is named and no parameter of it is.

> mark:

**A3.** `V6.3.1` — The shopper login carries no second factor and no stated anti-automation control.

- cites: `entity:shopper`, `flow:shopper-to-storefront-api:place-order`
- tier: must-find
- recorded note: The submitter states there is no MFA, so this half is settled rather than open.

> mark:

### authorization

**A4.** `V8.2.2` — One shared account holds full read and write on every order record.

- cites: `process:order-service`, `store:orders-db`, `flow:order-service-to-orders-db:read-write-orders`
- tier: must-find
- recorded note: Stated outright, so the ruling is plain rather than conditional.

> mark:

### configuration

**A5.** `V13.3.1` — The database password is held in an environment variable rather than a secret store.

- cites: `process:order-service`, `flow:order-service-to-orders-db:read-write-orders`
- tier: must-find
- recorded note: Stated outright by the submitter.

> mark:

### cryptography

**A6.** `V11.3.2` — No cipher is stated for the order database at rest.

- cites: `store:orders-db`
- tier: must-find
- recorded note: encryption_at_rest is unknown: an open question, not a missing control.

> mark:

### data-protection

**A7.** `V14.1.1` — No protection requirement follows from the order database's confidential classification.

- cites: `store:orders-db`
- tier: expected
- recorded note: The model answers the classification half and nothing answers the controls half.

> mark:

### encoding-and-sanitization

**A8.** `V1.2.4` — The order service reaches a PostgreSQL store and the input never says how its queries are built.

- cites: `process:order-service`, `store:orders-db`, `flow:order-service-to-orders-db:read-write-orders`
- tier: must-find
- recorded note: tech:database fires on store:orders-db. The requirement applies and no fact settles it.

> mark:

### secure-coding-and-architecture

**A9.** `V15.2.1` — Nothing states whether the Node and Python components are within their update windows.

- cites: `process:storefront-api`, `process:order-service`
- tier: expected
- recorded note: A code-practice requirement: the input carries prose rather than a dependency inventory.

> mark:

### secure-communication

**A10.** `V12.3.3` — The gRPC link between the storefront API and the order service states no transport protection.

- cites: `process:storefront-api`, `process:order-service`, `flow:storefront-api-to-order-service:submit-order`
- tier: must-find
- recorded note: A derived crossing with encryption_in_transit unknown; the internal scope is not an exemption.

> mark:

### security-logging-and-error-handling

**A11.** `V16.2.1` — Receipt archive entries name the order service rather than the acting shopper.

- cites: `store:receipt-archive`, `flow:order-service-to-receipt-archive:append-receipt`
- tier: must-find
- recorded note: The submitter states the record's content, so the ruling is plain.

> mark:

**A12.** `V16.5.1` — Nothing describes what the storefront API returns to a caller when an order fails.

- cites: `process:storefront-api`
- tier: expected
- recorded note: Applies to every internet-facing surface and is unstated here.

> mark:

### session-management

**A13.** `V7.4.1` — Nothing states whether a terminated shopper session stops being accepted.

- cites: `process:storefront-api`, `flow:shopper-to-storefront-api:place-order`
- tier: must-find
- recorded note: A session cookie exists, so the chapter applies; termination is unstated.

> mark:

**A14.** `V7.2.3` — The session token's generation and entropy are never described.

- cites: `process:storefront-api`
- tier: expected
- recorded note: A framework default is not evidence about the token.

> mark:

### validation-and-business-logic

**A15.** `V2.1.1` — No documented input validation rules exist for the order payload the storefront API accepts.

- cites: `process:storefront-api`
- tier: expected
- recorded note: A documentation requirement: the subject is outside the running system, so needs-info by construction.

> mark:

**A16.** `V2.2.2` — Nothing says which side enforces validation of the order the shopper submits.

- cites: `entity:shopper`, `process:storefront-api`, `flow:shopper-to-storefront-api:place-order`
- tier: must-find
- recorded note: The crossing from the public zone is the fact that makes the requirement apply.

> mark:

### web-frontend-security

**A17.** `V3.3.1` — The shopper session cookie is named and none of its attributes is stated.

- cites: `entity:shopper`, `process:storefront-api`, `flow:shopper-to-storefront-api:place-order`
- tier: must-find
- recorded note: A browser-delivered credential puts this system in the chapter; the attributes are the requirement.

> mark:

---

## What was on your list and not on either of theirs

The point of the sitting. One line each, and say which set you expected it in.

```
-
-
```

---

## What to do with the result

**Counts first**, kept apart per framework. How many `agree`, `doubt`, `dup` in
Part 2 and in Part 3, and how many of your own items are missing from either.

- **Few doubts, nothing important missing.** The control case holds. Record the
  sign-off (below) and the suite's recall numbers have a real standard behind
  them for the first time.
- **A whole class of attack missing from Part 2.** That is the serious outcome.
  Recall is measured against the STRIDE list, so the tool has been scoring full
  marks for a gap nobody could see. The reference set needs extending, and every
  recall figure quoted so far needs re-deriving.
- **A requirement missing from Part 3, or one that does not apply.** No number
  moves, because nothing scores this set yet. It matters anyway: #200 will build
  the applicability matrix *against this list*, so an error here becomes a wrong
  number the moment that lands rather than a wrong number today.
- **Several doubts.** The list overstates. That inflates the recall denominator
  and makes the tool look worse than it is, which is the cheaper direction but
  still wrong.

**Then record the sign-off.** Add this to `evals/corpus/01-payments-checkout/case.json`,
which is what `tests/test_case_review.py` reads:

```json
  "review": {
    "reviewer": "<your name or handle>",
    "date": "<YYYY-MM-DD>",
    "read": ["source.md", "model.json", "claims/stride.json", "claims/asvs.json"],
    "notes": "<counts, and anything you changed>"
  },
```

Then remove `01-payments-checkout` from `UNREVIEWED` in
`tests/test_case_review.py`. The test fails until you do, which is deliberate —
the debt list is only honest if it shrinks when the debt is paid.

`tests/test_case_review.py` checks that `read` covers every framework the case
declares, so both entries are required — a `read` list naming `claims/stride.json`
alone leaves the case in debt and the test says so.
