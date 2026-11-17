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
> Pet owners book appointments and read their pets' records in the owner app. The owner app calls the clinic API, which is on the internet. Owners sign in with an email address and a password. When an owner opens a record, the clinic API checks that the pet belongs to that owner's account.
>
> Vets, nurses and receptionists use the clinic desktop app on the clinic network, and it calls the same clinic API. Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff.
>
> The clinic API keeps owners, pets, appointments and medical notes in the records database. It connects to the records database with the database's admin account.
>
> The partner lab sends test results over the internet to the lab results importer. The importer writes each result into the pet's record in the records database. It does not check which clinic the result is for, or whether the lab may send results for that pet: it writes every result it receives.
>
> The billing service reads appointments from the records database and sends invoices to owners by email. Priya thinks the billing service checks each request against the caller's role, but nobody has confirmed it.
>
> The clinic API, the lab results importer and the billing service run in the group's cloud account. The records database is on the group's private database network, and only those three services can reach it.

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
| process:clinic-api | internet-facing | unknown | boundary:group-cloud-account | unknown |
| process:lab-results-importer | internet-facing | unknown | boundary:group-cloud-account | unknown |
| process:billing-service | unknown | unknown | boundary:group-cloud-account | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:records-database | boundary:private-database-network | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:pet-owner>process:clinic-api>book-appointments-and-read-records | entity:pet-owner | process:clinic-api | unknown | password sign-in with an email address | unknown |
| flow:entity:clinic-staff>process:clinic-api>manage-records | entity:clinic-staff | process:clinic-api | unknown | unknown | unknown |
| flow:process:clinic-api>store:records-database>keep-records | process:clinic-api | store:records-database | unknown | unknown | unknown |
| flow:entity:partner-lab>process:lab-results-importer>send-test-results | entity:partner-lab | process:lab-results-importer | unknown | unknown | unknown |
| flow:process:lab-results-importer>store:records-database>write-lab-results | process:lab-results-importer | store:records-database | unknown | unknown | unknown |
| flow:process:billing-service>store:records-database>read-appointments | process:billing-service | store:records-database | unknown | unknown | unknown |
| flow:process:billing-service>entity:pet-owner>send-invoices | process:billing-service | entity:pet-owner | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:internet | network |
| boundary:clinic-network | network |
| boundary:group-cloud-account | network |
| boundary:private-database-network | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:clinic-api` — Stated in the source: "When an owner opens a record, the clinic API checks that the pet belongs to that owner's account." and "Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff."
- `process:lab-results-importer` — Stated in the source: "It does not check which clinic the result is for, or whether the lab may send results for that pet: it writes every result it receives."
- `process:billing-service` — A hedge in the source: "Priya thinks the billing service checks each request against the caller's role, but nobody has confirmed it."
- `flow:process:clinic-api>store:records-database>keep-records` — The source states the account, "the database's admin account", and names no mechanism behind it, so authentication reads unknown.

**Assumptions**

- `entity:pet-owner` — Pet owners are placed on the internet because the schema requires a zone. (basis: The owner app calls the clinic API on the internet; the source does not state where owners are.)
- `entity:partner-lab` — The partner lab is placed on the internet because the schema requires a zone. (basis: The lab sends results over the internet; the source does not state where the lab runs.)
- `process:lab-results-importer` — The lab results importer is internet-facing. (basis: The partner lab sends test results to it over the internet.)

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

## Part 2 — the 14 recorded STRIDE threats

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

**1.** If the clinic API does not limit or slow failed sign-ins, an attacker can guess an owner's password and then read that owner's pet records and book appointments as the owner.

- `flow:entity:pet-owner>process:clinic-api>book-appointments-and-read-records`, `entity:pet-owner`, `process:clinic-api`
- severity: medium/medium · verb: `guess-credential`
- Owners sign in with an email address and a password, as stated. The source says nothing about failed sign-ins, so the claim is conditional.

> mark:

**2.** If the lab results importer does not authenticate the partner lab, an attacker on the internet can send test results that the importer accepts as the lab's.

- `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `entity:partner-lab`, `process:lab-results-importer`
- severity: medium/high · verb: `impersonate`
- The source never says how the importer knows a result came from the partner lab.

> mark:


### tampering

**3.** Whoever can send results to the lab results importer can write a test result into any pet's record, because the importer does not check which clinic the result is for or whether the lab may send results for that pet.

- `process:lab-results-importer`, `flow:process:lab-results-importer>store:records-database>write-lab-results`, `store:records-database`
- severity: medium/high · verb: `alter`
- Stated outright: "It does not check which clinic the result is for, or whether the lab may send results for that pet: it writes every result it receives." A wrong result in a pet's record can lead a vet to a wrong treatment.

> mark:

**4.** If test results travel over the internet without integrity protection, an attacker on the path between the partner lab and the importer can change a result before the importer writes it.

- `flow:entity:partner-lab>process:lab-results-importer>send-test-results`
- severity: low/high · verb: `alter-in-transit`
- The source says the results travel over the internet and says nothing about their protection.

> mark:


### repudiation

**5.** If the clinic API does not record which staff account deleted or changed a medical note, a staff member can deny a change, and the clinic cannot show who made it.

- `flow:entity:clinic-staff>process:clinic-api>manage-records`, `process:clinic-api`
- severity: medium/medium · verb: `unattributable`
- Every staff account can delete medical notes. The source says nothing about a record of changes.

> mark:

**6.** If the importer keeps no record of who sent each result, nobody can show whether a wrong result in a pet's record came from the partner lab.

- `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `process:lab-results-importer`
- severity: low/medium · verb: `unattributable`
- The source says nothing about a record of received results.

> mark:


### information-disclosure

**7.** If the owner app's requests are not encrypted in transit, an attacker on the network path reads owners' details and their pets' records.

- `flow:entity:pet-owner>process:clinic-api>book-appointments-and-read-records`
- severity: low/medium · verb: `intercept`
- The source says nothing about encryption in transit.

> mark:

**8.** If the billing service does not check each request against the caller's role, a caller who reaches it can get other owners' appointments and invoices.

- `process:billing-service`, `flow:process:billing-service>store:records-database>read-appointments`, `store:records-database`
- severity: low/medium · verb: `elicit`
- A hedge in the source: Priya thinks the billing service checks each request against the caller's role, and nobody has confirmed it. The claim stays conditional.

> mark:

**9.** If the records database is not encrypted at rest and access to its storage is not restricted, an attacker with access to a copy of its storage reads every owner's details and every pet's medical notes.

- `store:records-database`
- severity: low/high · verb: `read`
- The source says nothing about encryption at rest or about who can get the database's storage.

> mark:


### denial-of-service

**10.** If the internet-facing clinic API does not limit the requests it accepts, an attacker floods it until owners and clinic staff cannot use it, which stops the clinics' own work.

- `process:clinic-api`, `flow:entity:pet-owner>process:clinic-api>book-appointments-and-read-records`
- severity: medium/high · verb: `flood`
- The source states no limit on the clinic API. Staff use the same API, so an outage also stops the clinics.

> mark:

**11.** If the importer does not limit what it accepts, an attacker sends very many results to the internet-facing importer, and it writes all of them into the records database.

- `process:lab-results-importer`, `flow:entity:partner-lab>process:lab-results-importer>send-test-results`, `store:records-database`
- severity: low/medium · verb: `flood`
- The importer writes every result it receives, as stated. The source states no limit.

> mark:


### elevation-of-privilege

**12.** Any staff account, a receptionist's included, can delete medical notes and change any record in the clinic API, because the clinic API has no roles; a staff member can use that access far beyond their job.

- `process:clinic-api`, `flow:entity:clinic-staff>process:clinic-api>manage-records`, `entity:clinic-staff`
- severity: medium/high · verb: `abuse-grant`
- Stated outright: "Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff." The staff member holds the access, so the verb is abuse-grant.

> mark:

**13.** If an attacker compromises the internet-facing clinic API, they act with the database's admin account, and so can read, change or delete everything in the records database, and change the database itself.

- `process:clinic-api`, `flow:process:clinic-api>store:records-database>keep-records`, `store:records-database`
- severity: low/high · verb: `abuse-grant`
- The admin account is stated: "It connects to the records database with the database's admin account." The compromise is not, so the claim is conditional.

> mark:

**14.** If an attacker compromises the internet-facing lab results importer, they act with its access to the records database, which can reach more than the lab results the importer needs to write.

- `process:lab-results-importer`, `flow:process:lab-results-importer>store:records-database>write-lab-results`, `store:records-database`
- severity: low/medium · verb: `abuse-grant`
- The source states nothing about the importer's database access.

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
      "source.md": "dfa7af4f5d735906fa9a64edd0eaffcfa0b5efbcc6cf993efbdf64bfb77be0f5",
      "model.json": "388991037363ce9feb329433fd6a85ad0ef3274cd682da98e1ad32acfa25713c",
      "claims/stride.json": "fab4082f3c429e342c65b6c61ddbdf1e3e8f628e47d1e16bbbdc13449155594a"
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
