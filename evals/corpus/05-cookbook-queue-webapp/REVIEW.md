# Review sitting — is `05-cookbook-queue-webapp`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/05-cookbook-queue-webapp`.

**Web application with queue-decoupled background worker** — domain `web-app-and-queue`.

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

> Web application with a queue-decoupled background process.
>
> A user's browser talks to the web application over HTTP/S across the public
> internet. The browser sits outside our network; the web application runs in our
> web tier.
>
> The web application does not do the heavy work itself. It puts jobs onto a
> message queue, and a background worker process picks them up and does the work.
> The queue, the worker and the database are all in the backend tier, behind the
> web tier.
>
> The background worker reads and writes the database. The database is also where
> we keep the application's log records.
>
> Both processes read their settings from a config store: the web application has
> a web application config, and the worker has a worker config. Both of those
> config stores hold the credentials the process needs — the web application's
> queue credentials, and the worker's database credentials.
>
> The diagram does not say anything about how the web application authenticates
> to the queue, how the worker authenticates to the database, or whether anything
> is encrypted at rest. The browser-to-application traffic is the one link marked
> as encrypted.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:browser | external-system | boundary:public-internet |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:web-application | internet-facing | web | boundary:web-tier | unknown |
| process:background-worker | internal | non-web | boundary:backend-tier | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:message-queue | boundary:backend-tier | unknown | unknown |
| store:database | boundary:backend-tier | unknown | unknown |
| store:web-application-config | boundary:web-tier | unknown | unknown |
| store:worker-config | boundary:backend-tier | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:browser-to-web-application:page-request | entity:browser | process:web-application | HTTP/S | unknown | encrypted (marked as the one encrypted link) |
| flow:web-application-to-message-queue:enqueue-job | process:web-application | store:message-queue | unknown | unknown | unknown |
| flow:background-worker-to-message-queue:consume-job | process:background-worker | store:message-queue | unknown | unknown | unknown |
| flow:background-worker-to-database:read-write-records | process:background-worker | store:database | unknown | unknown | unknown |
| flow:web-application-to-web-application-config:read-configuration | process:web-application | store:web-application-config | unknown | unknown | unknown |
| flow:background-worker-to-worker-config:read-configuration | process:background-worker | store:worker-config | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:web-tier | network |
| boundary:backend-tier | network |

**Assumptions**

- `process:web-application` — The web application is reachable from the public internet. (basis: The browser is stated to reach it across the public internet.)

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


### web-frontend-security

**A1.** `V3.4.1` — No security response header is described for the one encrypted browser-facing link.

- cites: `entity:browser`, `process:web-application`, `flow:browser-to-web-application:page-request`
- tier: must-find
- recorded note: tech:browser-frontend fires; the chapter applies and every header is unstated.

> mark:

**A2.** `V3.3.1` — Nothing states whether the web application sets any cookie or with which attributes.

- cites: `process:web-application`, `flow:browser-to-web-application:page-request`
- tier: expected
- recorded note: A browser client is stated; the cookie question is open rather than answered.

> mark:


### configuration

**A3.** `V13.3.1` — Two configuration stores are named and nothing says whether either holds a secret or how.

- cites: `store:web-application-config`, `store:worker-config`
- tier: must-find
- recorded note: Both stores carry unknown technology and classification.

> mark:


### secure-coding-and-architecture

**A4.** `V15.2.2` — Nothing states what bounds the work a queued job can consume.

- cites: `process:background-worker`, `store:message-queue`, `flow:background-worker-to-message-queue:consume-job`
- tier: must-find
- recorded note: A queue with an internal worker raises the availability requirement; the input never reaches it.

> mark:


### webrtc

**A5.** `V17.2.1` — No WebRTC media path exists in this system, so this chapter does not apply.

- cites: 
- tier: must-find
- recorded note: The exclusion the standard invites by name. The flows state HTTP/S and unknown, and no element is a media server.

> mark:


### secure-communication

**A6.** `V12.3.3` — Every link except the browser one states no transport protection at all.

- cites: `process:web-application`, `store:message-queue`, `flow:web-application-to-message-queue:enqueue-job`
- tier: expected
- recorded note: The submitter marks one link as the only encrypted one, which settles the others.

> mark:


### authentication

**A7.** `V6.1.1` — Nothing states how the web application authenticates anybody.

- cites: `process:web-application`
- tier: expected
- recorded note: authentication is unknown on the browser flow; the chapter applies and stays open.

> mark:

## Part 3 — the 17 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `reject` — overstated, unsupported by the text, or not really a finding here.
- `duplicate` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker interacts with the web application as a legitimate user, because how the application authenticates the browser is unverified.

- cites: `flow:browser-to-web-application:page-request`, `entity:browser`
- tier: must-find · severity: medium/medium · verb: `impersonate`
- recorded note: Authentication on the one internet-crossing flow is unknown; needs-info is an acceptable verdict, silence is not.

> mark:

**2.** An attacker who reaches the queue enqueues jobs as if they came from the web application, since queue authentication is unverified.

- cites: `flow:web-application-to-message-queue:enqueue-job`, `store:message-queue`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: The queue is the trust hand-off in this design; nothing states how a producer is identified.

> mark:

**3.** An attacker holding the worker's database credentials connects to the database as the worker.

- cites: `flow:background-worker-to-database:read-write-records`
- tier: expected · severity: medium/high · verb: `use-credential`
- recorded note: Credentials are stated to exist in the worker config; their protection is not.

> mark:


### tampering

**4.** An attacker places a poisoned message on the queue and the worker processes it as legitimate work.

- cites: `store:message-queue`, `process:background-worker`
- tier: must-find · severity: medium/high · verb: `plant`
- recorded note: The canonical queue-decoupling threat: the worker's input is only as trustworthy as write access to the queue.

> mark:

**5.** An attacker who can write to the web application config changes the queue endpoint or credentials and redirects the application's work.

- cites: `store:web-application-config`, `process:web-application`
- tier: must-find · severity: low/high · verb: `alter`
- recorded note: Config stores are control planes; protection on this one is entirely unstated.

> mark:

**6.** An attacker with the worker's database access alters application records or the log records stored alongside them.

- cites: `store:database`, `flow:background-worker-to-database:read-write-records`
- tier: expected · severity: medium/high · verb: `alter`
- recorded note: Records and their own audit log share one store — tampering with one covers the other.

> mark:


### repudiation

**7.** An attacker who compromises the worker erases or edits the log records that would show what it did, because the logs live in the same database the worker writes.

- cites: `store:database`, `process:background-worker`
- tier: must-find · severity: medium/high · verb: `delete`
- recorded note: The strongest finding available from the diagram: no separation between the audit record and the audited actor.

> mark:

**8.** The origin of a processed job cannot be established, because nothing records which producer enqueued it.

- cites: `store:message-queue`, `flow:web-application-to-message-queue:enqueue-job`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: Decoupling removes the request context that would otherwise attribute the work.

> mark:


### information-disclosure

**9.** An attacker who compromises the background worker reads the database credentials from its config store.

- cites: `store:worker-config`
- tier: must-find · severity: medium/high · verb: `recover-credential`
- recorded note: This is the threat the original cookbook model records against this element; protection at rest is unstated.

> mark:

**10.** An attacker who compromises the internet-facing web application reads the queue credentials from its config store.

- cites: `store:web-application-config`
- tier: must-find · severity: medium/high · verb: `recover-credential`
- recorded note: Same shape as the worker finding, but reachable from the internet-facing tier, so likelier.

> mark:

**11.** An attacker who reaches the database storage reads application and log records, whose protection at rest is unverified.

- cites: `store:database`
- tier: expected · severity: medium/medium · verb: `read`
- recorded note: Data classification is unknown here, so impact cannot be rated higher than medium on the facts given.

> mark:

**12.** An attacker on the internal network reads job contents in transit, because transport encryption between the tiers is unverified.

- cites: `flow:web-application-to-message-queue:enqueue-job`, `flow:background-worker-to-message-queue:consume-job`
- tier: expected · severity: medium/medium · verb: `intercept`
- recorded note: Only the browser link is marked encrypted; the rest is explicitly silent.

> mark:


### denial-of-service

**13.** An attacker floods the queue with jobs until the worker cannot keep up and queued work stops completing.

- cites: `store:message-queue`, `process:background-worker`
- tier: must-find · severity: medium/medium · verb: `flood`
- recorded note: A single worker behind an unbounded queue; the backlog is invisible to the user who submitted the work.

> mark:

**14.** An attacker floods the internet-facing web application until it stops serving browsers.

- cites: `process:web-application`, `flow:browser-to-web-application:page-request`
- tier: expected · severity: medium/medium · verb: `flood`
- recorded note: Generic but grounded: it is the only internet-facing element.

> mark:

**15.** An attacker submits work that makes the worker exhaust database capacity, stalling both job processing and logging.

- cites: `store:database`, `process:background-worker`
- tier: expected · severity: low/medium · verb: `flood`
- recorded note: Shared store means one saturation affects the audit trail too.

> mark:


### elevation-of-privilege

**16.** An attacker who compromises the internet-facing web application uses its queue credentials to reach the backend tier.

- cites: `process:web-application`, `store:message-queue`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The queue is the only path across the tier boundary, so it is the escalation route by construction.

> mark:

**17.** An attacker who gets code execution in the worker inherits whatever database privilege its credentials carry, which is unverified and may be unrestricted.

- cites: `process:background-worker`, `store:database`
- tier: must-find · severity: medium/high · verb: `abuse-grant`
- recorded note: Job content is attacker-influenceable via the queue, so worker execution is a realistic starting point.

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
Append this entry to `reviews` in `evals/corpus/05-cookbook-queue-webapp/case.json`, which is
what `tests/test_case_review.py` reads:

```json
  "reviews": [
    {
      "submitted_by": "<the GitHub login opening the PR>",
      "submitted_for": "<who read the case: a login, or the word anonymous>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "20b0aa82c922766db2353cade33f7a26b38c60a3c7061244ef4686b7a647778b"},
        {"file": "model.json", "sha256": "68fe8bcf41cbbc60cac575dee317700e76db8101fec46da8f3ac89598f4a75df"},
        {"file": "claims/asvs.json", "sha256": "067e81430d13c11e7d4191add2bd23113e27e282618b1c084ea09115ff6dd7c0"},
        {"file": "claims/stride.json", "sha256": "18ff482a563387a6a4815393dfc0a44ec56f1e2df224d0ae6bce940ba48f29ef"}
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
