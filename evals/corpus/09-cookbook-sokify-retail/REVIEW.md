# Review sitting — is `09-cookbook-sokify-retail`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/09-cookbook-sokify-retail`.

**Online sock retailer with a macro-driven SQL path and a fax dispatch leg** — domain `online-retail`.

**What you are checking.** Not whether two write-ups are the same threat — the
identity rule decides that mechanically. This asks the question underneath:
**do these reference sets describe what could actually go wrong with this
system?** If a set misses a whole class of attack, the tool scores full marks
for missing it too, and nothing in the repo would ever say so.

## The one rule

**Read Part 1 and write your own list before you open Part 2.** If you read
the recorded threats first you will find them reasonable, and the sitting
measures nothing. Your list does not have to be good or complete — it only has
to be yours, written first.

Roughly an hour.

---

## Part 1 — the system

### System description (description)

Exactly what the service would receive.

> Sokify order and dispatch — rough notes for the threat model.
>
> Sokify sells socks online. Customers browse and order through our mobile app;
> there is no website. The app talks to the web API over HTTP — not HTTPS, it's
> on the list.
>
> The web API keeps customers in the user database: name, address, and the card
> they paid with. When an order is placed the API hands the order over to SIMS,
> our stock and inventory system, which has been running since long before the
> app existed.
>
> SIMS does two things with an order. It writes the delivery address into a flat
> file kept alongside it — only addresses go in that file, nothing else — and it
> sends a dispatch note to the fax gateway, which faxes the customer a
> confirmation with their name and address on it. Yes, fax. The gateway dials the
> number stored against the order and nobody checks it arrived at the right
> place.
>
> Marketing keep the catalogue in a spreadsheet. The macros in it send SQL
> statements straight to the web API to change prices and product copy — it was a
> stopgap and it is still here. The spreadsheet lives on a marketing laptop in
> the office.
>
> Nobody here can tell me what the API does about authentication, or whether the
> user database and the flat file are encrypted.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:customer | human | boundary:customer-device |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:mobile-app | unknown | non-web | boundary:customer-device | mobile app |
| process:web-api | internet-facing | web | boundary:sokify-internal-systems | unknown |
| process:sims | unknown | unknown | boundary:sokify-internal-systems | unknown |
| process:fax-gateway | unknown | non-web | boundary:sokify-internal-systems | fax |
| process:catalogue-spreadsheet | unknown | non-web | boundary:marketing-office | spreadsheet with macros |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:user-database | boundary:sokify-internal-systems | unknown | Customer name, address, and the card they paid with |
| store:delivery-address-flat-file | boundary:sokify-internal-systems | unknown | Delivery addresses only |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:customer-to-mobile-app:browse-and-order | entity:customer | process:mobile-app | unknown | unknown | unknown |
| flow:mobile-app-to-web-api:api-traffic | process:mobile-app | process:web-api | HTTP | unknown | none — the app talks to the web API over HTTP, not HTTPS |
| flow:catalogue-spreadsheet-to-web-api:sql-statements | process:catalogue-spreadsheet | process:web-api | SQL statements over an unstated transport | unknown | unknown |
| flow:web-api-to-user-database:customer-records | process:web-api | store:user-database | unknown | unknown | unknown |
| flow:web-api-to-sims:order-handover | process:web-api | process:sims | unknown | unknown | unknown |
| flow:sims-to-delivery-address-flat-file:address-write | process:sims | store:delivery-address-flat-file | unknown | unknown | unknown |
| flow:sims-to-fax-gateway:dispatch-note | process:sims | process:fax-gateway | unknown | unknown | unknown |
| flow:fax-gateway-to-customer:confirmation-fax | process:fax-gateway | entity:customer | fax | the dialled destination is never verified; nobody checks the fax arrived at the right place | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:customer-device | network |
| boundary:sokify-internal-systems | network |
| boundary:marketing-office | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:mobile-app` — Tagged pii because the app is the only stated route by which a customer's details reach the web API — the source rules out a website. It is not tagged financial: the source never says where payment details are captured, only that the database holds the card they paid with.
- `process:web-api` — The source states outright that nobody can say what the API does about authentication, so every flow into it holds authentication unknown rather than absent.
- `process:fax-gateway` — The source does not say whether the gateway is operated in-house or by a third party; it is zoned with Sokify's own systems on the strength of being described as part of what SIMS does with an order.
- `process:catalogue-spreadsheet` — Typed as a process rather than an external entity: it is the org's own tooling and the source describes it by what its macros execute, not by who operates it. It is the resting place of the catalogue and the code that pushes SQL, and is modelled once.
- `flow:fax-gateway-to-customer:confirmation-fax` — The stated absence is destination verification, not authentication in general — the source says nobody checks the fax arrived at the right place, and says nothing else about how this leg is controlled.
- `boundary:sokify-internal-systems` — The source names no network segments; this zone groups the components described as Sokify's own systems.

**Assumptions**

- `process:web-api` — The web API is reachable from the internet. (basis: The mobile app is the only customer channel and talks to the web API over HTTP from customers' own devices.)
- `process:mobile-app` — The customer and the mobile app sit outside Sokify's own systems, in a customer-device zone. (basis: The app is the channel through which customers browse and order, and it reaches the web API over HTTP; the source names no other placement for it.)
- `process:catalogue-spreadsheet` — The marketing laptop and its spreadsheet sit in a separate office zone from the server-side systems. (basis: "The spreadsheet lives on a marketing laptop in the office", distinct from where the web API and SIMS are described as running.)

### Your list

Write what could go wrong. Anything: an attack, a missing control, a question
the text does not answer. Bullet points, in any order, no need to sort by
category.

```
-
-
-
```

---

## Part 2 — the 7 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### secure-communication

**A1.** `V12.2.1` — The mobile app reaches the web API over HTTP rather than HTTPS.

- cites: `process:mobile-app`, `process:web-api`, `flow:mobile-app-to-web-api:api-traffic`
- tier: must-find
- recorded note: encryption_in_transit is stated absent, so the ruling is plain rather than conditional.

> mark:


### data-protection

**A2.** `V14.2.1` — Customer names, addresses and card details are held with no stated protection.

- cites: `store:user-database`, `process:web-api`, `flow:web-api-to-user-database:customer-records`
- tier: must-find
- recorded note: The classification is stated and nothing that follows from it is.

> mark:


### encoding-and-sanitization

**A3.** `V1.2.4` — A macro-bearing spreadsheet sends SQL statements to the web API over an unstated transport.

- cites: `process:catalogue-spreadsheet`, `process:web-api`, `flow:catalogue-spreadsheet-to-web-api:sql-statements`
- tier: must-find
- recorded note: The strongest injection trigger in the corpus: raw SQL from a client the model names.

> mark:


### secure-coding-and-architecture

**A4.** `V15.3.1` — The web API serves customer records to the mobile app and nothing states which fields a response carries.

- cites: `process:mobile-app`, `process:web-api`, `flow:mobile-app-to-web-api:api-traffic`
- tier: must-find
- recorded note: The delivery file receives addresses only, and the source says so. The open subset question is what the API returns to the app.

> mark:


### authentication

**A5.** `V6.1.1` — Nothing states how a customer is authenticated to the mobile app.

- cites: `entity:customer`, `process:mobile-app`, `flow:customer-to-mobile-app:browse-and-order`
- tier: must-find
- recorded note: authentication is unknown on the one human-facing flow.

> mark:


### authorization

**A6.** `V8.2.2` — Nothing restricts what the web API may read from the customer record store.

- cites: `process:web-api`, `store:user-database`
- tier: expected
- recorded note: The store holds payment card data and the flow's authentication is unknown.

> mark:


### web-frontend-security

**A7.** `V3.5.3` — Nothing states which HTTP methods the web API accepts for order submission.

- cites: `process:web-api`, `flow:mobile-app-to-web-api:api-traffic`
- tier: expected
- recorded note: An HTTP surface is stated; the method policy is not.

> mark:

## Part 3 — the 20 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `reject` — overstated, unsupported by the text, or not really a finding here.
- `duplicate` — the same finding as another entry on this list, by number.
- `unsure` — you read it and cannot decide. It is a real answer: say it rather
  than pick one of the other three to get past the entry.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker submits orders to the web API as if they came from the mobile app, since how the API authenticates callers is unverified.

- cites: `flow:mobile-app-to-web-api:api-traffic`, `process:web-api`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: The source states outright that nobody can say what the API does about authentication, and the app is the only stated client; needs-info is an acceptable verdict here, silence is not.

> mark:

**2.** An attacker who obtains a copy of the catalogue spreadsheet sends SQL to the web API as the catalogue tool.

- cites: `flow:catalogue-spreadsheet-to-web-api:sql-statements`, `process:catalogue-spreadsheet`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: The macro path's authority is carried by a file on a laptop; nothing in the source identifies who is driving it.

> mark:

**3.** An attacker faxes a customer what appears to be a Sokify order confirmation, since nothing on the fax leg identifies the sender.

- cites: `flow:fax-gateway-to-customer:confirmation-fax`, `entity:customer`
- tier: expected · severity: low/medium · verb: `forge`
- recorded note: The reverse direction of the case's signature weakness: the model records that the destination is never verified, and the leg carries no sender identity either.

> mark:


### tampering

**4.** An attacker on the network path alters order details in flight between the app and the web API, because that traffic runs over plain HTTP.

- cites: `flow:mobile-app-to-web-api:api-traffic`
- tier: must-find · severity: high/medium · verb: `alter-in-transit`
- recorded note: The missing TLS is stated by the source rather than inferred, so this is the one claim in the case that rests on no unknown at all.

> mark:

**5.** An attacker drives the spreadsheet's macros to change catalogue prices through the web API.

- cites: `flow:catalogue-spreadsheet-to-web-api:sql-statements`, `process:web-api`
- tier: must-find · severity: medium/high · verb: `alter`
- recorded note: The stated purpose of the path, exercised by the wrong party; distinct from injecting statements the path was never meant to carry.

> mark:

**6.** An attacker appends further SQL to the statements the macros send so the web API executes changes beyond prices and product copy.

- cites: `flow:catalogue-spreadsheet-to-web-api:sql-statements`, `process:web-api`
- tier: must-find · severity: medium/high · verb: `inject`
- recorded note: Injection through a path that carries statements rather than parameters; kept distinct from the price-change claim because the target differs. `inject` against a claim about altering a price is a verb difference as well as an element one.

> mark:

**7.** An attacker who can write to the flat file rewrites a delivery address so goods are dispatched somewhere else.

- cites: `store:delivery-address-flat-file`, `process:sims`
- tier: expected · severity: low/high · verb: `alter`
- recorded note: The file sits alongside SIMS with no stated protection; the impact is physical goods, not records.

> mark:

**8.** An attacker changes the number stored against an order so the confirmation is faxed to a machine they control.

- cites: `flow:web-api-to-sims:order-handover`, `process:sims`
- tier: expected · severity: low/medium · verb: `alter`
- recorded note: Reaches the disclosure below by a tampering route; the two are separate findings because the actions differ.

> mark:


### repudiation

**9.** A customer denies placing an order and Sokify cannot show who submitted it, because how the API authenticates callers is unverified.

- cites: `process:web-api`, `flow:mobile-app-to-web-api:api-traffic`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: The same unknown as the spoofing claim, filed for what it costs after the fact rather than for the access it grants.

> mark:

**10.** Nobody can show who made a price change, because the changes arrive at the web API as SQL from a spreadsheet rather than from an identified user.

- cites: `flow:catalogue-spreadsheet-to-web-api:sql-statements`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: Grounded in the model's shape rather than in a missing control: the source describes no person at the sending end of this flow.

> mark:

**11.** Sokify cannot show a confirmation ever reached the customer, because nobody checks the fax arrived at the right place.

- cites: `flow:fax-gateway-to-customer:confirmation-fax`
- tier: expected · severity: medium/low · verb: `unattributable`
- recorded note: The stated absence read for its evidentiary cost; an analyst that only files the disclosure has read half of it.

> mark:


### information-disclosure

**12.** An attacker on the network path reads customer details out of the app's plain-HTTP traffic to the web API.

- cites: `flow:mobile-app-to-web-api:api-traffic`
- tier: must-find · severity: high/high · verb: `intercept`
- recorded note: The clearest finding in the case and the one that needs no unknown; a run that misses it has not read the source at all.

> mark:

**13.** An attacker who reaches the user database reads customers' names, addresses and the cards they paid with, since at-rest protection is unverified.

- cites: `store:user-database`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: The source names the contents and then says outright that nobody knows whether the store is encrypted.

> mark:

**14.** An attacker who reaches the flat file reads every delivery address written into it.

- cites: `store:delivery-address-flat-file`
- tier: expected · severity: medium/medium · verb: `read`
- recorded note: Lower impact than the database because the source is explicit that only addresses go in the file, which is a stated scope limit rather than an assumed one.

> mark:

**15.** The confirmation fax discloses a customer's name and address to whoever holds the dialled number, because nobody checks it arrived at the right place.

- cites: `flow:fax-gateway-to-customer:confirmation-fax`, `process:fax-gateway`
- tier: must-find · severity: medium/medium · verb: `read`
- recorded note: The case's signature threat and the reason the fax leg is in the corpus at all: a channel where the control is not unverified but unavailable, which is a different thing from unknown.

> mark:


### denial-of-service

**16.** An attacker floods the internet-facing web API until customers cannot place orders.

- cites: `process:web-api`
- tier: expected · severity: medium/medium · verb: `flood`
- recorded note: The app is the only channel the source describes, so losing the API loses all ordering.

> mark:

**17.** An attacker sends SQL through the macro path that locks catalogue rows so the app cannot serve the catalogue.

- cites: `flow:catalogue-spreadsheet-to-web-api:sql-statements`, `process:web-api`
- tier: expected · severity: low/high · verb: `inject`
- recorded note: The same path as the tampering claims, used to deny rather than to alter.

> mark:

**18.** An attacker who stops SIMS accepting order handovers halts dispatch for every order placed.

- cites: `process:sims`, `flow:web-api-to-sims:order-handover`
- tier: expected · severity: low/high · verb: `disable`
- recorded note: SIMS is a single legacy path with no stated alternative; orders are taken but nothing ships.

> mark:


### elevation-of-privilege

**19.** An attacker who reaches the marketing laptop gains a write path into the web API from the office zone, because the spreadsheet's macros speak SQL to it.

- cites: `process:catalogue-spreadsheet`, `flow:catalogue-spreadsheet-to-web-api:sql-statements`, `process:web-api`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The crossing an analyst should see first: an unmanaged endpoint in a different zone holds a standing write path into the server-side estate.

> mark:

**20.** An attacker whose SQL reaches the web API through the catalogue path acts against customer records the catalogue tool has no business touching.

- cites: `flow:catalogue-spreadsheet-to-web-api:sql-statements`, `store:user-database`
- tier: expected · severity: medium/high · verb: `abuse-grant`
- recorded note: The classic escalation shape: a path scoped by intent rather than by a control, reaching whatever authority the API holds over the database.

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

**Counts first**, kept apart per framework: how many `agree`, `reject`,
`duplicate` per part, and how many of your own items are missing from either set.

- **Few `reject` marks, nothing important missing** — the sets hold, and the numbers
  measured against them have a standard behind them.
- **A whole class of attack missing** — the serious outcome. Recall is measured
  against these sets, so the tool has been scoring full marks for a gap nobody
  could see. Extend the set, and re-derive what was quoted against it.
- **Several `reject` marks** — the sets overstate, inflating the denominator. Cheaper
  direction, still wrong.

**Then record the sitting.** This document is your reading aid and the
evidence that the method ran; it is not the record. The record is **one JSON
file** under `evals/review/submissions/`, carrying your own list, your marks,
your missing list, your notes and a digest of each file you read:

```json
{
  "envelope": 1,
  "submitted_by": "<the GitHub login opening the PR>",
  "submitted_for": "<who read the case: a login, or the word anonymous>",
  "generated": "<YYYY-MM-DD>",
  "cases": {
    "09-cookbook-sokify-retail": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "603873a0d569ba3f0ac4a91a363086b54bae022cf432862d17be9eec9465e4ea",
      "model.json": "74673857e4d774b062ea84ae8a2e1d281c7c33d0ab25a2c84ece6021c8f46a8a",
      "claims/asvs.json": "fa211c11c2a82bf649b5518b6d7120ece7720af93b570fdcfb1a56b15a0b0adb",
      "claims/stride.json": "33f48cb7d16f23f0d627279af76e3c66acdf37011ca3fbd319b9a9965af07c2a"
      }
    }
  }
}
```

The app (`uv run python webapp/sitting.py`) and the standalone page both write
that file for you and open the pull request; a reader with no clone lands on
GitHub's editor with it already filled in. The file is named for its own
digest, so an edited file no longer matches its name.

**Two names, because they answer two questions.** `submitted_by` is the account
that opens the pull request and answers for the sitting; contribution CI binds
it to that account, so it needs no roster line. `submitted_for` is who read the
case: the same login where you read it yourself, another login, or `anonymous`
where the reader takes part on no name of their own.

The digests above are the files as they were when this document was
generated. A submission covers the frameworks whose reference sets it carries
a matching digest for, and it stops covering one the moment that file changes.
CI checks that every finding of every set you read carries a mark, that the
digests match the tree, and that the pull request adds this one file and
nothing else. `tests/test_case_review.py` names the cases still waiting, and
derives that list from the merged submissions.
