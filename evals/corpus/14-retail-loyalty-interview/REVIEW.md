# Review sitting — is `14-retail-loyalty-interview`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/14-retail-loyalty-interview`.

**Retail loyalty points platform, from a written note and a fresher interview that contradicts it** — domain `retail-loyalty`.

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

## Part 1 — the system, and your own list

### Loyalty platform note (description)

Exactly what the service would receive.

> Loyalty points platform, as written down.
>
> Customers collect and spend loyalty points through our mobile app. The points API is a plain REST service and it is the only way anything touches points. The points API is internal-only; the mobile app reaches it through the group's shared gateway.
>
> Stores have self-service kiosks where a customer who paid cash can scan a paper receipt to claim the points on it. The kiosks submit scanned receipts to the points API. The kiosks sit on the store network.
>
> Balances and the transaction history live in the points database. The points API and the points database run on the core network.
>
> Support can adjust a customer's balance by hand when something goes wrong, through an adjustments page the points API serves. Support work out of the Leeds office.
>
> This note is old in places. Priya on the platform team has the current picture; the interview transcript alongside is more recent than this note.

### Interview transcript (transcript)

Exactly what the service would receive.

> Dan: Thanks for making time, Priya. I am trying to get the loyalty points platform written down properly before the assessment. I have the old platform note in front of me, but I was told half of it has moved on.
>
> Priya: More than half, probably. That note has been wrong since the spring, and it was thin before that. People fix the platform and nobody fixes the paper. What do you want to start with?
>
> Dan: Start with how a customer actually earns points. Forget the diagrams, just walk me through what happens when someone buys something and taps their phone in the app.
>
> Priya: The app on their phone calls the points API. It sends what the customer bought and the API works out the points and writes the new balance. Redeeming is the same call in reverse, the app asks to spend points against a basket and the API says yes or no. From the customer's side it is one tap either way, all the arithmetic is ours.
>
> Dan: And how does the app reach the points API? The note says everything goes through the group's shared gateway.
>
> Priya: That is one of the wrong bits. The phones talk straight to the points API over the internet. The gateway went away last year, when the group platform team wound it down, and we took the direct route rather than build a replacement. Nobody updated the note because nobody owns the note.
>
> Dan: Straight to it. Alright. What does the points API check when a phone calls it? What stops me calling it as somebody else?
>
> Priya: I think it checks a token the app gets at sign-in, but I'd have to look. That code is older than my time on the team and I have never had a reason to open it. It has never come up in an incident, which is the only time we read anything old. Honestly I could not tell you today what it accepts, or what it does with a call it does not like.
>
> Dan: That is fine, an honest gap is more use to me than a guess. What is behind the API?
>
> Priya: The points database. Names, email addresses, balances and the full history of every collect and redeem live in the points database. The API is the only thing that reads or writes it. It writes to the two databases— actually, no. We merged those in the spring. It's one points database now. The old offers database is gone.
>
> Dan: One database, noted. Now the kiosks. The note says a customer who paid cash can scan a paper receipt at a kiosk in the store and claim the points that way.
>
> Priya: That is still true. The kiosks are in every store and they send each receipt to the points API as it is scanned. Cash customers are the whole reason they exist, there is no other way to claim off a paper receipt. The volume is small next to the app but it is steady, mostly older customers who will not install anything.
>
> Dan: Do the kiosks still write straight into the points database, the way the old fleet did?
>
> Priya: The kiosks are Dev's team's area. I couldn't tell you what they talk to these days. I only ever see what arrives at the API.
>
> Dan: I will chase Dev then. Is there anything else that can move a balance? Anything human?
>
> Priya: Support can. When a customer complains, someone on support opens the adjustments page and adds or removes points by hand. Anyone on support uses the same shared login for the adjustments page. It has been that way as long as I have been here, and the password moves around on sticky notes whenever someone new starts.
>
> Dan: The same login for the whole team? So if a balance is adjusted, can you tell me which person did it?
>
> Priya: You can tell it was support. You cannot tell who. It is one account, the page does not ask again, and the history just records that an adjustment happened and by how much. I have raised it before, it always loses to something louder. If a balance ever moves and a customer swears it was not them, we would be guessing between a dozen people.
>
> Dan: Understood. What about load? Does the platform have quiet and busy times?
>
> Priya: Double-points weekends are when it falls over. It has gone down twice this year. Marketing doubles the earn rate for a weekend, every till and every phone hits us at once, and the API is one service with no queue in front of it. When it goes down nobody can collect or spend anything, the app just spins, and the stores get the complaints because the customer is standing there.
>
> Dan: While we are on the API, what is it, technology-wise? The note calls it a plain REST service.
>
> Priya: It's a REST API. JSON in, JSON out. Nothing exotic, no message bus, no second protocol hiding anywhere. The dullness is deliberate, it is the one system here that has to be boring.
>
> Dan: Any partners in the picture? Anyone outside the company who can touch points?
>
> Priya: No. There has been talk for years, it comes back every planning round and dies every planning round. If we ever let the coffee chain redeem points in their app, we'd have to stand something up for them, but nothing like that exists today. Points stay inside the company, earned with us and spent with us.
>
> Dan: Two more and I will let you go. Where does support sit, physically and on the network?
>
> Priya: Leeds office, on the office network like everyone else there. They reach the adjustments page the same way they reach anything internal, there is nothing special about how support gets to it.
>
> Dan: Last one. If you could fix one thing on this platform tomorrow, what would it be?
>
> Priya: The shared support login. Second would be finding out what the API actually checks when a phone calls it, because if the answer is nothing much, the internet can reach it now and I would rather learn that from us than from someone else.
>
> Dan: That is a good place to stop. Thank you, Priya. I will write this up and send it to you to check.
>
> Priya: Send it to Dev's team too. The kiosks deserve their own hour.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:customer | human | boundary:public-internet |
| entity:support-agent | human | boundary:office-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:points-api | unknown | web | boundary:core-network | a plain REST service, JSON in and JSON out |
| process:store-kiosk | unknown | unknown | boundary:store-network | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:points-database | boundary:core-network | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:customer-to-points-api:collect-and-redeem-points | entity:customer | process:points-api | unknown | unknown | unknown |
| flow:store-kiosk-to-points-api:submit-scanned-receipts | process:store-kiosk | process:points-api | unknown | unknown | unknown |
| flow:points-api-to-points-database:read-and-write-points | process:points-api | store:points-database | unknown | unknown | unknown |
| flow:support-agent-to-points-api:adjust-points-by-hand | entity:support-agent | process:points-api | unknown | a single login shared by everyone on support; the page records that an adjustment happened, not who made it | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:store-network | network |
| boundary:core-network | network |
| boundary:office-network | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:points-api` — The two sources disagree on exposure and the value is flattened to unknown with both claims kept. Loyalty platform note: "The points API is internal-only; the mobile app reaches it through the group's shared gateway." Interview transcript, Priya: "The phones talk straight to the points API over the internet. The gateway went away last year". The transcript is stated to be more recent, but recency is not adjudication.
- `process:store-kiosk` — What the kiosks talk to was probed and not answered. Dan asked whether the kiosks still write straight into the points database; Priya: "The kiosks are Dev's team's area. I couldn't tell you what they talk to these days." The fact inside the question is not recorded as a fact: no kiosk-to-database flow exists in this model.
- `store:points-database` — Priya corrected herself mid-sentence: "It writes to the two databases— actually, no. We merged those in the spring. It's one points database now." The corrected statement stands; no second database exists in this model.
- `flow:customer-to-points-api:collect-and-redeem-points` — Authentication was probed and answered with a hedge, so it stays unknown. Priya: "I think it checks a token the app gets at sign-in, but I'd have to look." and "Honestly I could not tell you today what it accepts". A hedge is a speaker talking about a fact, not stating one.

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


### authentication

**A1.** `V6.2.1` — What the points API requires of a caller before acting on a balance is hedged, never stated.

- cites: `flow:customer-to-points-api:collect-and-redeem-points`, `process:points-api`
- tier: must-find
- recorded note: The hedge lands here as well as in STRIDE: the requirement applies and the input shows nothing satisfied.

> mark:


### session-management

**A2.** `V7.2.1` — A token the app gets at sign-in is mentioned once, as a guess, and nothing describes how it is generated or what it contains.

- cites: `process:points-api`
- tier: expected
- recorded note: The one mention is inside a hedge, which is what makes this an applicability ruling and not evidence either way.

> mark:


### secure-communication

**A3.** `V12.2.1` — Nothing states whether the connection between the app and the points API is encrypted in transit.

- cites: `flow:customer-to-points-api:collect-and-redeem-points`
- tier: must-find
- recorded note: REST and JSON are stated; the wire scheme never is, and the exposure dispute makes the path this rides even less certain.

> mark:


### authorization

**A4.** `V8.2.1` — The adjustments page acts for one shared identity, so no per-user authorization decision can exist on it.

- cites: `flow:support-agent-to-points-api:adjust-points-by-hand`, `process:points-api`
- tier: must-find
- recorded note: Stated outright rather than unknown: one login, no re-ask, history without a person.

> mark:

**A5.** `V8.1.1` — No documented authorization rules exist for who may adjust, collect or spend points.

- cites: `process:points-api`
- tier: expected
- recorded note: A documentation requirement; needs-info by construction, as with its counterpart in the control case.

> mark:


### validation-and-business-logic

**A6.** `V2.2.2` — Nothing says which side validates a scanned receipt before it becomes points.

- cites: `flow:store-kiosk-to-points-api:submit-scanned-receipts`, `process:points-api`
- tier: must-find
- recorded note: The forged-receipt threat's ASVS shape: a paper artifact enters the system and no stated control examines it.

> mark:


### data-protection

**A7.** `V14.2.1` — The points database holds names, email addresses and full purchase-linked history, and no protection for that data is described.

- cites: `store:points-database`
- tier: expected
- recorded note: The store's classification and at-rest protection are both unknown.

> mark:

## Part 3 — the 11 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a finding here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker calls the points API as another customer and collects or spends that customer's points, because what the API checks on a call from the app is unverified.

- cites: `flow:customer-to-points-api:collect-and-redeem-points`, `entity:customer`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: The hedge threat this case exists for: Priya's answer is a hedge, so authentication is unknown and needs-info is the right verdict. An extraction that records the hedged token as a value suppresses exactly this finding.

> mark:

**2.** An attacker presents itself to the points API as a store kiosk and submits receipt claims, because how a kiosk is identified to the API is unverified.

- cites: `flow:store-kiosk-to-points-api:submit-scanned-receipts`, `process:store-kiosk`
- tier: expected · severity: medium/medium · verb: `impersonate`
- recorded note: Nothing states how the API tells a kiosk from anything else on the store network, or off it.

> mark:


### tampering

**3.** An attacker scans forged or copied paper receipts at a kiosk and credits points for purchases that never happened.

- cites: `flow:store-kiosk-to-points-api:submit-scanned-receipts`, `process:store-kiosk`
- tier: must-find · severity: high/medium · verb: `forge`
- recorded note: The kiosk exists for cash customers, so the input is a paper artifact the platform cannot verify against a till record by anything either source states.

> mark:

**4.** An attacker who reaches the points database alters balances or rewrites history directly, bypassing the API's arithmetic.

- cites: `store:points-database`, `flow:points-api-to-points-database:read-and-write-points`
- tier: expected · severity: low/high · verb: `alter`
- recorded note: How the API authenticates to the database is unknown, and nothing else is stated to touch the store — which is exactly why direct access is the tampering path worth naming.

> mark:


### repudiation

**5.** A balance adjustment cannot be attributed to a person, because everyone on support shares one login and the history records only that an adjustment happened.

- cites: `flow:support-agent-to-points-api:adjust-points-by-hand`, `entity:support-agent`
- tier: must-find · severity: high/medium · verb: `unattributable`
- recorded note: Stated outright, twice: the shared login, and that the history does not say who. The strongest-grounded finding in the case.

> mark:

**6.** A customer denies a redemption and nothing can contradict them, because what the API verifies about the caller is unverified.

- cites: `flow:customer-to-points-api:collect-and-redeem-points`, `entity:customer`
- tier: expected · severity: medium/low · verb: `unattributable`
- recorded note: Follows from the same unknown as the spoofing must-find; kept separate because the harmed party and the question differ.

> mark:


### information-disclosure

**7.** An attacker who reaches the points database or its backups reads names, email addresses, balances and the full transaction history, because protection at rest is unverified.

- cites: `store:points-database`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: encryption_at_rest is unknown on a store holding pii and the scheme's whole history.

> mark:

**8.** An attacker positioned on the path between the app and the points API reads what customers buy and their balances, because transport protection is unverified.

- cites: `flow:customer-to-points-api:collect-and-redeem-points`
- tier: expected · severity: medium/medium · verb: `intercept`
- recorded note: Purchase contents ride this flow and nothing states TLS; the exposure dispute makes the path itself uncertain, which widens rather than narrows who could sit on it.

> mark:


### denial-of-service

**9.** An attacker floods the points API and no customer can collect or spend points, since it is one service with no queue in front of it.

- cites: `process:points-api`, `flow:customer-to-points-api:collect-and-redeem-points`
- tier: must-find · severity: high/medium · verb: `flood`
- recorded note: The source states the failure mode has already happened twice this year under legitimate load alone.

> mark:


### elevation-of-privilege

**10.** Anyone holding the shared support login adjusts any customer's balance at will, because nothing scopes what the adjustments page allows or reviews what it did.

- cites: `flow:support-agent-to-points-api:adjust-points-by-hand`, `store:points-database`
- tier: must-find · severity: medium/high · verb: `abuse-grant`
- recorded note: The credential is stated to move on sticky notes; the grant is the whole balance table and the control on it is one password.

> mark:

**11.** An attacker on the internet reaches the points API directly and drives it from outside every interior network, because whether the API is exposed to the internet is contradicted between the sources.

- cites: `process:points-api`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The conflict threat this case exists for: the note says internal-only, the transcript says direct over the internet, and exposure is flattened to unknown. A tool that silently believes either source misses either this finding or the truth.

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

**Counts first**, kept apart per framework: how many `agree`, `doubt`, `dup`
per part, and how many of your own items are missing from either set.

- **Few doubts, nothing important missing** — the sets hold, and the numbers
  measured against them have a standard behind them.
- **A whole class of attack missing** — the serious outcome. Recall is measured
  against these sets, so the tool has been scoring full marks for a gap nobody
  could see. Extend the set, and re-derive what was quoted against it.
- **Several doubts** — the sets overstate, inflating the denominator. Cheaper
  direction, still wrong.

**Then record the sign-off.** Add this to
`evals/corpus/14-retail-loyalty-interview/case.json`, which is what
`tests/test_case_review.py` reads:

```json
  "review": {
    "reviewer": "<your name or handle>",
    "date": "<YYYY-MM-DD>",
    "read": ["source.md", "transcript.md", "model.json", "claims/asvs.json", "claims/stride.json"],
    "notes": "<counts, and anything you changed>"
  },
```

If this case is named in `UNREVIEWED` in `tests/test_case_review.py`, delete
its line — the debt list is only honest if it shrinks when the debt is paid. A
case not named there is new, and merges with this block from the start.

`tests/test_case_review.py` checks that `read` covers every framework the case
declares, so every claims file above is required.
