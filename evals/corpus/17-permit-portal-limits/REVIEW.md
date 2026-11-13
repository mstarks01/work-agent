# Review sitting — is `17-permit-portal-limits`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/17-permit-portal-limits`.

**A city building-permit portal whose parts state different request limits** — domain `municipal permits`.

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

> Building permit portal — notes for the security review.
>
> Applicants (homeowners, builders, architects) apply for building permits on the portal from anywhere. Every application request goes through the permit gateway, which faces the internet. The gateway allows 60 requests a minute from one IP address and rejects any request body over 5 MB. It passes each request on to the permit service.
>
> The permit service keeps applications, their status and the inspectors' decisions in the permit database. Marta thinks the permit service limits how many open applications one account can have, but nobody has checked that.
>
> Plan drawings do not go through the gateway, because they are too big. Applicants upload them straight to the plan upload service, which is also on the internet. The plan upload service has no size limit and no rate limit: architects send very large PDFs and we did not want to turn anyone away. It saves each file in the plan store and hands it to the plan renderer.
>
> The plan renderer converts each PDF into image tiles that inspectors can zoom into, and writes the tiles back to the plan store. It runs four conversions at a time; anything more waits.
>
> Inspectors review applications and record their decisions through the permit service from the city network. All inspectors in a district use one shared inspector login.
>
> The permit service, the plan upload service, the plan renderer, the permit database and the plan store run in the city's cloud account.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:applicant | human | boundary:internet |
| entity:inspector | human | boundary:city-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:permit-gateway | internet-facing | web | boundary:internet | unknown |
| process:permit-service | unknown | unknown | boundary:city-cloud-account | unknown |
| process:plan-upload-service | internet-facing | unknown | boundary:city-cloud-account | unknown |
| process:plan-renderer | unknown | unknown | boundary:city-cloud-account | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:permit-database | boundary:city-cloud-account | unknown | unknown |
| store:plan-store | boundary:city-cloud-account | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:applicant>process:permit-gateway>submit-applications | entity:applicant | process:permit-gateway | unknown | unknown | unknown |
| flow:process:permit-gateway>process:permit-service>forward-application-requests | process:permit-gateway | process:permit-service | unknown | unknown | unknown |
| flow:process:permit-service>store:permit-database>keep-applications-and-decisions | process:permit-service | store:permit-database | unknown | unknown | unknown |
| flow:entity:applicant>process:plan-upload-service>upload-plan-drawings | entity:applicant | process:plan-upload-service | unknown | unknown | unknown |
| flow:process:plan-upload-service>store:plan-store>save-plan-files | process:plan-upload-service | store:plan-store | unknown | unknown | unknown |
| flow:process:plan-upload-service>process:plan-renderer>hand-over-plan-files | process:plan-upload-service | process:plan-renderer | unknown | unknown | unknown |
| flow:process:plan-renderer>store:plan-store>write-image-tiles | process:plan-renderer | store:plan-store | unknown | unknown | unknown |
| flow:entity:inspector>process:permit-service>review-and-decide | entity:inspector | process:permit-service | unknown | one inspector login shared by all inspectors in a district | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:internet | network |
| boundary:city-cloud-account | network |
| boundary:city-network | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:permit-gateway` — Limits stated in the source: "The gateway allows 60 requests a minute from one IP address and rejects any request body over 5 MB." The source does not say where the gateway runs; the closing list of what runs in the city's cloud account does not name it.
- `process:permit-service` — A limit on open applications per account is hedged, not stated: "Marta thinks the permit service limits how many open applications one account can have, but nobody has checked that."
- `process:plan-upload-service` — Stated absence of limits: "The plan upload service has no size limit and no rate limit: architects send very large PDFs and we did not want to turn anyone away." Drawings do not pass the gateway: "Plan drawings do not go through the gateway, because they are too big."
- `process:plan-renderer` — Stated concurrency limit: "It runs four conversions at a time; anything more waits." The source does not say how inspectors reach the tiles.
- `flow:entity:inspector>process:permit-service>review-and-decide` — "All inspectors in a district use one shared inspector login." The source does not name the mechanism behind the login.

**Assumptions**

- `entity:applicant` — Applicants are placed on the internet because the schema requires a zone. (basis: They apply "from anywhere" and reach the gateway and the plan upload service over the internet; the source does not state where applicants are.)
- `process:permit-gateway` — The permit gateway is placed on the internet because the schema requires a zone. (basis: The source says the gateway faces the internet and does not name it among what runs in the city's cloud account.)
- `process:permit-gateway` — The permit gateway presents a web interface. (basis: Applicants apply on the portal, and every application request goes through the gateway. A portal is web, as prompts/extract.md rules.)

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

## Part 2 — the 11 recorded STRIDE threats

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

**1.** If the portal does not authenticate applicants, an attacker submits or changes permit applications as another applicant.

- `flow:entity:applicant>process:permit-gateway>submit-applications`, `entity:applicant`
- severity: medium/medium · verb: `impersonate`
- The source never says how an applicant signs in, so authentication is unknown and the finding is conditional.

> mark:

**2.** An attacker who learns a district's shared inspector login records permit decisions as any inspector in that district.

- `flow:entity:inspector>process:permit-service>review-and-decide`, `entity:inspector`
- severity: medium/high · verb: `use-credential`
- Stated: "All inspectors in a district use one shared inspector login." A login many people hold is a login many people can leak.

> mark:


### tampering

**3.** If the plan upload service does not check who owns an application, an attacker uploads drawings that replace another applicant's plans in the plan store.

- `flow:entity:applicant>process:plan-upload-service>upload-plan-drawings`, `store:plan-store`
- severity: medium/medium · verb: `alter`
- The upload path skips the gateway and the source states no authentication or ownership check on it.

> mark:

**4.** If application requests are not encrypted in transit, an attacker on the network path changes an application on its way to the gateway.

- `flow:entity:applicant>process:permit-gateway>submit-applications`
- severity: low/medium · verb: `alter-in-transit`
- The source says nothing about transport encryption on any flow.

> mark:


### repudiation

**5.** An inspector approves or refuses a permit under the district's shared login, and nothing shows which inspector made the decision.

- `flow:entity:inspector>process:permit-service>review-and-decide`, `entity:inspector`
- severity: high/medium · verb: `unattributable`
- Stated shared login. A permit decision is the record a dispute turns on.

> mark:


### information-disclosure

**6.** If the plan store does not restrict who reads it, an attacker reads other applicants' plan drawings.

- `store:plan-store`
- severity: medium/medium · verb: `read`
- The source states nothing about access to the plan store. Plan drawings show the layout of private homes.

> mark:

**7.** If uploads are not encrypted in transit, an attacker on the network path reads plan drawings as applicants upload them.

- `flow:entity:applicant>process:plan-upload-service>upload-plan-drawings`
- severity: low/medium · verb: `intercept`
- The source says nothing about transport encryption on the upload path.

> mark:


### denial-of-service

**8.** An attacker sends very large PDFs, or very many of them, straight to the plan upload service and fills it and the plan store until applicants cannot upload drawings.

- `process:plan-upload-service`, `flow:entity:applicant>process:plan-upload-service>upload-plan-drawings`
- severity: high/medium · verb: `flood`
- This case exists for this finding. The source states the absence of both limits: "The plan upload service has no size limit and no rate limit." The service is on the internet and outside the gateway, so the gateway's limits do not cover it. An assertion pass that writes the two stated absences gives the lane the facts this finding rests on.

> mark:

**9.** An attacker uploads many large drawings so that the plan renderer, which runs four conversions at a time, falls far behind and inspectors wait for tiles.

- `process:plan-renderer`, `flow:process:plan-upload-service>process:plan-renderer>hand-over-plan-files`
- severity: medium/low · verb: `flood`
- The concurrency limit is stated: "It runs four conversions at a time; anything more waits." The limit protects the renderer, and the queue in front of it has no stated bound.

> mark:

**10.** An attacker sends requests from many IP addresses at once, which the gateway's limit of 60 requests a minute per address does not stop, until the permit service stops serving applicants.

- `process:permit-gateway`, `flow:entity:applicant>process:permit-gateway>submit-applications`, `process:permit-service`
- severity: medium/medium · verb: `flood`
- The gateway's rate limit and size limit are stated. The rate limit is per IP address, so it does not bound a flood spread over many addresses.

> mark:


### elevation-of-privilege

**11.** An attacker uploads a crafted PDF that exploits the plan renderer's conversion and runs code with the renderer's access to the plan store and the city's cloud account.

- `process:plan-renderer`, `flow:process:plan-upload-service>process:plan-renderer>hand-over-plan-files`
- severity: low/high · verb: `escalate`
- The renderer parses files that anyone on the internet uploads. The source states nothing about how the conversion is isolated.

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
  "generated": "<YYYY-MM-DDTHHMMSSZ>",
  "cases": {
    "17-permit-portal-limits": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "8cddb3f778550ca2d33274d4a588eb18f966200eb5a903a07fda93446c2288f5",
      "model.json": "6d46ca38340a493a2672944db1c4cd8ef900dfdf18c6902f7374d62c77b4001a",
      "claims/stride.json": "5bb806e02f1337028b14aff281cadd59f3ddb563816e159a4917a25907881fc8"
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
