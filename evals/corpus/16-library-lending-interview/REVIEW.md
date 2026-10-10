# Review sitting — is `16-library-lending-interview`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/16-library-lending-interview`.

**A library lending service, from a written note and a later interview that contradicts it** — domain `library-lending`.

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

### Lending service note (description)

> Library lending service, as written down.
>
> Members borrow, renew and reserve books through the library app on their phones. The lending API is a plain REST service and it is the only way anything touches loans. The lending API is internal-only.
>
> Every branch has self-service kiosks where a member scans their library card and the books they are borrowing or returning. The kiosks send each loan and each return to the lending API. The kiosks sit on the branch network.
>
> Members, loans and reservations live in the loans database. The lending API and the loans database run in the council data centre.
>
> Desk staff can extend a loan, clear a block on a member's account or mark a book as returned by hand, through a circulation page the lending API serves. Desk staff work on the branch network.

### Interview transcript (transcript)

> Dan: Thanks for making time, Priya. I am trying to get the lending service written down properly before the assessment. I have the old service note in front of me, but I was told half of it has moved on.
>
> Priya: More than half, probably. That note has been wrong since the spring, and it was thin before that. People fix the service and nobody fixes the paper. What do you want to start with?
>
> Dan: Start with how a member actually borrows a book from their phone. Forget the diagrams, just walk me through what happens when someone renews a loan or reserves a title in the app.
>
> Priya: The app on their phone calls the lending API. It says which book and which member, and the API checks the loan rules and writes the change. Reserving is the same shape, the app asks for a title and the API puts the member in the queue for it.
>
> Dan: And how does the app reach the lending API? The note says the lending API is internal-only.
>
> Priya: That is one of the wrong bits. The phones talk straight to the lending API over the internet. We opened it up last year when the app moved off the council's old portal, and nobody updated the note because nobody owns the note.
>
> Dan: Straight to it. Alright. What does the lending API check when a phone calls it? What stops me renewing books as somebody else?
>
> Priya: I think it checks a token the app gets at sign-in, but I'd have to look. That code is older than my time on the team and I have never had a reason to open it. Honestly I could not tell you today what it accepts, or what it does with a call it does not like.
>
> Dan: That is fine, an honest gap is more use to me than a guess. What is behind the API?
>
> Priya: The loans database. Names, email addresses, home addresses and every member's full borrowing history live in the loans database. The API is the only thing that reads or writes it. It writes to the two databases— actually, no. We merged those in the spring. It's one loans database now. The old reservations database is gone.
>
> Dan: One database, noted. Now the kiosks. The note says a member scans their card and their books at a kiosk in the branch.
>
> Priya: That is still true. The kiosks are in every branch and they send each loan and each return to the lending API as it is scanned. Most of the borrowing in a branch goes through them now, the desk mostly handles problems.
>
> Dan: Do the kiosks still write straight into the loans database, the way the old ones did?
>
> Priya: The kiosks are Dev's team's area. I couldn't tell you what they talk to these days. I only ever see what arrives at the API.
>
> Dan: I will chase Dev then. Is there anything else that can change a loan? Anything human?
>
> Priya: Desk staff can. When a member says they returned a book and the record says they did not, someone at the desk opens the circulation page and marks it returned, or clears the block on the account. Everyone at the desk uses the same shared login for the circulation page. The password is on a card taped under the desk.
>
> Dan: The same login for everyone? So if a loan is marked returned, can you tell me which person did it?
>
> Priya: You can tell it was the desk. You cannot tell who, or even which branch. It is one account, the page does not ask again, and the history just records that the loan was closed by hand. If a member swears a book went back and the book never turns up, we would be guessing between forty people.
>
> Dan: Understood. What about load? Does the service have quiet and busy times?
>
> Priya: The launch day of the summer reading challenge is when it falls over. It went down twice last summer. Every school sends its classes in the same week, every kiosk and every phone hits us at once, and the API is one service with no queue in front of it. When it goes down nobody can borrow or return anything, and the branches get the complaints.
>
> Dan: While we are on the API, what is it, technology-wise? The note calls it a plain REST service.
>
> Priya: It's a REST API. JSON in, JSON out. Nothing exotic, no message bus, no second protocol hiding anywhere.
>
> Dan: Any partners in the picture? Anyone outside the library who can touch loans?
>
> Priya: No. There has been talk for years, it comes back every planning round and dies every planning round. If we ever join the regional interlibrary scheme, we'd have to stand something up for the other libraries, but nothing like that exists today. Loans stay inside the service.
>
> Dan: Last one. If you could fix one thing on this service tomorrow, what would it be?
>
> Priya: The shared desk login. Second would be finding out what the API actually checks when a phone calls it, because if the answer is nothing much, the internet can reach it now and I would rather learn that from us than from someone else.
>
> Dan: That is a good place to stop. Thank you, Priya. I will write this up and send it to you to check.
>
> Priya: Send it to Dev's team too. The kiosks deserve their own hour.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:member | human | boundary:internet |
| entity:desk-staff | human | boundary:branch-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:lending-api | internet-facing | web | boundary:council-data-centre | a plain REST service, JSON in and JSON out |
| process:self-service-kiosk | unknown | unknown | boundary:branch-network | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:loans-database | boundary:council-data-centre | unknown | confidential |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:member>process:lending-api>borrow-renew-and-reserve | entity:member | process:lending-api | unknown | unknown | unknown |
| flow:process:self-service-kiosk>process:lending-api>record-loans-and-returns | process:self-service-kiosk | process:lending-api | unknown | unknown | unknown |
| flow:entity:desk-staff>process:lending-api>change-loans-by-hand | entity:desk-staff | process:lending-api | unknown | one login shared by all desk staff in every branch; the password is on a card taped under the desk | unknown |
| flow:process:lending-api>store:loans-database>read-and-write-loans | process:lending-api | store:loans-database | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:internet | network |
| boundary:branch-network | network |
| boundary:council-data-centre | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:lending-api` — The transcript corrects the note on exposure. Lending service note: "The lending API is internal-only." Interview transcript, Dan quotes that line and Priya answers: "That is one of the wrong bits. The phones talk straight to the lending API over the internet. We opened it up last year when the app moved off the council's old portal, and nobody updated the note because nobody owns the note." The note describes the service before that change, so exposure is internet-facing. Neither source states which operations a call from the internet can reach. Priya on load: "the API is one service with no queue in front of it"; it went down twice last summer on the reading challenge launch day.
- `process:self-service-kiosk` — What else the kiosks talk to was asked and not answered. Dan asked: "Do the kiosks still write straight into the loans database, the way the old ones did?" Priya: "The kiosks are Dev's team's area. I couldn't tell you what they talk to these days." A question states no fact, so the model holds no kiosk-to-database flow.
- `store:loans-database` — Priya corrected herself in one turn: "It writes to the two databases— actually, no. We merged those in the spring. It's one loans database now." The later statement stands, so the model holds one database.
- `flow:entity:member>process:lending-api>borrow-renew-and-reserve` — Authentication was asked about and answered with a hedge, so it stays unknown. Priya: "I think it checks a token the app gets at sign-in, but I'd have to look." and "Honestly I could not tell you today what it accepts, or what it does with a call it does not like."
- `flow:entity:desk-staff>process:lending-api>change-loans-by-hand` — Priya: "You can tell it was the desk. You cannot tell who, or even which branch." The history records only that the loan was closed by hand.

**Assumptions**

- `entity:member` — Members are placed on the internet because the schema requires a zone. (basis: Their phones reach the lending API over the internet; neither source states where members are.)

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


### authorization

**A1.** `V8.2.1` — The circulation page acts for one login every desk shares, so no access rule on it can name a person.

- `flow:entity:desk-staff>process:lending-api>change-loans-by-hand`, `process:lending-api`
- Stated outright: one login, no second check, a history with no person in it.

> mark:

**A2.** `V8.2.2` — Nothing states that the lending API lets a member change only their own loans and reservations.

- `flow:entity:member>process:lending-api>borrow-renew-and-reserve`, `process:lending-api`
- What the API checks on a call from the app is a hedge, so whether a call is tied to one member's records is open.

> mark:


### session-management

**A3.** `V7.2.1` — A token the app gets at sign-in is mentioned once, inside a hedge, and nothing says where it is verified.

- `flow:entity:member>process:lending-api>borrow-renew-and-reserve`, `process:lending-api`
- The one mention is a hedge, so it is not evidence either way.

> mark:


### secure-communication

**A4.** `V12.2.1` — Nothing states whether the connection between the app and the lending API uses TLS.

- `flow:entity:member>process:lending-api>borrow-renew-and-reserve`
- REST and JSON are stated and the transport never is. The transcript puts this flow on the internet.

> mark:


### authentication

**A5.** `V6.1.1` — Nothing documents what limits repeated sign-in attempts on the app or on the circulation page.

- `flow:entity:desk-staff>process:lending-api>change-loans-by-hand`, `flow:entity:member>process:lending-api>borrow-renew-and-reserve`
- Neither source describes a sign-in defence; a fuller description could answer it.

> mark:


### validation-and-business-logic

**A6.** `V2.2.2` — Nothing states that the lending API checks a loan or a return a kiosk sends, rather than trusting the kiosk.

- `flow:process:self-service-kiosk>process:lending-api>record-loans-and-returns`, `process:lending-api`
- How a kiosk is identified is also unknown, which is the spoofing claim on the same flow.

> mark:


### encoding-and-sanitization

**A7.** `V1.2.4` — The lending API reads and writes the loans database and nothing says how its queries are built.

- `flow:process:lending-api>store:loans-database>read-and-write-loans`, `process:lending-api`
- A property of the code, which neither source can settle.

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

**1.** If the lending API does not authenticate calls from the app, an attacker borrows, renews or reserves books as another member.

- `flow:entity:member>process:lending-api>borrow-renew-and-reserve`, `entity:member`
- severity: medium/medium · verb: `impersonate`
- The hedge this case exists for. Priya's answer about the token is a hedge, so authentication is unknown and the finding is conditional. An extraction that records the hedged token as the value suppresses this finding.

> mark:

**2.** If the lending API does not authenticate kiosks and check what each one may record, an attacker who reaches the kiosk operations forges loans and returns.

- `flow:process:self-service-kiosk>process:lending-api>record-loans-and-returns`, `process:self-service-kiosk`
- severity: medium/medium · verb: `impersonate`
- Neither source states how a kiosk authenticates to the API. The kiosks sit on the branch network, which is the network desk staff also use.

> mark:

**3.** An attacker who reads the shared desk password off the card taped under a desk signs in to the circulation page as desk staff.

- `flow:entity:desk-staff>process:lending-api>change-loans-by-hand`, `entity:desk-staff`
- severity: medium/medium · verb: `use-credential`
- Stated outright: the password is on a card under the desk. The likelihood depends on who stands behind a desk, which neither source states.

> mark:


### tampering

**4.** If the connection between a member's phone and the lending API has no effective authenticated encryption, an attacker on the path alters a renewal or a reservation in flight.

- `flow:entity:member>process:lending-api>borrow-renew-and-reserve`
- severity: low/low · verb: `alter-in-transit`
- encryption_in_transit is unknown on a flow the transcript says crosses the internet. The impact is one member's loan or reservation.

> mark:


### repudiation

**5.** A loan closed by hand or a block cleared at the desk cannot be traced to a person or a branch, because every desk shares one login and the history records only that the change was made by hand.

- `flow:entity:desk-staff>process:lending-api>change-loans-by-hand`, `entity:desk-staff`
- severity: high/medium · verb: `unattributable`
- Stated outright, twice: the shared login, and that nobody can tell who or which branch. The finding with the strongest grounds in the case.

> mark:

**6.** If the API does not keep trustworthy records tying renewals and reservations to authenticated members, a member could deny an action and the service may be unable to attribute it reliably.

- `flow:entity:member>process:lending-api>borrow-renew-and-reserve`, `entity:member`
- severity: medium/low · verb: `unattributable`
- Caller authentication and trustworthy records of each action are separate controls, and neither source states either one. The question differs from the spoofing claim: who acted, not who could act.

> mark:


### information-disclosure

**7.** If the loans database and its copies have no effective protection at rest, an attacker who obtains the storage reads every member's home address and full borrowing history.

- `store:loans-database`
- severity: low/high · verb: `read`
- encryption_at_rest is unknown on a store that holds home addresses and every member's reading history.

> mark:

**8.** If the connection between a member's phone and the lending API has no effective encryption, an attacker on the path reads which books a member borrows and reserves.

- `flow:entity:member>process:lending-api>borrow-renew-and-reserve`
- severity: medium/medium · verb: `intercept`
- No source states TLS on this flow. The transcript says the phones reach the API over the internet.

> mark:


### denial-of-service

**9.** An attacker floods the lending API with calls, and no member or kiosk can borrow or return anything, because ordinary launch-day load already took the one service down twice.

- `process:lending-api`, `flow:entity:member>process:lending-api>borrow-renew-and-reserve`
- severity: high/medium · verb: `flood`
- The source states this failure already happened twice last summer under legitimate load alone, and that every kiosk and every phone depends on the one API.

> mark:


### elevation-of-privilege

**10.** If a member or unauthenticated caller can reach the API's manual-return, block-clearing or extension operations and the API fails to enforce staff authorization, that caller could perform staff-only changes.

- `process:lending-api`, `flow:entity:member>process:lending-api>borrow-renew-and-reserve`
- severity: medium/high · verb: `escalate`
- The transcript puts the lending API on the internet, and the circulation page and the app's calls reach one API. Neither source states which operations a call from outside the branch network can reach, or a staff role check. An extraction that believes the superseded note and writes internal removes the internet caller from this finding.

> mark:


### tampering

**11.** A desk worker marks an unreturned book as returned, clears a justified block or grants an improper extension on purpose, through the circulation page, with the powers the desk already has.

- `flow:entity:desk-staff>process:lending-api>change-loans-by-hand`, `store:loans-database`
- severity: medium/high · verb: `alter`
- Desk staff hold these powers by the note, so this is misuse of a grant, not an escalation. The shared login stops the change being traced to a person or a branch, which is the repudiation claim on the same flow.

> mark:


### spoofing

**12.** If a scanned library card is all a kiosk checks, a person with another member's borrowed, stolen or copied card borrows books on that member's account.

- `process:self-service-kiosk`, `entity:member`
- severity: medium/low · verb: `use-credential`
- The note says a member scans their card and their books at a kiosk. Neither source states a second check.

> mark:


### information-disclosure

**13.** If the desk, kiosk or database connections of the lending API have no effective encryption, an attacker on one of those paths reads the circulation credentials or member data they carry.

- `flow:entity:desk-staff>process:lending-api>change-loans-by-hand`, `flow:process:self-service-kiosk>process:lending-api>record-loans-and-returns`, `flow:process:lending-api>store:loans-database>read-and-write-loans`
- severity: low/medium · verb: `intercept`
- encryption_in_transit is unknown on all three flows. The member flow is a separate claim.

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
    "16-library-lending-interview": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "8bc51704b4514584c02f95f04048086a70f43a3dbb2c40af53afe13b3f79e6a9",
      "transcript.md": "c48f8b9e53090b42971ffb548ed2302d0021a8c2b8539552973211c21df3f765",
      "model.json": "273a81ef4f52e7b84a64bdff2e15815383b51ccdf5d4def8a6e49f186f068563",
      "claims/asvs.json": "617a68c8e31065f8886e8b544ee4c67b63eaa7e43c769cd179d35b437443f8d7",
      "claims/stride.json": "87120ad43f66af05e1a80a1dcd9cc93bbbc794b7d9d9f1c154b60dd15f2c97cc"
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
