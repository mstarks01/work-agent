# Review sitting — is `11-sparse-shift-scheduling`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/11-sparse-shift-scheduling`.

**Colleague shift scheduling tool described almost entirely without security detail** — domain `workforce-scheduling`.

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

> Colleague shift scheduling tool.
>
> Store managers use a scheduling web app to build the weekly rota for their
> store. Colleagues use the same app to look at the shifts they have been given.
> Colleagues reach the app from their own phones.
>
> The web app talks to a scheduling service. The scheduling service is what
> actually reads and writes the rotas, which live in a rota database. Colleague
> names, contact details and stated availability are held in that database.
>
> Once a week the scheduling service writes a payroll export onto a file share.
> The payroll system, which is run by another team, collects the export from the
> file share.
>
> The scheduling service, the rota database and the file share are on the
> internal network.
>
> That is as much as we have written down. Nobody has documented how colleagues
> or managers sign in, how the web app and the scheduling service identify each
> other, how the payroll system is identified when it collects the export, or
> whether any of it is encrypted.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:colleague | human | boundary:public-internet |
| entity:store-manager | human | boundary:public-internet |
| entity:payroll-system | external-system | boundary:payroll-team-environment |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:scheduling-web-app | internet-facing | web | boundary:internal-network | unknown |
| process:scheduling-service | internal | unknown | boundary:internal-network | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:rota-database | boundary:internal-network | unknown | unknown |
| store:file-share | boundary:internal-network | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:colleague-to-scheduling-web-app:view-shifts | entity:colleague | process:scheduling-web-app | unknown | unknown | unknown |
| flow:store-manager-to-scheduling-web-app:build-rota | entity:store-manager | process:scheduling-web-app | unknown | unknown | unknown |
| flow:scheduling-web-app-to-scheduling-service:rota-requests | process:scheduling-web-app | process:scheduling-service | unknown | unknown | unknown |
| flow:scheduling-service-to-rota-database:read-write-rotas | process:scheduling-service | store:rota-database | unknown | unknown | unknown |
| flow:scheduling-service-to-file-share:write-payroll-export | process:scheduling-service | store:file-share | unknown | unknown | unknown |
| flow:payroll-system-to-file-share:collect-payroll-export | entity:payroll-system | store:file-share | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:internal-network | network |
| boundary:payroll-team-environment | tenant |

**Assumptions**

- `process:scheduling-web-app` — The scheduling web app is reachable from outside the internal network. (basis: Colleagues are stated to reach it from their own phones, which the source does not place on the internal network.)
- `process:scheduling-web-app` — The scheduling web app itself sits on the internal network. (basis: The source lists the service, database and share as internal and does not place the web app anywhere; it is grouped with them for want of any stated zone of its own.)

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

## Part 2 — the 8 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### encoding-and-sanitization

**A1.** `V1.2.4` — The scheduling service reads and writes the rota database and nothing says how its queries are built.

- cites: `process:scheduling-service`, `store:rota-database`, `flow:scheduling-service-to-rota-database:read-write-rotas`
- tier: must-find
- recorded note: A process reaching a store; query construction unstated.

> mark:


### web-frontend-security

**A2.** `V3.3.1` — Colleagues reach the scheduling web app from their own phones and no cookie attribute is stated.

- cites: `entity:colleague`, `process:scheduling-web-app`, `flow:colleague-to-scheduling-web-app:view-shifts`
- tier: expected
- recorded note: The source says nobody documented how colleagues or managers sign in, which is the silence this requirement lands in.

> mark:


### authentication

**A3.** `V6.1.1` — No documentation defines rate limiting or anti-automation for a sign-in reachable from colleagues' own phones.

- cites: `process:scheduling-web-app`
- tier: must-find
- recorded note: The source states the gap outright: nobody has written down how colleagues or managers sign in.

> mark:


### session-management

**A4.** `V7.2.1` — Nothing says where a colleague's session token is verified, or that a trusted backend does it.

- cites: `process:scheduling-web-app`, `process:scheduling-service`
- tier: expected
- recorded note: The web app holds no rota data of its own, so the verification point is a real question the input does not answer.

> mark:


### authorization

**A5.** `V8.1.1` — No authorization documentation separates what a store manager may do from what a colleague may do.

- cites: `process:scheduling-web-app`, `process:scheduling-service`
- tier: expected
- recorded note: Documentation requirement; both roles reach the same app.

> mark:

**A6.** `V8.2.2` — Colleagues view their own shifts and managers build their own store's rota, and nothing restricts either to their own data.

- cites: `entity:colleague`, `entity:store-manager`, `process:scheduling-service`, `store:rota-database`
- tier: must-find
- recorded note: Two roles over one store of colleague names, contact details and availability. Data-specific access is the requirement and nothing settles it.

> mark:


### secure-communication

**A7.** `V12.2.1` — Colleagues reach the web app from their own phones and the source says nobody documented whether any of it is encrypted.

- cites: `entity:colleague`, `process:scheduling-web-app`, `flow:colleague-to-scheduling-web-app:view-shifts`
- tier: must-find
- recorded note: The case that made #219 concrete: the app is stated to be a web app and its transport is stated to be unrecorded. The requirement applies for the first fact and is unsettled by the second.

> mark:


### data-protection

**A8.** `V14.2.1` — Colleague contact details and availability move through the app and nothing says they stay out of URLs and query strings.

- cites: `process:scheduling-web-app`, `store:rota-database`
- tier: expected
- recorded note: The store is named as holding the data; the transport detail is unstated.

> mark:

## Part 3 — the 16 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `reject` — overstated, unsupported by the text, or not really a finding here.
- `duplicate` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker signs in to the scheduling web app as a colleague and reads that colleague's shifts and details, because how colleagues are authenticated is unverified.

- cites: `flow:colleague-to-scheduling-web-app:view-shifts`, `entity:colleague`
- tier: must-find · severity: medium/medium · verb: `impersonate`
- recorded note: The source explicitly says nobody has documented how colleagues sign in. needs-info is the right verdict here; silence is not.

> mark:

**2.** An attacker signs in as a store manager and builds that store's rota, because how managers are authenticated is unverified.

- cites: `flow:store-manager-to-scheduling-web-app:build-rota`, `entity:store-manager`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: Same unknown as the colleague lane but a materially higher impact, which is why the two are kept as separate references rather than one.

> mark:

**3.** An attacker presents itself to the file share as the payroll system and collects the weekly payroll export, since how the share identifies a collector is unverified.

- cites: `flow:payroll-system-to-file-share:collect-payroll-export`, `store:file-share`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: This is the boundary crossing the source draws most clearly and says least about. The payroll team environment is a zone we are told nothing else about.

> mark:

**4.** An attacker on the internal network calls the scheduling service while claiming to be the web app, because how the two identify each other is unverified.

- cites: `flow:scheduling-web-app-to-scheduling-service:rota-requests`
- tier: expected · severity: medium/high · verb: `impersonate`
- recorded note: Stated as undocumented in the closing paragraph, which is the sentence most likely to be dropped in extraction.

> mark:


### tampering

**5.** An attacker alters the payroll export while it sits on the file share, so the payroll system collects hours nobody worked.

- cites: `store:file-share`, `flow:payroll-system-to-file-share:collect-payroll-export`
- tier: must-find · severity: medium/high · verb: `alter`
- recorded note: The export rests unattended between two independent trust zones, and nothing states any integrity control over it. This is the case's strongest finding.

> mark:

**6.** An attacker who reaches the rota database changes rota entries directly, bypassing whatever the scheduling service enforces.

- cites: `store:rota-database`, `flow:scheduling-service-to-rota-database:read-write-rotas`
- tier: expected · severity: low/high · verb: `alter`
- recorded note: Reaching the store and modifying it is a distinct claim from reading it; the pair is deliberately split across lanes.

> mark:

**7.** An attacker positioned between a manager's device and the web app modifies rota changes in flight, because whether the traffic is encrypted is unverified.

- cites: `flow:store-manager-to-scheduling-web-app:build-rota`
- tier: expected · severity: low/medium · verb: `alter-in-transit`
- recorded note: Encryption in transit is stated as undocumented, not as absent; an analyst asserting there is no TLS here is unsupported.

> mark:


### repudiation

**8.** A store manager denies having made a rota change that disadvantaged a colleague, and nothing in the model records who changed what.

- cites: `process:scheduling-service`, `flow:store-manager-to-scheduling-web-app:build-rota`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: No log or audit store appears anywhere in the source. Absence of a logging element is a legitimate repudiation finding; absence of a stated control is not.

> mark:

**9.** Nobody can establish who collected a given payroll export from the file share, because no record of collections exists in the model.

- cites: `flow:payroll-system-to-file-share:collect-payroll-export`, `store:file-share`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: Pairs with the spoofing claim on the same flow: one is getting in as payroll, this one is that nothing afterwards distinguishes them.

> mark:


### information-disclosure

**10.** An attacker reads colleague names, contact details and availability from the rota database, because whether it is encrypted at rest is unverified.

- cites: `store:rota-database`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: The source states what the data is but never classifies it. The pii tag comes from the content, and data_classification stays unknown; a case that conflates the two is exactly what this case is here to catch.

> mark:

**11.** An attacker with access to the file share reads a whole store's payroll export in one file.

- cites: `store:file-share`, `flow:scheduling-service-to-file-share:write-payroll-export`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: Aggregation is the point: the export concentrates in one artifact what the database holds per colleague.

> mark:

**12.** An attacker on the network path between a colleague's phone and the web app reads that colleague's shifts and details in transit.

- cites: `flow:colleague-to-scheduling-web-app:view-shifts`
- tier: expected · severity: low/medium · verb: `intercept`
- recorded note: The one flow that leaves the internal network on the colleague side, with encryption explicitly undocumented.

> mark:


### denial-of-service

**13.** An attacker floods the scheduling web app so managers cannot build rotas and colleagues cannot see their shifts.

- cites: `process:scheduling-web-app`
- tier: expected · severity: medium/medium · verb: `flood`
- recorded note: The web app is the one process the model infers to be reachable from outside, and that inference is recorded as an assumption rather than asserted.

> mark:

**14.** An attacker prevents the weekly export from reaching the file share, so a pay run happens with no hours for a store.

- cites: `store:file-share`, `process:scheduling-service`
- tier: must-find · severity: low/high · verb: `disable`
- recorded note: A weekly batch with a deadline behind it fails differently from a request path: the damage is a missed pay run, not slow pages.

> mark:


### elevation-of-privilege

**15.** A colleague uses the shared app to build or change a rota as though they were a store manager, because the separation between the two roles is unverified.

- cites: `process:scheduling-web-app`, `flow:colleague-to-scheduling-web-app:view-shifts`
- tier: must-find · severity: medium/high · verb: `abuse-grant`
- recorded note: Both roles are stated to use the same app, and no authorization rule between them is stated anywhere. This is the finding that follows from the shared-app fact rather than from any missing control.

> mark:

**16.** An attacker on the internal network calls the scheduling service directly and performs rota writes the web app would not have allowed.

- cites: `process:scheduling-service`, `flow:scheduling-web-app-to-scheduling-service:rota-requests`
- tier: expected · severity: low/high · verb: `escalate`
- recorded note: The service is where writes actually happen, which the source says outright; whether it re-checks anything the app checked is undocumented.

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

**Then record the sitting.** Save this filled document as
`REVIEW-<the submitting GitHub login>.md` beside the original — the filled copy
is the evidence, and the generated `REVIEW.md` stays derived and unfilled.
Append this entry to `reviews` in `evals/corpus/11-sparse-shift-scheduling/case.json`, which is
what `tests/test_case_review.py` reads:

```json
  "reviews": [
    {
      "submitted_by": "<the GitHub login opening the PR>",
      "submitted_for": "<who read the case: a login, or the word anonymous>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "2507fd3081003c1c94427ef81dcea36f6ca92f5358c965789b49ec4af89b6a60"},
        {"file": "model.json", "sha256": "8a84e2d38125ddfd3c82971019a4d35d95f92de3689c6743cc3a449195a42420"},
        {"file": "claims/asvs.json", "sha256": "70682bbd369bfa31b62dc3127d10f33c5636106d80a4f3ff997987fab3c2d58d"},
        {"file": "claims/stride.json", "sha256": "b56e600389930164b345d4859160bc6fd77bc59df466b5ecf0af5e22bb8d67b0"}
      ],
      "document": "REVIEW-<the submitting GitHub login>.md",
      "notes": "<counts, and anything you changed>"
    }
  ],
```

**Two names, because they answer two questions.** `submitted_by` is the account
that opens the pull request and answers for the sitting. `submitted_for` is who
read the case: the same login where you read it yourself, another login, or
`anonymous` where the reader takes part on no name of their own. Only
`submitted_by` needs a roster line, and only `submitted_by` names the document.

The digests above are the files as they were when this document was
generated. If the sitting changed a file — a claim edit is a normal outcome —
recompute that file's digest (`sha256sum <file>`) before you commit: the
entry signs the bytes that merge.

If this case is named in `UNREVIEWED` in `tests/test_case_review.py`, delete
its line. That list names the cases nobody has read, so it is only accurate
while a reviewed case comes off it. A case not named there is new, and merges
with this entry from the start.

`tests/test_case_review.py` checks that `read` covers every framework the
case declares, that every digest matches, that the `document` file exists,
and that `submitted_by` has a line in `evals/review/voters.toml` — a first-time
contributor adds their own, standing `contributor`. `submitted_for` needs no
roster line, because it grants nothing. Then
`python -m evals.harness.run submit sitting` opens the PR.
