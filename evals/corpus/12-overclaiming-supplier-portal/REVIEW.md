# Review sitting — is `12-overclaiming-supplier-portal`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/12-overclaiming-supplier-portal`.

**Vendor-hosted supplier document portal described in security marketing language** — domain `supplier-management`.

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

> Supplier document portal.
>
> Suppliers upload their compliance paperwork through a portal — insurance
> certificates, food safety audits, and contact details for their own staff. The
> documents themselves stay in the vendor's platform. Our category managers, who
> work from the corporate network, review what has been uploaded and approve or
> reject it.
>
> The portal is a SaaS product. The vendor hosts it and we do not run any part of
> it. The vendor's datasheet says the platform is secure by design, that it uses
> enterprise-grade encryption throughout, that all access is fully authenticated
> and audited, and that the product is fully compliant.
>
> Suppliers sign in with a username and password that the vendor issues to them.
>
> Every night the vendor pushes a supplier data extract to a landing bucket in our
> cloud account. Our supplier master service loads that file and writes the
> records into the supplier database. The bucket, the service and the database are
> all in our cloud account.
>
> We were told the nightly extract is encrypted end to end. The runbook for the
> landing bucket says the file arrives as a plain CSV and is picked up as-is.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:supplier | human | boundary:public-internet |
| entity:category-manager | human | boundary:corporate-network |
| entity:portal-vendor | external-system | boundary:vendor-platform |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:supplier-portal | internet-facing | web | boundary:vendor-platform | unknown |
| process:supplier-master-service | internal | non-web | boundary:cloud-account | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:document-store | boundary:vendor-platform | unknown | unknown |
| store:landing-bucket | boundary:cloud-account | unknown | unknown |
| store:supplier-database | boundary:cloud-account | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:supplier-to-supplier-portal:upload-documents | entity:supplier | process:supplier-portal | unknown | username and password issued by the vendor | unknown |
| flow:category-manager-to-supplier-portal:review-documents | entity:category-manager | process:supplier-portal | unknown | unknown | unknown |
| flow:supplier-portal-to-document-store:store-documents | process:supplier-portal | store:document-store | unknown | unknown | unknown |
| flow:portal-vendor-to-landing-bucket:push-nightly-extract | entity:portal-vendor | store:landing-bucket | unknown | unknown | unknown |
| flow:supplier-master-service-to-landing-bucket:load-extract | process:supplier-master-service | store:landing-bucket | unknown | unknown | unknown |
| flow:supplier-master-service-to-supplier-database:write-supplier-records | process:supplier-master-service | store:supplier-database | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:corporate-network | network |
| boundary:vendor-platform | tenant |
| boundary:cloud-account | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:supplier-portal` — The datasheet's phrases — secure by design, enterprise-grade encryption throughout, fully authenticated and audited, fully compliant — name no technology and state no verifiable control, so none of them set an attribute here.
- `store:document-store` — Enterprise-grade encryption throughout is a vendor marketing claim about the platform, not a stated property of this store, so encryption_at_rest is unknown rather than encrypted.
- `flow:category-manager-to-supplier-portal:review-documents` — All access is fully authenticated and audited is a datasheet claim covering the platform generally; the source states how suppliers sign in and never states how category managers do, so this stays unknown.
- `flow:portal-vendor-to-landing-bucket:push-nightly-extract` — The source contradicts itself here: we were told the extract is encrypted end to end, and the landing bucket runbook says the file arrives as a plain CSV picked up as-is. Neither statement is privileged over the other and nothing in the source resolves them, so this is unknown rather than either encrypted or unencrypted, and the conflict is itself a finding.

**Assumptions**

- `process:supplier-portal` — Suppliers reach the portal across the public internet. (basis: The portal is a vendor-hosted SaaS product suppliers sign in to, and the source places suppliers nowhere on our networks.)

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

## Part 2 — the 10 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### encoding-and-sanitization

**A1.** `V1.2.4` — The supplier master service writes records from a vendor CSV into the supplier database and nothing says how those writes are built.

- cites: `process:supplier-master-service`, `store:supplier-database`, `flow:supplier-master-service-to-supplier-database:write-supplier-records`
- tier: must-find
- recorded note: The runbook states the file arrives as plain CSV and is picked up as-is, which is the fact that makes an untrusted-input path concrete.

> mark:


### file-handling

**A2.** `V5.2.1` — Suppliers upload compliance documents and nothing states a size the portal will accept.

- cites: `process:supplier-portal`, `store:document-store`
- tier: expected
- recorded note: Upload is stated; the limit is not.

> mark:

**A3.** `V5.2.2` — Suppliers upload insurance certificates and audit documents and nothing says the portal checks the file against an expected type.

- cites: `entity:supplier`, `process:supplier-portal`, `flow:supplier-to-supplier-portal:upload-documents`
- tier: must-find
- recorded note: An upload from outside the organization is the trigger. The vendor datasheet asserts the platform is secure by design, which is a claim rather than a stated control.

> mark:


### authentication

**A4.** `V6.1.1` — No documentation defines rate limiting or anti-automation on the supplier sign-in.

- cites: `process:supplier-portal`
- tier: expected
- recorded note: Documentation requirement. The datasheet's 'fully authenticated' is not a control the input states.

> mark:

**A5.** `V6.2.1` — Suppliers sign in with a password and no minimum length is stated.

- cites: `entity:supplier`, `process:supplier-portal`
- tier: expected
- recorded note: Password authentication is stated outright, so the chapter applies; the parameter is what nothing settles.

> mark:

**A6.** `V6.4.1` — The vendor issues each supplier a username and password, and nothing says the initial credential is randomly generated or expires.

- cites: `entity:supplier`, `entity:portal-vendor`, `process:supplier-portal`
- tier: must-find
- recorded note: A stated issued credential is exactly this requirement's subject, and the party issuing it is outside the organization.

> mark:


### authorization

**A7.** `V8.2.2` — Every supplier uploads into one platform and nothing restricts a supplier to its own documents.

- cites: `entity:supplier`, `process:supplier-portal`, `store:document-store`
- tier: must-find
- recorded note: The documents carry contact details for the suppliers' own staff, so data-specific access is the requirement. The datasheet asserts access is fully authenticated, which says nothing about which data a signed-in supplier reaches.

> mark:


### secure-communication

**A8.** `V12.2.1` — Suppliers upload to an externally hosted portal and no flow states its transport.

- cites: `entity:supplier`, `process:supplier-portal`, `flow:supplier-to-supplier-portal:upload-documents`
- tier: must-find
- recorded note: 'Enterprise-grade encryption throughout' is the vendor's claim about its own product, not a stated fact about this connection. The requirement applies and is unsettled.

> mark:


### secure-coding-and-architecture

**A9.** `V15.1.1` — The whole portal is a third-party product and no documentation defines remediation time frames for it.

- cites: `process:supplier-portal`, `entity:portal-vendor`
- tier: must-find
- recorded note: The source states the organization runs no part of the portal, which makes the third-party rule the central one for this case.

> mark:

**A10.** `V15.2.1` — Nothing says the portal's components sit inside any documented update window.

- cites: `process:supplier-portal`
- tier: expected
- recorded note: Follows V15.1.1: with no documented time frame there is nothing to hold the components to.

> mark:

## Part 3 — the 15 recorded STRIDE threats

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

**1.** An attacker who obtains a supplier's vendor-issued password signs in as that supplier and uploads paperwork in their name.

- cites: `flow:supplier-to-supplier-portal:upload-documents`, `entity:supplier`
- tier: must-find · severity: medium/medium · verb: `impersonate`
- recorded note: Sign-in is the one control the source actually states, so this claim rests on a stated fact rather than on an unknown. Whether a second factor exists is never stated, and asserting its absence would be unsupported.

> mark:

**2.** An attacker signs in to the portal as a category manager and approves supplier paperwork, because how category managers are authenticated is unverified.

- cites: `flow:category-manager-to-supplier-portal:review-documents`, `entity:category-manager`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: The datasheet's fully authenticated and audited covers this flow rhetorically and states nothing about it. An analyst that treats the manager path as authenticated because the vendor says so has swallowed the claim.

> mark:

**3.** An attacker writes a file into the landing bucket while posing as the vendor's nightly push, since how the bucket identifies the writer is unverified.

- cites: `flow:portal-vendor-to-landing-bucket:push-nightly-extract`, `store:landing-bucket`
- tier: must-find · severity: medium/high · verb: `plant`
- recorded note: The one flow crossing from the vendor platform into our cloud account, and the source states no authentication on it at all.

> mark:


### tampering

**4.** An attacker modifies the extract file in the landing bucket before the supplier master service loads it, and the altered supplier records reach the database.

- cites: `store:landing-bucket`, `flow:supplier-master-service-to-landing-bucket:load-extract`
- tier: must-find · severity: medium/high · verb: `alter`
- recorded note: The runbook states the file is picked up as-is, which is a stated absence of validation rather than an inferred one — the strongest grounded finding in the case.

> mark:

**5.** An attacker on the path of the nightly push alters the extract in transit, because the source contradicts itself over whether that transfer is encrypted.

- cites: `flow:portal-vendor-to-landing-bucket:push-nightly-extract`
- tier: must-find · severity: medium/high · verb: `alter-in-transit`
- recorded note: This is the case's central reference. The correct handling of we were told it is encrypted end to end against the runbook's plain CSV is needs-info on an unresolved conflict; both a confident encrypted and a confident unencrypted are wrong, and the latter is worse.

> mark:

**6.** An attacker with access inside the vendor platform alters a stored compliance document so a supplier appears certified when they are not.

- cites: `store:document-store`, `flow:supplier-portal-to-document-store:store-documents`
- tier: expected · severity: low/high · verb: `alter`
- recorded note: We run no part of the vendor platform, so every control inside it is unverifiable by us rather than absent. Secure by design is not a control.

> mark:


### repudiation

**7.** A category manager denies having approved a supplier's paperwork, and the model holds no audit record we control or can produce.

- cites: `flow:category-manager-to-supplier-portal:review-documents`, `process:supplier-portal`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: The datasheet says audited and the model has no audit store, because a marketing adjective is not an element. An analyst that cites an audit log here is citing something that does not exist in the model.

> mark:

**8.** The vendor disputes what a given nightly extract contained when it left their platform, and nothing on our side records what arrived.

- cites: `entity:portal-vendor`, `flow:portal-vendor-to-landing-bucket:push-nightly-extract`
- tier: expected · severity: low/medium · verb: `unattributable`
- recorded note: A dispute across an organizational boundary is the repudiation shape that matters here, and it is the boundary we have least visibility across.

> mark:


### information-disclosure

**9.** An attacker with access to the landing bucket reads the whole supplier extract, because whether the bucket is encrypted at rest is unverified.

- cites: `store:landing-bucket`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: Enterprise-grade encryption throughout is a claim about the vendor's platform; the bucket is in our cloud account and the source says nothing about it either way.

> mark:

**10.** An attacker inside the vendor platform reads supplier staff contact details out of the stored compliance documents.

- cites: `store:document-store`, `entity:portal-vendor`
- tier: expected · severity: low/high · verb: `read`
- recorded note: The documents are stated to hold contact details for supplier staff, which is what drives the pii tag; fully compliant states nothing about who inside the vendor can read them.

> mark:

**11.** An attacker on the network path between a supplier and the portal reads uploaded paperwork in transit, because whether that traffic is encrypted is unverified.

- cites: `flow:supplier-to-supplier-portal:upload-documents`
- tier: expected · severity: low/medium · verb: `intercept`
- recorded note: The upload flow crosses from the public internet into the vendor platform and the datasheet's encryption claim never becomes a stated property of it.

> mark:


### denial-of-service

**12.** The vendor platform becomes unavailable and suppliers cannot file compliance paperwork while it is down.

- cites: `entity:portal-vendor`, `process:supplier-portal`
- tier: expected · severity: medium/medium · verb: `disable`
- recorded note: Availability of a system we do not run is a real exposure and not a control gap; it belongs in the report even though there is nothing on our side to fix.

> mark:

**13.** An attacker stops the nightly extract from being loaded and the supplier database silently keeps serving stale supplier records.

- cites: `store:landing-bucket`, `process:supplier-master-service`
- tier: must-find · severity: low/high · verb: `disable`
- recorded note: The failure here is staleness rather than an outage: nothing in the source detects a missing nightly file.

> mark:


### elevation-of-privilege

**14.** A signed-in supplier reaches another supplier's compliance documents through the portal, because no separation between supplier tenants is stated.

- cites: `flow:supplier-to-supplier-portal:upload-documents`, `process:supplier-portal`
- tier: must-find · severity: medium/high · verb: `abuse-grant`
- recorded note: Many suppliers share one vendor-hosted product and the source states nothing about isolation between them. Fully compliant is the phrase most likely to be mistaken for an answer to this.

> mark:

**15.** An attacker uses the contents of the extract file to make the supplier master service act beyond what a data load should do, since the file is consumed as-is.

- cites: `flow:supplier-master-service-to-landing-bucket:load-extract`, `process:supplier-master-service`
- tier: expected · severity: low/high · verb: `inject`
- recorded note: Picked up as-is is stated, so treating attacker-influenced file content as trusted input is grounded rather than speculative.

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
Append this entry to `reviews` in `evals/corpus/12-overclaiming-supplier-portal/case.json`, which is
what `tests/test_case_review.py` reads:

```json
  "reviews": [
    {
      "submitted_by": "<the GitHub login opening the PR>",
      "submitted_for": "<who read the case: a login, or the word anonymous>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "3542d5a0939da730951ce8c09de7a18d1bbc74d5e972180acacba73bfd168d41"},
        {"file": "model.json", "sha256": "33f40cfae742117671099ebbda6dc2e5768631cae7e494318fde3638048337c9"},
        {"file": "claims/asvs.json", "sha256": "c2f4a400c6c8ebc8ed4d316fec47e9d162c2c525958b26f5fca40f1daec68814"},
        {"file": "claims/stride.json", "sha256": "9180dccc19d0b67acbf92c65005f0c9095e3c7761fc059251eeb24eecfbac152"}
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
