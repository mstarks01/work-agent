# Review sitting — is `13-dispatch-control-plane`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/13-dispatch-control-plane`.

**Browser dispatch console reaching a production control plane, fed by a partner's SOAP schedule** — domain `field-service-dispatch`.

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

## Part 1 — the system, and your own list

### System description (description)

Exactly what the service would receive.

> Dispatch control console.
>
> We run our own field dispatch platform. Every part of it below is ours, apart
> from the scheduling partner at the end.
>
> Duty engineers sit on the corporate network. They open the dispatch console in
> their browser. The console is a single-page JavaScript app and our corporate web
> host serves it. Nobody wrote down how an engineer signs in to it.
>
> The console calls the dispatch API. The dispatch API runs in the production
> control plane. The control plane is the same company and the same staff, but it
> is the zone that holds the live estate: an account there can move a crew, and it
> can change the work a depot does that day. A corporate laptop holds none of
> those rights. The control plane is not reachable from the internet.
>
> The console and the API are served from different origins, so the API answers
> the console cross-origin. Whoever configured CORS did not record which origins
> the API allows, or whether it allows credentials.
>
> The console also opens a WebSocket to the dispatch API and holds it open for
> live job status. Nobody wrote down whether that socket runs over TLS, whether
> its handshake is authenticated, or whether it checks the engineer's session
> again after it is open.
>
> The dispatch API reads and writes the dispatch database. That database holds the
> job orders, the crew names and the crew mobile numbers. Every job order in it
> carries the token or the session that created it, and nothing about the person
> behind that token. How the database is protected at rest is not written down
> anywhere.
>
> A scheduling partner, which is another company, publishes tomorrow's planned
> work. Our schedule importer runs on the corporate network. Every hour it pulls
> the partner's SOAP feed over HTTPS and parses the XML document it gets back. It
> writes each document it downloads into the schedule archive, a folder on the
> corporate file store. Nothing was written down about how the partner identifies
> our importer, or how our importer identifies the partner.
>
> The importer then posts the parsed work orders to the same dispatch API the
> console calls. It presents an API token. That token was issued when the importer
> was built and nobody has rotated it since.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:duty-engineer | human | boundary:corporate-network |
| entity:scheduling-partner | external-system | boundary:scheduling-partner-platform |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:dispatch-console | unknown | web | boundary:corporate-network | single-page JavaScript app in the browser |
| process:dispatch-api | internal | web | boundary:production-control-plane | unknown |
| process:schedule-importer | unknown | non-web | boundary:corporate-network | parses the partner's SOAP feed as an XML document; no product named |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:dispatch-database | boundary:production-control-plane | unknown | unknown |
| store:schedule-archive | boundary:corporate-network | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:duty-engineer-to-dispatch-console:open-console | entity:duty-engineer | process:dispatch-console | unknown | unknown | unknown |
| flow:dispatch-console-to-dispatch-api:dispatch-requests | process:dispatch-console | process:dispatch-api | unknown | unknown | unknown |
| flow:dispatch-console-to-dispatch-api:live-job-status | process:dispatch-console | process:dispatch-api | WebSocket | unknown | unknown |
| flow:schedule-importer-to-scheduling-partner:pull-schedule-feed | process:schedule-importer | entity:scheduling-partner | SOAP over HTTPS | unknown | encrypted (HTTPS) |
| flow:schedule-importer-to-schedule-archive:store-schedule-documents | process:schedule-importer | store:schedule-archive | unknown | unknown | unknown |
| flow:schedule-importer-to-dispatch-api:post-work-orders | process:schedule-importer | process:dispatch-api | unknown | an API token issued when the importer was built and never rotated since | unknown |
| flow:dispatch-api-to-dispatch-database:read-write-job-orders | process:dispatch-api | store:dispatch-database | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:corporate-network | network |
| boundary:production-control-plane | privilege |
| boundary:scheduling-partner-platform | tenant |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `store:dispatch-database` — The source states the at-rest protection is not written down anywhere, so this is a gap somebody registered rather than a topic nobody raised.
- `flow:duty-engineer-to-dispatch-console:open-console` — The source states nobody wrote down how an engineer signs in.
- `flow:dispatch-console-to-dispatch-api:live-job-status` — The source states nobody wrote down whether the socket runs over TLS, whether its handshake is authenticated, or whether it checks the session again once open.
- `flow:schedule-importer-to-scheduling-partner:pull-schedule-feed` — The source states nothing was written down about how the partner identifies our importer, or how our importer identifies the partner.

**Assumptions**

- `process:dispatch-api` — The dispatch API is not reachable from the internet. (basis: The source states the control plane is not reachable from the internet, and places the dispatch API in it; the source states nothing about the API's own exposure.)

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

## Part 2 — the 6 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### encoding-and-sanitization

**A1.** `V1.5.1` — The importer parses a partner's SOAP feed as XML and nothing states whether external entities are disabled.

- cites: `process:schedule-importer`, `flow:schedule-importer-to-scheduling-partner:pull-schedule-feed`
- tier: must-find
- recorded note: The corpus's first XML parse, and the reason this case was authored for the encoding-and-sanitization lane.

> mark:


### web-frontend-security

**A2.** `V3.4.2` — Nothing states which origins the dispatch API returns in its Access-Control-Allow-Origin header.

- cites: `process:dispatch-api`, `process:dispatch-console`, `flow:dispatch-console-to-dispatch-api:dispatch-requests`
- tier: must-find
- recorded note: The source states the policy exists and states that nobody recorded its origins, so this is an unknown somebody registered rather than an absent control.

> mark:

**A3.** `V3.5.1` — Nothing states what validates a cross-origin dispatch request as one the console originated.

- cites: `process:dispatch-api`, `flow:dispatch-console-to-dispatch-api:dispatch-requests`
- tier: expected
- recorded note: Separate from the header's value: one requirement asks which origins are named and this one asks what checks the request itself.

> mark:


### api-and-web-service

**A4.** `V4.4.1` — Nothing states whether the live job status WebSocket runs over TLS.

- cites: `flow:dispatch-console-to-dispatch-api:live-job-status`
- tier: must-find
- recorded note: The corpus's first WebSocket, and the transport is the attribute the source leaves unwritten by name.

> mark:


### session-management

**A5.** `V7.2.1` — Nothing states whether the dispatch API checks an engineer's session again once the socket is open.

- cites: `flow:dispatch-console-to-dispatch-api:live-job-status`, `process:dispatch-api`
- tier: expected
- recorded note: A long-lived channel is where session verification stops being a per-request property, which is the shape no other case carries.

> mark:


### authentication

**A6.** `V6.1.1` — No documentation states how an engineer signs in to the console or what limits repeated attempts.

- cites: `flow:duty-engineer-to-dispatch-console:open-console`, `process:dispatch-console`
- tier: expected
- recorded note: The source states the sign-in was never written down, which is the documentation this requirement asks for.

> mark:

## Part 3 — the 19 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a finding here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker who holds the importer's never-rotated API token posts work orders to the dispatch API as the importer.

- cites: `flow:schedule-importer-to-dispatch-api:post-work-orders`, `process:schedule-importer`, `process:dispatch-api`
- tier: must-find · severity: medium/high · verb: `use-credential`
- recorded note: The token is the case's one fully stated credential: issued once at build time and never rotated. It is also the only stated authentication on any crossing into the control plane, so a lane that misses it is reading nothing the source gave it.

> mark:

**2.** An attacker opens a WebSocket to the dispatch API in a duty engineer's name, because nothing states what the handshake checks.

- cites: `flow:dispatch-console-to-dispatch-api:live-job-status`, `process:dispatch-api`
- tier: must-find · severity: medium/medium · verb: `impersonate`
- recorded note: The source states the handshake's authentication was never written down, which is an unknown somebody registered rather than a topic nobody raised.

> mark:

**3.** An attacker answers the hourly pull as the scheduling partner, because nothing states how the importer identifies the partner.

- cites: `flow:schedule-importer-to-scheduling-partner:pull-schedule-feed`, `entity:scheduling-partner`, `process:schedule-importer`
- tier: expected · severity: low/high · verb: `impersonate`
- recorded note: Kept apart from the tampering entry on the same flow: standing in for the partner is the action here, and what the returned document then carries is the other.

> mark:

**4.** An attacker signs in to the dispatch console as a duty engineer, because nothing states what the sign-in checks.

- cites: `flow:duty-engineer-to-dispatch-console:open-console`, `process:dispatch-console`
- tier: expected · severity: medium/high · verb: `impersonate`
- recorded note: The console is the near end of every crossing into the control plane, so the weakest gate on it is the authority over the estate.

> mark:


### tampering

**5.** An attacker returns an XML document to the hourly pull that plants work orders the partner never sent.

- cites: `flow:schedule-importer-to-scheduling-partner:pull-schedule-feed`, `process:schedule-importer`, `process:dispatch-api`
- tier: must-find · severity: medium/high · verb: `forge`
- recorded note: The path the case is built on: a document from outside becomes work the control plane hands to a depot, and nothing between the two checks it.

> mark:

**6.** An attacker on the corporate network alters a dispatch request on its way into the control plane, because nothing states the call is encrypted.

- cites: `flow:dispatch-console-to-dispatch-api:dispatch-requests`, `process:dispatch-api`
- tier: must-find · severity: medium/high · verb: `alter-in-transit`
- recorded note: encryption_in_transit is unknown on the one flow that carries crew moves, and the crossing it makes is into the privilege zone.

> mark:

**7.** An attacker inside the control plane writes job orders straight into the dispatch database, because nothing states what the connection checks.

- cites: `store:dispatch-database`, `flow:dispatch-api-to-dispatch-database:read-write-job-orders`
- tier: expected · severity: low/high · verb: `forge`
- recorded note: Distinct from the transit entry: writing at the store is a different action from altering a request in flight.

> mark:

**8.** An attacker rewrites a document in the schedule archive so the kept copy no longer matches what the partner sent.

- cites: `store:schedule-archive`, `flow:schedule-importer-to-schedule-archive:store-schedule-documents`
- tier: expected · severity: low/medium · verb: `alter`
- recorded note: The archive is written after the parse, so this alters the record of the day rather than the work itself.

> mark:


### repudiation

**9.** A duty engineer denies moving a crew, and the job order names only the token or session that wrote it.

- cites: `store:dispatch-database`, `process:dispatch-api`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: Stated in the source rather than unknown: every job order carries the token or session and nothing about the person behind it.

> mark:

**10.** The scheduling partner denies sending a work order, and nothing ties the posted order to the archived document it was parsed from.

- cites: `process:schedule-importer`, `flow:schedule-importer-to-dispatch-api:post-work-orders`, `store:schedule-archive`
- tier: expected · severity: low/medium · verb: `unattributable`
- recorded note: The archive holds the documents and the database holds the orders, and the source states no link between them.

> mark:


### information-disclosure

**11.** An attacker on the corporate network reads the live job status stream, because nothing states the WebSocket runs over TLS.

- cites: `flow:dispatch-console-to-dispatch-api:live-job-status`
- tier: must-find · severity: medium/medium · verb: `intercept`
- recorded note: The transport of the long-lived channel is the attribute the source explicitly leaves unwritten.

> mark:

**12.** An attacker who obtains a copy of the dispatch database reads the crew names and mobile numbers in it.

- cites: `store:dispatch-database`
- tier: must-find · severity: low/high · verb: `read`
- recorded note: The contents are stated and the at-rest protection is stated to be unwritten, so pii comes from the content while data_classification stays unknown.

> mark:

**13.** An attacker returns an XML document whose external entity makes the importer read a file off the corporate network and hand it back.

- cites: `process:schedule-importer`, `flow:schedule-importer-to-scheduling-partner:pull-schedule-feed`
- tier: expected · severity: low/high · verb: `elicit`
- recorded note: The parser's configuration is unstated, which is exactly what the ASVS encoding-and-sanitization record rules on for the same element.

> mark:

**14.** An attacker who reaches the corporate file store reads the partner's schedule documents out of the archive.

- cites: `store:schedule-archive`, `flow:schedule-importer-to-schedule-archive:store-schedule-documents`
- tier: expected · severity: low/medium · verb: `read`
- recorded note: The archive sits in the corporate zone, so it is the one copy of the day's work an attacker reaches without crossing into the control plane.

> mark:


### denial-of-service

**15.** An attacker holds open enough WebSockets to the dispatch API that duty engineers lose live job status.

- cites: `process:dispatch-api`, `flow:dispatch-console-to-dispatch-api:live-job-status`
- tier: must-find · severity: medium/medium · verb: `flood`
- recorded note: A long-lived channel is the availability shape a request-response API does not have, and the API serves both the console and the importer.

> mark:

**16.** An attacker returns an XML document large enough that the hourly import never finishes and tomorrow's work never reaches the control plane.

- cites: `process:schedule-importer`, `flow:schedule-importer-to-scheduling-partner:pull-schedule-feed`
- tier: expected · severity: low/medium · verb: `flood`
- recorded note: The failure is silent staleness rather than an outage: the API keeps answering and the work it holds is yesterday's.

> mark:


### elevation-of-privilege

**17.** An attacker on the corporate network uses the importer's token to act in the control plane, where a corporate laptop holds no rights at all.

- cites: `flow:schedule-importer-to-dispatch-api:post-work-orders`, `process:dispatch-api`, `boundary:production-control-plane`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The case's central claim, and the one the privilege boundary exists to put in front of a lane: same company on both sides, and the authority is on the far side.

> mark:

**18.** An attacker gets a duty engineer's browser to dispatch a crew from a page the attacker controls, because the API's allowed origins and its credential rule are unrecorded.

- cites: `flow:dispatch-console-to-dispatch-api:dispatch-requests`, `process:dispatch-api`
- tier: must-find · severity: medium/high · verb: `ride-session`
- recorded note: The browser holds the authority and the origin rule is what decides who may spend it, which is why an unrecorded CORS policy is an elevation rather than a header hygiene point.

> mark:

**19.** An attacker who can publish on the corporate web host serves script from the console's own origin and dispatches crews with it.

- cites: `process:dispatch-console`, `flow:dispatch-console-to-dispatch-api:dispatch-requests`
- tier: expected · severity: low/high · verb: `inject`
- recorded note: Distinct from the cross-origin entry: this attacker takes the origin the API trusts rather than working around what it allows.

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

**Counts first**, kept apart per framework: how many `agree`, `doubt`, `dup`
per part, and how many of your own items are missing from either set.

- **Few doubts, nothing important missing** — the sets hold, and the numbers
  measured against them have a standard behind them.
- **A whole class of attack missing** — the serious outcome. Recall is measured
  against these sets, so the tool has been scoring full marks for a gap nobody
  could see. Extend the set, and re-derive what was quoted against it.
- **Several doubts** — the sets overstate, inflating the denominator. Cheaper
  direction, still wrong.

**Then record the sign-off.** Add this to
`evals/corpus/13-dispatch-control-plane/case.json`, which is what
`tests/test_case_review.py` reads:

```json
  "review": {
    "reviewer": "<your name or handle>",
    "date": "<YYYY-MM-DD>",
    "read": ["source.md", "model.json", "claims/asvs.json", "claims/stride.json"],
    "notes": "<counts, and anything you changed>"
  },
```

If this case is named in `UNREVIEWED` in `tests/test_case_review.py`, delete
its line. That list names the cases nobody has read, so it is only accurate
while a reviewed case comes off it. A case not named there is new, and merges
with this block from the start.

`tests/test_case_review.py` checks that `read` covers every framework the case
declares, so every claims file above is required.
