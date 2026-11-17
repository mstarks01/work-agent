# Review sitting — is `19-vet-clinic-records`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/19-vet-clinic-records`.

**A vet clinic group whose services state different access checks** — domain `veterinary records`.

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

> Vet clinic records — notes for the security review, from the clinic group's IT lead.
>
> Pet owners book appointments and read their pets' records in the owner app. The owner app calls the owner API, which is on the internet. Owners sign in with an email address and a password. Whenever an owner opens a record or books an appointment, the owner API checks that the pet belongs to that owner's account.
>
> Vets, nurses and receptionists use the clinic desktop app on the clinic network, and it calls the clinic API. The clinic API checks only that a staff account has signed in. Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff.
>
> Both APIs keep owners, pets, appointments and medical notes in the records database, and both connect to it with the database's admin account.
>
> The lab results importer is on the internet, and the partner lab sends test results to it over the internet. The importer writes each result into the pet's record in the records database. It does not check whether the partner lab may send a result: it writes every result the lab sends.
>
> The billing service reads appointments from the records database and sends invoices to owners by email. Receptionists open owners' invoices in the billing service from the clinic desktop app. Priya thinks the billing service checks each request against the caller's role, but nobody has confirmed it.
>
> The owner API, the clinic API, the lab results importer and the billing service run in the group's cloud account. The records database is on the group's private database network, and only those four services can reach it.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:pet-owner | human | boundary:internet |
| entity:clinic-staff | human | boundary:clinic-network |
| entity:partner-lab | external-system | boundary:internet |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:owner-api | internet-facing | unknown | boundary:group-cloud-account | unknown |
| process:clinic-api | unknown | unknown | boundary:group-cloud-account | unknown |
| process:lab-results-importer | internet-facing | unknown | boundary:group-cloud-account | unknown |
| process:billing-service | unknown | unknown | boundary:group-cloud-account | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:records-database | boundary:private-database-network | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:pet-owner>process:owner-api>book-appointments-and-read-records | entity:pet-owner | process:owner-api | unknown | password sign-in with an email address | unknown |
| flow:process:owner-api>store:records-database>keep-owner-records | process:owner-api | store:records-database | unknown | unknown | unknown |
| flow:entity:clinic-staff>process:clinic-api>manage-records | entity:clinic-staff | process:clinic-api | unknown | unknown | unknown |
| flow:process:clinic-api>store:records-database>keep-records | process:clinic-api | store:records-database | unknown | unknown | unknown |
| flow:entity:partner-lab>process:lab-results-importer>send-test-results | entity:partner-lab | process:lab-results-importer | unknown | unknown | unknown |
| flow:process:lab-results-importer>store:records-database>write-lab-results | process:lab-results-importer | store:records-database | unknown | unknown | unknown |
| flow:process:billing-service>store:records-database>read-appointments | process:billing-service | store:records-database | unknown | unknown | unknown |
| flow:process:billing-service>entity:pet-owner>send-invoices | process:billing-service | entity:pet-owner | email | unknown | unknown |
| flow:entity:clinic-staff>process:billing-service>open-invoices | entity:clinic-staff | process:billing-service | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:internet | network |
| boundary:clinic-network | network |
| boundary:group-cloud-account | network |
| boundary:private-database-network | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:owner-api` — Stated in the source: "Whenever an owner opens a record or books an appointment, the owner API checks that the pet belongs to that owner's account."
- `process:clinic-api` — Stated in the source: "The clinic API checks only that a staff account has signed in." and "Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff."
- `process:lab-results-importer` — Stated in the source: "It does not check whether the partner lab may send a result: it writes every result the lab sends."
- `process:billing-service` — A hedge in the source: "Priya thinks the billing service checks each request against the caller's role, but nobody has confirmed it."
- `flow:process:owner-api>store:records-database>keep-owner-records` — The source states the account, "the database's admin account", and names no mechanism behind it, so authentication reads unknown.
- `flow:entity:clinic-staff>process:clinic-api>manage-records` — The source says the clinic API checks that a staff account has signed in, and names no sign-in mechanism, so authentication reads unknown.
- `flow:process:clinic-api>store:records-database>keep-records` — The source states the account, "the database's admin account", and names no mechanism behind it, so authentication reads unknown.

**Assumptions**

- `entity:pet-owner` — Pet owners are placed on the internet because the schema requires a zone. (basis: The owner app calls the owner API on the internet; the source does not state where owners are.)
- `entity:partner-lab` — The partner lab is placed on the internet because the schema requires a zone. (basis: The lab sends results over the internet; the source does not state where the lab runs.)

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

## Part 2 — the 21 recorded STRIDE threats

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

**1.** An attacker who successfully authenticates with a guessed or reused owner password, defeating any additional sign-in controls, can read that owner's pet records and book appointments as the owner.

- `flow:entity:pet-owner>process:owner-api>book-appointments-and-read-records`, `entity:pet-owner`, `process:owner-api`
- severity: medium/medium · verb: `use-credential`
- Stated: owners sign in with an email address and a password. The source states nothing that slows guesses at sign-in.

> mark:

**2.** An attacker who obtains a staff account's credentials can act in the clinic API with that account's full access, including deleting medical notes.

- `flow:entity:clinic-staff>process:clinic-api>manage-records`, `entity:clinic-staff`, `process:clinic-api`
- severity: medium/high · verb: `use-credential`
- Stated: the clinic API checks only that a staff account has signed in, and every staff account can do everything. No sentence names how staff sign in.

> mark:

**3.** If the lab results importer does not authenticate the partner lab, an attacker on the internet may send test results that the importer accepts as the lab's.

- `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `entity:partner-lab`, `process:lab-results-importer`
- severity: medium/high · verb: `impersonate`
- The source never says how the importer knows that a result came from the partner lab.

> mark:

**4.** If owners cannot tell a real invoice email from a fake one, an attacker may send owners invoices that appear to come from the clinic.

- `flow:process:billing-service>entity:pet-owner>send-invoices`, `process:billing-service`, `entity:pet-owner`
- severity: medium/medium · verb: `impersonate`
- The billing service sends invoices by email, as stated. The source says nothing about how an owner can tell that an email came from the clinic.

> mark:


### tampering

**5.** A compromised or mistaken partner lab can write a false result into any pet's record, because the importer does not check whether the lab may send a result.

- `process:lab-results-importer`, `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `flow:process:lab-results-importer>store:records-database>write-lab-results`, `store:records-database`
- severity: medium/high · verb: `alter`
- Stated outright: "It does not check whether the partner lab may send a result: it writes every result the lab sends." The false result changes the pet's record, so the verb is alter.

> mark:

**6.** If test results travel over the internet without integrity protection, an attacker on the path between the partner lab and the importer may change a result before the importer writes it.

- `flow:entity:partner-lab>process:lab-results-importer>send-test-results`
- severity: low/high · verb: `alter-in-transit`
- Stated: the results travel over the internet. The source says nothing about their protection.

> mark:


### repudiation

**7.** If the clinic API does not record which staff account deleted or changed a medical note, a staff member may deny a change, and the clinic may be unable to show who made it.

- `flow:entity:clinic-staff>process:clinic-api>manage-records`, `process:clinic-api`
- severity: medium/medium · verb: `unattributable`
- Every staff account can delete medical notes, as stated. The source says nothing about a record of changes.

> mark:

**8.** If the importer keeps no record of who sent each result, the clinic may be unable to show whether a wrong result in a pet's record came from the partner lab.

- `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `process:lab-results-importer`
- severity: low/medium · verb: `unattributable`
- The source says nothing about a record of received results.

> mark:


### information-disclosure

**9.** If the owner app's requests are not encrypted in transit, an attacker on the network path may read owners' details and their pets' records.

- `flow:entity:pet-owner>process:owner-api>book-appointments-and-read-records`
- severity: low/medium · verb: `intercept`
- The owner app reaches the owner API on the internet. The source says nothing about encryption in transit.

> mark:

**10.** If the clinic desktop app's requests are not encrypted in transit, an attacker on the path between the clinic network and the clinic API may read medical notes.

- `flow:entity:clinic-staff>process:clinic-api>manage-records`
- severity: low/medium · verb: `intercept`
- The desktop app on the clinic network calls the clinic API in the cloud account. The source says nothing about encryption in transit.

> mark:

**11.** If test results are not encrypted in transit, an attacker on the path between the partner lab and the importer may read pets' test results.

- `flow:entity:partner-lab>process:lab-results-importer>send-test-results`
- severity: low/medium · verb: `intercept`
- Stated: the results travel over the internet. The source says nothing about encryption in transit.

> mark:

**12.** If invoice emails are not protected in transit, an attacker on the mail path may read owners' invoices.

- `flow:process:billing-service>entity:pet-owner>send-invoices`
- severity: low/low · verb: `intercept`
- The billing service sends invoices by email, as stated. The source says nothing about how the emails are protected.

> mark:

**13.** If the billing service does not check each request against the caller's role, a vet or nurse account may open owners' invoices that its job does not need.

- `process:billing-service`, `flow:entity:clinic-staff>process:billing-service>open-invoices`, `store:records-database`
- severity: low/medium · verb: `elicit`
- A hedge in the source: Priya thinks the billing service checks each request against the caller's role, and nobody has confirmed it. The claim stays conditional.

> mark:

**14.** If the records database is not encrypted at rest and access to its storage is not restricted, an attacker with access to a copy of its storage may read every owner's details and every pet's medical notes.

- `store:records-database`
- severity: low/high · verb: `read`
- The source says nothing about encryption at rest or about who can get the database's storage.

> mark:


### denial-of-service

**15.** If the internet-facing owner API does not limit the requests it accepts, an attacker may flood it until owners cannot book appointments or read records.

- `process:owner-api`, `flow:entity:pet-owner>process:owner-api>book-appointments-and-read-records`
- severity: medium/medium · verb: `flood`
- The source states no limit on the owner API.

> mark:

**16.** If the importer does not limit what it accepts, an attacker able to send results to the internet-facing importer may send very many, and the importer writes all of them into the records database.

- `process:lab-results-importer`, `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `store:records-database`
- severity: low/medium · verb: `flood`
- The importer writes every result the lab sends, as stated. The source states no limit.

> mark:


### elevation-of-privilege

**17.** Any staff account, a receptionist's included, can delete medical notes and change any record in the clinic API, because the clinic API has no roles; a staff member can use that access far beyond their job.

- `process:clinic-api`, `flow:entity:clinic-staff>process:clinic-api>manage-records`, `entity:clinic-staff`
- severity: medium/high · verb: `abuse-grant`
- Stated outright: "Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff." The staff member holds the access, so the verb is abuse-grant.

> mark:

**18.** The partner lab can write a result into the record of any pet, because the importer does not check whether the lab may send it; a lab that sends results it should not changes records beyond its job.

- `process:lab-results-importer`, `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `entity:partner-lab`
- severity: low/medium · verb: `abuse-grant`
- The missing check is stated. The source does not say which pets the lab's results may cover, so the reach beyond the lab's job is a condition.

> mark:

**19.** If the billing service does not check each request against the caller's role, a staff account may act in the billing service beyond its role.

- `process:billing-service`, `flow:entity:clinic-staff>process:billing-service>open-invoices`, `entity:clinic-staff`
- severity: low/medium · verb: `escalate`
- A hedge in the source: Priya thinks the role check is there, and nobody has confirmed it.

> mark:

**20.** An attacker who compromises the internet-facing owner API could act with the database's admin account, and so could potentially read, change or delete everything in the records database.

- `process:owner-api`, `flow:process:owner-api>store:records-database>keep-owner-records`, `store:records-database`
- severity: low/high · verb: `abuse-grant`
- The admin account is stated: "Both APIs keep owners, pets, appointments and medical notes in the records database, and both connect to it with the database's admin account." The compromise is not, so the claim is conditional.

> mark:

**21.** An attacker who compromises the internet-facing lab results importer can act with its actual database permissions, potentially changing any records those permissions reach.

- `process:lab-results-importer`, `flow:process:lab-results-importer>store:records-database>write-lab-results`, `store:records-database`
- severity: low/medium · verb: `abuse-grant`
- The source states nothing about the importer's database permissions.

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
    "19-vet-clinic-records": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "c3b1dc97272657e10e019c71ea817ea5a6f46f572ea5947d4cb471835969417b",
      "model.json": "4cddecd65193934202f18f7a0d34b8bf0e35ff10efa48af35248c5ca17d1c9d1",
      "claims/stride.json": "0f709ed8272075828e661ad43bd4a08ad2d02e76b4ab9d25da90db819c42dbe3"
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
nothing else. `uv run python webapp/sitting.py --list` names the cases still
waiting, and derives that list from the merged submissions.
