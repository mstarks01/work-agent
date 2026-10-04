# Review sitting — is `15-multitenant-invoicing`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/15-multitenant-invoicing`.

**One invoicing app shared by every customer company, with the tenant filter written into each query by hand** — domain `multi-tenant-saas`.

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

> Invoicing service for small businesses.
>
> We sell an invoicing service. Each customer company is one tenant, and all
> companies share one billing app, one database and one PDF bucket in our cloud
> platform.
>
> People at a customer company use the billing app in their browser over HTTPS.
> Each user belongs to one company and is either an admin or a viewer. They sign
> in with an email address and a password and get a session cookie. There is no
> second factor, for admins or viewers.
>
> The billing app keeps every company's invoices, customers and payout bank
> details in one PostgreSQL database, the tenant database. Every table has a
> company ID column. The billing app adds the company filter to each query
> itself, and the database has no row-level security. The app connects with one
> database account for all companies.
>
> When a user opens an invoice, the billing app loads it by its invoice number.
> Nobody wrote down whether it also checks that the invoice belongs to the user's
> company. Invoice numbers count up from 1000 across all companies.
>
> The pages hide the buttons a viewer may not use. Nobody wrote down whether the
> billing app checks the user's role again when a request arrives. The invoice
> edit form posts the whole invoice back, and the billing app saves every field
> it receives, including the paid flag and the company's payout bank account.
>
> Each invoice email sends the payer a link that ends in the invoice number. The
> payment page shows the invoice, the company's name and its bank details, and it
> asks the payer to sign in to nothing.
>
> To make a PDF, the billing app puts a render job on the render queue. The job
> carries a company ID and an invoice number. The PDF renderer takes jobs off the
> queue one at a time, reads the invoice from the tenant database and writes the
> PDF to the PDF bucket under the company ID and the invoice number. It trusts the
> company ID the job carries. Any user can ask for a PDF of any invoice, as often
> as they like. The billing app fetches PDFs from the bucket with one service
> credential for all companies.
>
> Our support agents work from the support office network. The admin console is
> reachable only from there, and each agent signs in to it with their own account
> and a one-time code. From the admin console an agent can open a session in the
> billing app as any customer user. Everything the agent then does is recorded in
> the tenant database against that customer user, and not against the agent.
>
> Nobody recorded how the billing app and the renderer reach the database, the
> queue or the bucket, or how the database and the bucket are protected at rest.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:company-user | human | boundary:internet |
| entity:payer | human | boundary:internet |
| entity:support-agent | human | boundary:support-office-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:billing-app | internet-facing | web | boundary:cloud-platform | web app over a shared PostgreSQL database |
| process:pdf-renderer | unknown | non-web | boundary:cloud-platform | unknown |
| process:admin-console | internal | unknown | boundary:cloud-platform | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:tenant-database | boundary:cloud-platform | unknown | unknown |
| store:render-queue | boundary:cloud-platform | unknown | unknown |
| store:pdf-bucket | boundary:cloud-platform | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:company-user>process:billing-app>manage-invoices | entity:company-user | process:billing-app | HTTPS | password and session cookie; no second factor | HTTPS |
| flow:entity:payer>process:billing-app>open-payment-page | entity:payer | process:billing-app | HTTPS | none; the link ends in the invoice number | HTTPS |
| flow:process:billing-app>store:tenant-database>read-and-write-tenant-data | process:billing-app | store:tenant-database | unknown | one database account for all companies | unknown |
| flow:process:billing-app>store:render-queue>queue-render-job | process:billing-app | store:render-queue | unknown | unknown | unknown |
| flow:process:pdf-renderer>store:render-queue>take-render-jobs | process:pdf-renderer | store:render-queue | unknown | unknown | unknown |
| flow:process:pdf-renderer>store:tenant-database>read-invoice | process:pdf-renderer | store:tenant-database | unknown | unknown | unknown |
| flow:process:pdf-renderer>store:pdf-bucket>write-pdf | process:pdf-renderer | store:pdf-bucket | unknown | unknown | unknown |
| flow:process:billing-app>store:pdf-bucket>fetch-pdfs | process:billing-app | store:pdf-bucket | unknown | one service credential for all companies | unknown |
| flow:entity:support-agent>process:admin-console>use-admin-console | entity:support-agent | process:admin-console | unknown | own account and a one-time code | unknown |
| flow:process:admin-console>process:billing-app>open-session-as-customer-user | process:admin-console | process:billing-app | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:internet | network |
| boundary:cloud-platform | network |
| boundary:support-office-network | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:billing-app` — Nobody wrote down whether it checks that an invoice belongs to the user's company, or whether it checks the user's role again when a request arrives.
- `store:tenant-database` — The source states nobody recorded how the database and the bucket are protected at rest.
- `store:pdf-bucket` — The source states nobody recorded how the database and the bucket are protected at rest.
- `flow:entity:payer>process:billing-app>open-payment-page` — The source states the payment page asks for no sign-in; that the link uses HTTPS is inferred from the statement that users reach the app over HTTPS.
- `flow:process:billing-app>store:tenant-database>read-and-write-tenant-data` — The source states nobody recorded how the billing app and the renderer reach the database, the queue or the bucket.
- `flow:process:billing-app>store:render-queue>queue-render-job` — The source states nobody recorded how the billing app and the renderer reach the database, the queue or the bucket.
- `flow:process:pdf-renderer>store:render-queue>take-render-jobs` — The source states nobody recorded how the billing app and the renderer reach the database, the queue or the bucket.
- `flow:process:pdf-renderer>store:tenant-database>read-invoice` — The source states nobody recorded how the billing app and the renderer reach the database, the queue or the bucket.
- `flow:process:pdf-renderer>store:pdf-bucket>write-pdf` — The source states nobody recorded how the billing app and the renderer reach the database, the queue or the bucket.
- `flow:process:billing-app>store:pdf-bucket>fetch-pdfs` — The source states nobody recorded how the billing app and the renderer reach the database, the queue or the bucket.
- `flow:process:admin-console>process:billing-app>open-session-as-customer-user` — Everything the agent then does is recorded against the customer user, not the agent.

**Assumptions**

- `entity:company-user` — Company users and payers are placed on the internet because the schema requires a zone. (basis: They reach the billing app in a browser over HTTPS from their own companies; the source places them nowhere.)
- `entity:payer` — Payers are placed on the internet because the schema requires a zone. (basis: A payer follows an emailed link in a browser; the source places them nowhere.)
- `process:billing-app` — The billing app is reachable from the internet. (basis: Customer companies' users and payers reach it in a browser; the source states no exposure directly.)
- `process:admin-console` — The admin console runs on the cloud platform. (basis: It opens sessions in the billing app; the source states only where it is reachable from, not where it runs.)

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


### authorization

**A1.** `V8.2.2` — The payment page serves any invoice by its counting number with no sign-in, so access to an invoice is not tied to anyone's permission to see it.

- `process:billing-app`, `flow:entity:payer>process:billing-app>open-payment-page`
- Stated outright.

> mark:

**A2.** `V8.4.1` — Nothing states that the billing app checks, on each request, that the invoice it loads by number belongs to the user's company.

- `process:billing-app`, `store:tenant-database`, `flow:entity:company-user>process:billing-app>manage-invoices`
- The app adds company filters by hand. Per-invoice ownership enforcement is unspecified. Missing row-level security in the database alone is not a vulnerability.

> mark:

**A3.** `V8.3.1` — The only stated role control is hiding buttons in the pages; nothing states that the server checks the role on each request.

- `process:billing-app`, `flow:entity:company-user>process:billing-app>manage-invoices`
- A client-side control is stated and a server-side one is not.

> mark:

**A4.** `V8.2.3` — The billing app saves every field an invoice edit posts, so nothing states that the paid flag and the payout bank account are limited to the users allowed to change them.

- `process:billing-app`, `flow:entity:company-user>process:billing-app>manage-invoices`
- Saving every posted field supports mass assignment. Whether any role is held back from those fields is unknown.

> mark:

**A5.** `V8.1.1` — The description does not define what each role may do to which invoices, beyond naming an admin and a viewer role.

- `process:billing-app`
- The description states tenant membership and query filtering; it does not define the complete permissions of each role.

> mark:

**A6.** `V8.1.2` — The description does not define which invoice fields each role may read or change.

- `process:billing-app`
- The field-level half of V8.1.1. This does not show that no policy exists elsewhere.

> mark:


### secure-coding-and-architecture

**A7.** `V15.3.3` — The edit handler accepts every field it receives rather than a list of fields each action allows.

- `process:billing-app`, `flow:entity:company-user>process:billing-app>manage-invoices`
- Stated outright; the same fact as V8.2.3 read as a coding defect.

> mark:

**A8.** `V15.1.3` — The supplied description documents asynchronous, serial PDF rendering but does not explain how it prevents excessive submissions from denying timely service to other users or tenants.

- `process:billing-app`, `process:pdf-renderer`, `store:render-queue`
- Missing documentation of the protection strategy, not proof that every operational defence is absent.

> mark:


### authentication

**A9.** `V6.3.3` — Users, admins included, sign in with a password alone.

- `process:billing-app`, `flow:entity:company-user>process:billing-app>manage-invoices`
- Stated outright: no second factor for admins or viewers.

> mark:


### security-logging-and-error-handling

**A10.** `V16.2.1` — When a support agent acts as a customer user, the record names the customer user and not the agent who acted.

- `process:admin-console`, `store:tenant-database`, `flow:process:admin-console>process:billing-app>open-session-as-customer-user`
- Stated outright.

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

**1.** An attacker who guesses or reuses a company admin's password signs in to the billing app as that admin, because there is no second factor.

- `flow:entity:company-user>process:billing-app>manage-invoices`, `process:billing-app`
- severity: medium/high · verb: `guess-credential`
- Stated outright: password only, for admins too. An admin can change the company's payout bank account.

> mark:

**2.** An attacker who takes over one support agent's admin console account opens billing-app sessions as any user of any company.

- `flow:process:admin-console>process:billing-app>open-session-as-customer-user`, `process:admin-console`, `process:billing-app`
- severity: low/high · verb: `use-credential`
- The console is reachable only from the support office and needs a one-time code, which keeps the likelihood low; the reach, every user of every company, keeps the impact high.

> mark:


### tampering

**3.** A company user posts an invoice edit that sets the paid flag or replaces the company's payout bank account, and the billing app saves it because it saves every field it receives.

- `flow:entity:company-user>process:billing-app>manage-invoices`, `process:billing-app`, `store:tenant-database`
- severity: high/high · verb: `alter`
- Stated outright. A viewer can do it too unless the app checks the role on the request, which the source says nobody wrote down. The bank account is the payment page's bank account, so payers then pay the attacker.

> mark:

**4.** An attacker who can write to the render queue plants a job whose company ID does not match its invoice, and the renderer files that invoice's PDF under the wrong company, unless it checks that the two belong together.

- `flow:process:billing-app>store:render-queue>queue-render-job`, `store:render-queue`, `process:pdf-renderer`
- severity: low/medium · verb: `plant`
- Conditional: the source states nothing about who can write to the queue. The renderer's trust in the job's company ID is stated.

> mark:


### repudiation

**5.** A support agent acting as a customer user changes that company's invoices, and the database record names only the customer user, not the agent.

- `flow:process:admin-console>process:billing-app>open-session-as-customer-user`, `process:admin-console`, `store:tenant-database`
- severity: medium/medium · verb: `unattributable`
- Stated outright: everything the agent does is recorded against the customer user.

> mark:


### information-disclosure

**6.** Anyone steps through invoice numbers on the payment page and reads every company's invoices, company names and bank details, because the numbers count up across all companies and the page asks for no sign-in.

- `flow:entity:payer>process:billing-app>open-payment-page`, `process:billing-app`, `entity:payer`
- severity: high/high · verb: `elicit`
- The case's clearest cross-tenant finding, and entirely stated: a shared counting sequence and a page with no sign-in.

> mark:

**7.** A signed-in user of one company opens another company's invoice by its number, if the billing app does not check that the invoice belongs to the user's company.

- `flow:entity:company-user>process:billing-app>manage-invoices`, `process:billing-app`, `store:tenant-database`
- severity: medium/high · verb: `elicit`
- Conditional on the check nobody wrote down. The tenant filter lives in each query by hand and the database has no row-level security, so one missed filter is enough.

> mark:

**8.** An attacker who steals a copy of the tenant database may read every company's invoices and payout bank details if the copy is unencrypted or the attacker can also obtain the means to decrypt it.

- `store:tenant-database`, `flow:process:billing-app>store:tenant-database>read-and-write-tenant-data`
- severity: low/high · verb: `read`
- Protection at rest is unspecified; one copy holds every tenant.

> mark:


### spoofing

**9.** An attacker who obtains the billing app's bucket credential acts as the billing app and reads every company's invoice PDFs, because one credential serves all companies.

- `store:pdf-bucket`, `flow:process:billing-app>store:pdf-bucket>fetch-pdfs`
- severity: low/high · verb: `use-credential`
- Stated: one service credential for all companies. How it could be obtained is not stated.

> mark:


### denial-of-service

**10.** A user asks for PDFs as often as they like and fills the render queue, so the one-job-at-a-time renderer delays every other company's PDFs.

- `flow:entity:company-user>process:billing-app>manage-invoices`, `process:pdf-renderer`, `store:render-queue`
- severity: medium/medium · verb: `flood`
- Stated outright: no limit on requests, one job at a time, one renderer for all companies.

> mark:


### elevation-of-privilege

**11.** A viewer sends the requests behind the buttons the pages hide from them and performs admin actions, if the billing app does not check the role on each request.

- `flow:entity:company-user>process:billing-app>manage-invoices`, `process:billing-app`
- severity: medium/high · verb: `escalate`
- Conditional on the check nobody wrote down; hiding a button is the only stated control.

> mark:


### information-disclosure

**12.** A company user who requests another company's invoice PDF may obtain its contents if job creation or rendering fails to enforce tenant ownership and PDF retrieval also permits that user to fetch the result.

- `flow:entity:company-user>process:billing-app>manage-invoices`, `process:billing-app`, `process:pdf-renderer`
- severity: low/high · verb: `elicit`
- The source states that any user may ask for a PDF of any invoice. It does not state that the job carries the requesting user's company or that the user can fetch the result.

> mark:

**13.** An attacker on an internal network path may read sensitive invoice or job data if transport protection does not prevent interception.

- `flow:process:billing-app>store:tenant-database>read-and-write-tenant-data`, `flow:process:billing-app>store:render-queue>queue-render-job`, `flow:process:pdf-renderer>store:render-queue>take-render-jobs`, `flow:process:pdf-renderer>store:tenant-database>read-invoice`, `flow:process:pdf-renderer>store:pdf-bucket>write-pdf`, `flow:process:billing-app>store:pdf-bucket>fetch-pdfs`
- severity: low/high · verb: `intercept`
- Transport protection on the billing app's and the renderer's connections to the database, the queue and the bucket is unspecified.

> mark:


### tampering

**14.** An attacker on a render-job network path may change the company ID or invoice number if authenticated integrity protection does not prevent alteration.

- `flow:process:billing-app>store:render-queue>queue-render-job`, `flow:process:pdf-renderer>store:render-queue>take-render-jobs`
- severity: low/medium · verb: `alter-in-transit`
- Integrity protection on the render-job paths is unspecified, and the renderer trusts the job's company ID.

> mark:


### information-disclosure

**15.** An attacker who steals a copy of the PDF bucket may read every company's invoice PDFs if the copy is unencrypted or the attacker can also obtain the means to decrypt it.

- `store:pdf-bucket`, `flow:process:billing-app>store:pdf-bucket>fetch-pdfs`
- severity: low/high · verb: `read`
- Protection at rest for the shared bucket is unspecified; it holds every company's PDFs.

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
    "15-multitenant-invoicing": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "5f6c64fc0b7494033e74547c61f660750882de95b66dd8ca9370eef14e7c796f",
      "model.json": "55a71c8adc0434496dc4e280dbfc108ff84f7bb72dc629515f65560480226bff",
      "claims/asvs.json": "dfd36f46fa16127d8c03766461e13af894d199c1cbdc04155e5fe5afae1a0f1d",
      "claims/stride.json": "429edf408e26c9c6fb149ef9fe0c84535c4138ce3e145937dfebcb3c8b4c46d4"
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
