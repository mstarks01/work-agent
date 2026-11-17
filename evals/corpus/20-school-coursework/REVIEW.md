# Review sitting — is `20-school-coursework`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/20-school-coursework`.

**A school coursework platform whose parts state different access checks** — domain `school coursework`.

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

> School coursework platform — what we run, written up by the school's IT team.
>
> Students hand in their coursework through the coursework service, which is on the internet. Students and teachers sign in with their school accounts. The coursework service checks each request against the user's role: a student can hand in and read work, and only a teacher can mark it.
>
> A student opens, changes and hands in a piece of work again at an address that holds the submission's number. The coursework service checks that the user is a student, but not that the submission is theirs.
>
> The coursework service keeps submissions, marks and comments in the coursework database. Its database account can only read and write the coursework tables.
>
> Office staff manage classes, accounts and final grades in the admin console. The admin console runs on the school office network, which is kept apart for administration. Only office staff are meant to use it, but teachers can also reach it from the school network, and it accepts a teacher's school account without checking whether the teacher is an administrator. The admin console writes classes, accounts and final grades to the coursework database.
>
> Every Friday the grade export sends final grades from the coursework database to the district records system. Dana thinks the grade export has read-only access to the database, but she has not looked.
>
> The coursework service, the grade export and the coursework database run in the school's cloud account.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:student | human | boundary:internet |
| entity:teacher | human | boundary:school-network |
| entity:office-staff | human | boundary:school-office-network |
| entity:district-records-system | external-system | boundary:internet |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:coursework-service | internet-facing | web | boundary:school-cloud-account | unknown |
| process:admin-console | unknown | unknown | boundary:school-office-network | unknown |
| process:grade-export | unknown | unknown | boundary:school-cloud-account | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:coursework-database | boundary:school-cloud-account | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:student>process:coursework-service>hand-in-work | entity:student | process:coursework-service | unknown | unknown | unknown |
| flow:entity:teacher>process:coursework-service>mark-work | entity:teacher | process:coursework-service | unknown | unknown | unknown |
| flow:process:coursework-service>store:coursework-database>keep-coursework | process:coursework-service | store:coursework-database | unknown | unknown | unknown |
| flow:entity:office-staff>process:admin-console>manage-classes-and-grades | entity:office-staff | process:admin-console | unknown | unknown | unknown |
| flow:entity:teacher>process:admin-console>open-admin-console | entity:teacher | process:admin-console | unknown | unknown | unknown |
| flow:process:admin-console>store:coursework-database>write-classes-and-grades | process:admin-console | store:coursework-database | unknown | unknown | unknown |
| flow:process:grade-export>store:coursework-database>read-final-grades | process:grade-export | store:coursework-database | unknown | unknown | unknown |
| flow:process:grade-export>entity:district-records-system>send-final-grades | process:grade-export | entity:district-records-system | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:internet | network |
| boundary:school-network | network |
| boundary:school-office-network | privilege |
| boundary:school-cloud-account | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:coursework-service` — Stated in the source: "The coursework service checks each request against the user's role: a student can hand in and read work, and only a teacher can mark it." and "The coursework service checks that the user is a student, but not that the submission is theirs."
- `process:admin-console` — Stated in the source: "Only office staff are meant to use it, but teachers can also reach it from the school network, and it accepts a teacher's school account without checking whether the teacher is an administrator."
- `process:grade-export` — A hedge in the source: "Dana thinks the grade export has read-only access to the database, but she has not looked."
- `flow:entity:student>process:coursework-service>hand-in-work` — The source says students sign in with their school accounts and names no mechanism, so authentication reads unknown.
- `flow:entity:teacher>process:coursework-service>mark-work` — The source says teachers sign in with their school accounts and names no mechanism, so authentication reads unknown.
- `flow:process:coursework-service>store:coursework-database>keep-coursework` — The source states a database account limited to the coursework tables and names no mechanism behind it, so authentication reads unknown.

**Assumptions**

- `entity:student` — Students are placed on the internet because the schema requires a zone. (basis: Students reach the coursework service on the internet; the source does not state where students are.)
- `entity:office-staff` — Office staff are placed on the school office network because the schema requires a zone. (basis: They manage the admin console, which runs on the office network; the source does not state where office staff sit.)
- `entity:district-records-system` — The district records system is placed on the internet because the schema requires a zone. (basis: The source does not state where the district records system runs, and it is outside the school's cloud account.)
- `process:coursework-service` — The coursework service presents a web interface. (basis: A student opens work at an address that holds the submission's number, which is a web address.)

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

## Part 2 — the 10 recorded STRIDE threats

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

**1.** If an attacker obtains a student's school account, they can hand in, change or read that student's work as the student.

- `flow:entity:student>process:coursework-service>hand-in-work`, `entity:student`, `process:coursework-service`
- severity: medium/medium · verb: `use-credential`
- Students sign in with their school accounts. The source names no mechanism and no second factor, so the claim is conditional.

> mark:


### tampering

**2.** A student can change the submission number in the address and change or replace another student's work, because the coursework service checks that the user is a student but not that the submission is theirs.

- `process:coursework-service`, `flow:entity:student>process:coursework-service>hand-in-work`, `store:coursework-database`
- severity: high/high · verb: `alter`
- Stated outright: "The coursework service checks that the user is a student, but not that the submission is theirs." and a student "opens, changes and hands in a piece of work again at an address that holds the submission's number".

> mark:

**3.** If the grade export sends final grades to the district records system without integrity protection, an attacker on the path can change grades before they arrive.

- `flow:process:grade-export>entity:district-records-system>send-final-grades`
- severity: low/high · verb: `alter-in-transit`
- The source says nothing about how the grades travel or how they are protected.

> mark:


### repudiation

**4.** If the admin console does not record which account changed a final grade, a teacher who changed grades through it can deny it, and the school cannot show who did it.

- `flow:entity:teacher>process:admin-console>open-admin-console`, `process:admin-console`
- severity: medium/medium · verb: `unattributable`
- Any teacher can open the admin console, as stated. The source says nothing about a record of changes.

> mark:


### information-disclosure

**5.** A student can change the submission number in the address and read another student's work and its marks and comments, because the coursework service does not check that the submission is theirs.

- `process:coursework-service`, `flow:entity:student>process:coursework-service>hand-in-work`, `store:coursework-database`
- severity: high/medium · verb: `elicit`
- Stated outright: the coursework service "checks that the user is a student, but not that the submission is theirs". The system returns the work in answer to the number the student sends, so the verb is elicit.

> mark:

**6.** If the coursework database is not encrypted at rest and access to its storage is not restricted, an attacker with access to a copy of its storage reads every student's work, marks and accounts.

- `store:coursework-database`
- severity: low/high · verb: `read`
- The source says nothing about encryption at rest or about who can get the database's storage.

> mark:

**7.** If the final grades are not encrypted in transit, an attacker on the path between the grade export and the district records system reads every student's final grades.

- `flow:process:grade-export>entity:district-records-system>send-final-grades`
- severity: low/medium · verb: `intercept`
- The source says nothing about encryption in transit.

> mark:


### denial-of-service

**8.** If the internet-facing coursework service does not limit the requests it accepts, an attacker floods it near a deadline so that students cannot hand in their work.

- `process:coursework-service`, `flow:entity:student>process:coursework-service>hand-in-work`
- severity: medium/medium · verb: `flood`
- The source states no limit on the coursework service.

> mark:


### elevation-of-privilege

**9.** A teacher can reach the admin console from the school network and change classes, accounts and final grades, because the admin console accepts a teacher's school account without checking whether the teacher is an administrator.

- `process:admin-console`, `flow:entity:teacher>process:admin-console>open-admin-console`, `entity:teacher`
- severity: high/high · verb: `escalate`
- Stated outright: "Only office staff are meant to use it, but teachers can also reach it from the school network, and it accepts a teacher's school account without checking whether the teacher is an administrator." The office network is kept apart for administration, and a teacher holds no administrator access, so the verb is escalate.

> mark:

**10.** If the grade export can write the coursework database, an attacker who compromises it can change grades and other records, which is more than the export's job needs.

- `process:grade-export`, `flow:process:grade-export>store:coursework-database>read-final-grades`, `store:coursework-database`
- severity: low/high · verb: `abuse-grant`
- A hedge in the source: Dana thinks the export has read-only access, and she has not looked. The claim stays conditional.

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
    "20-school-coursework": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "dff7ec2640470f43ebde447fc26709a8a006407d447525eac0c8ee180e171c2e",
      "model.json": "0083d36849f290026dab4e8fc090f2c6197dd7e7ed735e5e1d7afda14bbafcfb",
      "claims/stride.json": "8dc58cdc02ab6addc95cab0d4684ffe6fabb24f91fc0dcd83518e92a45070eb2"
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
