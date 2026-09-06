# Review sitting — is `03-batch-data-pipeline`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/03-batch-data-pipeline`.

**Nightly partner claims ingest pipeline** — domain `batch-data`.

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

> Nightly partner data pipeline.
>
> Three insurance partners drop a daily extract for us. They push files into a
> landing bucket over SFTP. Each partner has a static key we issued when they
> onboarded; the keys have not changed since. The extracts contain claim records
> with member names and dates of birth.
>
> An Airflow scheduler running in the landing network wakes up at 02:00, lists
> the bucket, and reads whatever is there. It does not check that a file came
> from the partner whose folder it landed in. Airflow keeps its connection
> strings and the partner keys in its own metadata database.
>
> For each file the scheduler triggers a Spark transform job in the warehouse
> network. The transform normalizes the records and loads them into BigQuery.
> Nothing validates the row contents beyond the schema.
>
> Analysts query the warehouse directly. They are on the warehouse network and
> authenticate with SSO, but the grant is dataset-wide — we have not split
> member-identifying columns out.
>
> I don't know how the landing bucket is encrypted or whether the Airflow
> metadata database is encrypted at rest. Both are on defaults as far as I know.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:insurance-partner | external-system | boundary:partner-network |
| entity:data-analyst | human | boundary:warehouse-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:ingest-scheduler | internal | non-web | boundary:landing-network | Airflow |
| process:transform-job | internal | non-web | boundary:warehouse-network | Spark |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:landing-bucket | boundary:landing-network | unknown | confidential |
| store:airflow-metadata-db | boundary:landing-network | unknown | confidential |
| store:claims-warehouse | boundary:warehouse-network | unknown | confidential |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:insurance-partner-to-landing-bucket:push-daily-extract | entity:insurance-partner | store:landing-bucket | SFTP | static per-partner key issued at onboarding, never rotated | SSH transport (SFTP) |
| flow:ingest-scheduler-to-landing-bucket:list-and-read-files | process:ingest-scheduler | store:landing-bucket | object storage API | unknown | unknown |
| flow:ingest-scheduler-to-airflow-metadata-db:read-connections | process:ingest-scheduler | store:airflow-metadata-db | PostgreSQL wire protocol | unknown | unknown |
| flow:ingest-scheduler-to-transform-job:trigger-transform | process:ingest-scheduler | process:transform-job | unknown | unknown | unknown |
| flow:transform-job-to-claims-warehouse:load-records | process:transform-job | store:claims-warehouse | BigQuery API | unknown | unknown |
| flow:data-analyst-to-claims-warehouse:run-queries | entity:data-analyst | store:claims-warehouse | BigQuery API | company SSO; dataset-wide grant with no column-level restriction | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:partner-network | network |
| boundary:landing-network | network |
| boundary:warehouse-network | network |

**Assumptions**

- `flow:insurance-partner-to-landing-bucket:push-daily-extract` — SFTP traffic from partners is protected by the SSH transport it runs over. (basis: The text names SFTP, whose transport encryption is intrinsic to the protocol; no other transport claim is made.)
- `store:claims-warehouse` — The claims data is health-related personal data. (basis: Described as insurance claim records carrying member names and dates of birth.)

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

## Part 2 — the 17 recorded STRIDE threats

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

**1.** An attacker who obtains a partner's never-rotated static SFTP key uploads extracts as that partner.

- cites: `flow:insurance-partner-to-landing-bucket:push-daily-extract`, `entity:insurance-partner`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: Long-lived shared secret held by a third party; the primary identity weakness on the ingest path.

> mark:

**2.** An attacker deposits a file into another partner's folder and the scheduler processes it as that partner's extract, because it never checks the depositor.

- cites: `process:ingest-scheduler`, `store:landing-bucket`
- tier: must-find · severity: medium/high · verb: `plant`
- recorded note: Stated absence, not an unknown: the source says the check does not happen. Attribution of data to a source is the whole point of the folder layout.

> mark:

**3.** An attacker submits a transform run impersonating the scheduler, since authentication on the trigger path is unverified.

- cites: `flow:ingest-scheduler-to-transform-job:trigger-transform`
- tier: expected · severity: low/medium · verb: `impersonate`
- recorded note: Crosses landing into warehouse with unknown authentication.

> mark:


### tampering

**4.** An attacker who can write to the landing bucket alters claim records before the nightly run, and only the schema is checked before they reach the warehouse.

- cites: `store:landing-bucket`, `process:transform-job`
- tier: must-find · severity: medium/high · verb: `alter`
- recorded note: Content validation is stated as absent; the case's headline integrity finding.

> mark:

**5.** An attacker who can write to the Airflow metadata database rewrites a connection string to redirect the pipeline to infrastructure they control.

- cites: `store:airflow-metadata-db`, `flow:ingest-scheduler-to-airflow-metadata-db:read-connections`
- tier: expected · severity: low/high · verb: `alter`
- recorded note: The metadata database is a control plane, not just a data store — worth its own finding.

> mark:

**6.** An attacker who compromises the transform job writes fabricated claim rows into the warehouse alongside genuine ones.

- cites: `store:claims-warehouse`, `flow:transform-job-to-claims-warehouse:load-records`
- tier: expected · severity: low/high · verb: `forge`
- recorded note: The load path has unverified authentication and no downstream reconciliation.

> mark:


### repudiation

**7.** A partner denies having sent an extract and nothing binds a landed file to the key that uploaded it.

- cites: `store:landing-bucket`, `entity:insurance-partner`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: Direct consequence of the unchecked depositor: provenance exists only as folder convention.

> mark:

**8.** A dispute over a warehouse row cannot be traced back to the extract it came from, because no lineage from file to loaded record is described.

- cites: `store:claims-warehouse`, `process:transform-job`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: Batch pipelines lose attribution at the normalization step unless it is deliberately carried.

> mark:


### information-disclosure

**9.** An analyst who needs only aggregate figures reads member names and dates of birth, because the grant covers the whole dataset.

- cites: `store:claims-warehouse`, `flow:data-analyst-to-claims-warehouse:run-queries`
- tier: must-find · severity: high/high · verb: `abuse-grant`
- recorded note: Stated absence of column-level restriction over health data; the highest-likelihood disclosure in the model.

> mark:

**10.** An attacker who reaches the Airflow metadata database recovers every partner key and connection string it holds, since its protection at rest is unverified.

- cites: `store:airflow-metadata-db`
- tier: must-find · severity: medium/high · verb: `recover-credential`
- recorded note: Single store concentrating credentials for the entire ingest path.

> mark:

**11.** An attacker who reaches the landing bucket's storage reads raw partner extracts, whose protection at rest is unverified.

- cites: `store:landing-bucket`
- tier: expected · severity: medium/high · verb: `read`
- recorded note: The source explicitly flags this as unknown and on defaults; needs-info is an acceptable verdict.

> mark:

**12.** An attacker observing the load path reads claim records in transit, because transport encryption on it is unverified.

- cites: `flow:transform-job-to-claims-warehouse:load-records`
- tier: expected · severity: low/high · verb: `intercept`
- recorded note: Intra-zone, so lower likelihood than the crossing flows.

> mark:


### denial-of-service

**13.** An attacker deposits an enormous or malformed file that consumes the nightly window and prevents genuine extracts from being processed.

- cites: `store:landing-bucket`, `process:ingest-scheduler`
- tier: must-find · severity: medium/medium · verb: `flood`
- recorded note: The batch shape is the vulnerability: a missed window is a lost day, not a slow request.

> mark:

**14.** An attacker crafts input that makes the transform job fail repeatedly, leaving the warehouse stale without any request-level error surfacing.

- cites: `process:transform-job`, `store:claims-warehouse`
- tier: expected · severity: medium/medium · verb: `disable`
- recorded note: Silent staleness rather than visible downtime — the failure mode analysts under-report on pipelines.

> mark:


### elevation-of-privilege

**15.** An attacker with a foothold in the landing network reads the metadata database and escalates to every credential the pipeline holds.

- cites: `store:airflow-metadata-db`, `process:ingest-scheduler`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: Escalation framing of the credential concentration: one foothold to all downstream systems.

> mark:

**16.** An attacker who can plant a file in the landing bucket gains execution in the warehouse network through the job it triggers.

- cites: `process:transform-job`, `flow:ingest-scheduler-to-transform-job:trigger-transform`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: Data crossing into a compute zone that acts on it is the boundary crossing that matters here.

> mark:

**17.** An analyst uses their dataset-wide grant to reach claim data belonging to partners outside their remit.

- cites: `entity:data-analyst`, `store:claims-warehouse`
- tier: expected · severity: medium/medium · verb: `abuse-grant`
- recorded note: Authorization scope, distinct from the disclosure entry about which columns are readable.

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
Append this entry to `reviews` in `evals/corpus/03-batch-data-pipeline/case.json`, which is
what `tests/test_case_review.py` reads:

```json
  "reviews": [
    {
      "submitted_by": "<the GitHub login opening the PR>",
      "submitted_for": "<who read the case: a login, or the word anonymous>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "df7757178c394258cbcf1643e81fca5b01f324058a0841824f008e74346da2d0"},
        {"file": "model.json", "sha256": "37d3e8f3229ce0fe2d196968aa8b3bf66bbe01e5aeb6dae8b52b92c8b8a5ecfb"},
        {"file": "claims/stride.json", "sha256": "0cff7f5fd30438bc4a6152acea87bf84fbaf5404252d9c19caeebb2b67f2e12d"}
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
