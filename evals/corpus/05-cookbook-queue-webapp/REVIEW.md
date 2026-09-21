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
| process:background-worker-process | internal | non-web | boundary:backend-tier | unknown |

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
| flow:entity:browser>process:web-application>page-request | entity:browser | process:web-application | HTTP/S | unknown | encrypted (marked as the one encrypted link) |
| flow:process:web-application>store:message-queue>enqueue-job | process:web-application | store:message-queue | unknown | unknown | unknown |
| flow:process:background-worker-process>store:message-queue>consume-job | process:background-worker-process | store:message-queue | unknown | unknown | unknown |
| flow:process:background-worker-process>store:database>read-write-records | process:background-worker-process | store:database | unknown | unknown | unknown |
| flow:process:web-application>store:web-application-config>read-configuration | process:web-application | store:web-application-config | unknown | unknown | unknown |
| flow:process:background-worker-process>store:worker-config>read-configuration | process:background-worker-process | store:worker-config | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:web-tier | network |
| boundary:backend-tier | network |

**Assumptions**

- `process:web-application` — The web application is reachable from the public internet. (basis: The browser is stated to reach it across the public internet.)
- `process:background-worker-process` — background worker process's exposure is internal, which the schema requires and no source states. (basis: Behind the web tier supplies architectural evidence beyond a network name. It still does not prove that every worker interface is inaccessible from the internet.)
- `store:web-application-config` — web application config's trust_zone is boundary:web-tier, which the schema requires and no source states. (basis: The application's use of a config store does not establish that store's network location. A remote configuration service is also possible.)
- `store:worker-config` — worker config's trust_zone is boundary:backend-tier, which the schema requires and no source states. (basis: The worker's use of a config store does not establish backend-tier membership.)

**Reviewed aliases** — other names a reader ruled identify the same element, each with the words in the source that support it. An extraction using one is named differently, not wrong.

- `entity:browser — user's browser` — The source's own words for the browser. Ruled by the maintainer on 2026-09-16 (#961 step 6). Source: > A user's browser talks to the web application
- `process:background-worker-process — background worker` — The source drops the word 'process' on second mention; one worker. Ruled by the maintainer on 2026-09-16 (#961 step 6). Source: > The background worker reads and writes the database.

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

- `entity:browser`, `process:web-application`, `flow:entity:browser>process:web-application>page-request`
- tech:browser-frontend fires; the chapter applies and every header is unstated. The header is emitted by the application or by the layer in front of it, so either route settles it.

> mark:

**A2.** `V3.3.1` — Nothing states whether the web application sets any cookie or with which attributes.

- `process:web-application`, `flow:entity:browser>process:web-application>page-request`
- A browser client is stated; the cookie question is open rather than answered. The attribute is set by whatever emits Set-Cookie, which is the application or the layer in front of it, so either route settles it.

> mark:


### configuration

**A3.** `V13.3.1` — Both configuration stores hold the credentials their process needs, and nothing says whether a secrets management solution holds or injects them.

- `store:web-application-config`, `store:worker-config`
- The source says the stores hold credentials. Where those credentials come from is a deployment fact the description does not carry.

> mark:


### secure-coding-and-architecture

**A4.** `V15.2.2` — Nothing states what bounds the work a queued job can consume.

- `process:background-worker-process`, `store:message-queue`, `flow:process:background-worker-process>store:message-queue>consume-job`
- A queue with an internal worker raises the availability requirement; the input never reaches it.

> mark:


### webrtc

**A5.** `V17.2.1` — No WebRTC media path exists in this system, so this chapter does not apply.

- 
- The exclusion the standard invites by name. The flows state HTTP/S and unknown, and no element is a media server.

> mark:


### secure-communication

**A6.** `V12.3.3` — Every link except the browser one states no transport protection at all.

- `process:web-application`, `store:message-queue`, `flow:process:web-application>store:message-queue>enqueue-job`
- The submitter marks one link as the only encrypted one, which settles the others.

> mark:


### authentication

**A7.** `V6.1.1` — Nothing states how the web application authenticates anybody.

- `process:web-application`
- authentication is unknown on the browser flow; the chapter applies and stays open.

> mark:

## Part 3 — the 13 recorded STRIDE threats

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

**1.** An attacker interacts with the web application as a legitimate user, because how the application authenticates the browser is unverified.

- `flow:entity:browser>process:web-application>page-request`, `entity:browser`
- severity: medium/medium · verb: `impersonate`
- Authentication on the one internet-crossing flow is unknown; needs-info is an acceptable verdict, silence is not.

> mark:

**2.** An attacker who reaches the queue enqueues jobs as if they came from the web application, since queue authentication is unverified.

- `flow:process:web-application>store:message-queue>enqueue-job`, `store:message-queue`
- severity: medium/high · verb: `impersonate`
- The queue is the trust hand-off in this design; nothing states how a producer is identified.

> mark:

**3.** An attacker holding the worker's database credentials connects to the database as the worker.

- `flow:process:background-worker-process>store:database>read-write-records`
- severity: medium/high · verb: `use-credential`
- Credentials are stated to exist in the worker config; their protection is not.

> mark:


### tampering

**4.** An attacker who can write to the web application config changes the queue endpoint or credentials and redirects the application's work.

- `store:web-application-config`, `process:web-application`
- severity: low/high · verb: `alter`
- Config stores are control planes; protection on this one is entirely unstated.

> mark:

**5.** An attacker with the worker's database access alters application records or the log records stored alongside them.

- `store:database`, `flow:process:background-worker-process>store:database>read-write-records`
- severity: medium/high · verb: `alter`
- Records and their own audit log share one store — tampering with one covers the other.

> mark:


### repudiation

**6.** The origin of a processed job cannot be established from what is described, because the input states nothing that records which producer enqueued it.

- `store:message-queue`, `flow:process:web-application>store:message-queue>enqueue-job`
- severity: medium/medium · verb: `unattributable`
- Decoupling removes the request context that would otherwise attribute the work. A producer identifier inside the message would settle it, and the source does not say whether one is carried.

> mark:


### information-disclosure

**7.** An attacker who compromises the background worker reads the database credentials from its config store.

- `store:worker-config`
- severity: medium/high · verb: `recover-credential`
- This is the threat the original cookbook model records against this element; protection at rest is unstated.

> mark:

**8.** An attacker who compromises the internet-facing web application reads the queue credentials from its config store.

- `store:web-application-config`
- severity: medium/high · verb: `recover-credential`
- Same shape as the worker finding, but reachable from the internet-facing tier, so likelier.

> mark:

**9.** An attacker who reaches the database storage reads application and log records, whose protection at rest is unverified.

- `store:database`
- severity: medium/medium · verb: `read`
- Data classification is unknown here, so impact cannot be rated higher than medium on the facts given.

> mark:

**10.** An attacker on the internal network reads job contents in transit, because transport encryption between the tiers is unverified.

- `flow:process:web-application>store:message-queue>enqueue-job`, `flow:process:background-worker-process>store:message-queue>consume-job`
- severity: medium/medium · verb: `intercept`
- Only the browser link is marked encrypted; the rest is explicitly silent.

> mark:


### denial-of-service

**11.** An attacker floods the queue with jobs until the worker cannot keep up and queued work stops completing.

- `store:message-queue`, `process:background-worker-process`
- severity: medium/medium · verb: `flood`
- A single worker behind an unbounded queue; the backlog is invisible to the user who submitted the work.

> mark:

**12.** An attacker floods the internet-facing web application until it stops serving browsers.

- `process:web-application`, `flow:entity:browser>process:web-application>page-request`
- severity: medium/medium · verb: `flood`
- Generic but grounded: it is the only internet-facing element.

> mark:


### repudiation

**13.** An attacker holding the worker's database access alters existing log records, if the worker's permissions extend to modifying them, and the record of what was done no longer shows it.

- `process:background-worker-process`, `store:database`, `flow:process:background-worker-process>store:database>read-write-records`
- severity: low/medium · verb: `alter`
- Drafted from Baseline 6bff717-gpt-5.6-terra-24dda4db draft R-01, ruled a relevant threat scenario by the maintainer on 2026-09-20 (audit QA-2026-09-20-01). Conditional. The worker writes to the database that holds the application's log records; where its permissions include modifying existing rows, rewriting evidence enables repudiation just as deletion does. It is a distinct mechanism from reference 6, which it does not satisfy.

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
    "05-cookbook-queue-webapp": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "20b0aa82c922766db2353cade33f7a26b38c60a3c7061244ef4686b7a647778b",
      "model.json": "bf3cfe67eb9ecbc49237bb6cf388bf664e1de8b54b444c8c004cdf87de5fdc46",
      "claims/asvs.json": "9e4ee6be326673ba2101ed60662718b0d50ecaf6aba7218bcec702c785b033c5",
      "claims/stride.json": "6ace713ac19d8068ad13fdee59320e39c193e2c1af56cc45c5334437ba29e123"
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
