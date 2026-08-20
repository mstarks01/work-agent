# Review sitting — is `06-cookbook-online-game`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/06-cookbook-online-game`.

**Online battle-royale game, player-facing flows** — domain `online-game`.

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

### System description (description)

Exactly what the service would receive.

> Online battle-royale game — player-facing flows.
>
> Players run our game client on their own machines. We do not control those
> machines. The player launches the client and plays through it.
>
> The client connects out to two things in our production network. It talks to
> the lobby on TCP 1234 for matchmaking, and once a match starts it talks
> directly to the game servers on TCP 1235. Both of those have to be reachable
> from wherever a player is, so they are exposed.
>
> The lobby reads the player database to set up a match, and hands the match over
> to the game servers. The game servers read and write the stats database during
> and after a match, and they also write back to the player database.
>
> Separately, our customer support staff work from the corporate network and use
> a moderation website to look at and act on player accounts. That website reads
> and writes the player database directly.
>
> The diagram doesn't record what authentication is on any of these links, or how
> any of the stores are protected. The two client links are just port numbers.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:player | human | boundary:player-local-machine |
| entity:customer-support | human | boundary:corp-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:game-client | unknown | non-web | boundary:player-local-machine | unknown |
| process:lobby | internet-facing | unknown | boundary:prod-network | unknown |
| process:game-servers | internet-facing | unknown | boundary:prod-network | unknown |
| process:moderation-website | unknown | web | boundary:prod-network | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:player-database | boundary:prod-network | unknown | unknown |
| store:stats-database | boundary:prod-network | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:player-to-game-client:launch-and-play | entity:player | process:game-client | local | unknown | unknown |
| flow:game-client-to-lobby:matchmaking | process:game-client | process:lobby | TCP 1234 | unknown | unknown |
| flow:game-client-to-game-servers:gameplay-traffic | process:game-client | process:game-servers | TCP 1235 | unknown | unknown |
| flow:lobby-to-game-servers:hand-over-match | process:lobby | process:game-servers | unknown | unknown | unknown |
| flow:lobby-to-player-database:read-players | process:lobby | store:player-database | unknown | unknown | unknown |
| flow:game-servers-to-stats-database:read-write-stats | process:game-servers | store:stats-database | unknown | unknown | unknown |
| flow:game-servers-to-player-database:update-players | process:game-servers | store:player-database | unknown | unknown | unknown |
| flow:customer-support-to-moderation-website:moderate-accounts | entity:customer-support | process:moderation-website | unknown | unknown | unknown |
| flow:moderation-website-to-player-database:read-write-players | process:moderation-website | store:player-database | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:player-local-machine | tenant |
| boundary:corp-network | network |
| boundary:prod-network | network |

**Assumptions**

- `process:lobby` — The lobby and the game servers accept connections from arbitrary networks. (basis: Stated to be reachable "from wherever a player is, so they are exposed".)
- `store:player-database` — Player records constitute personal data. (basis: Described as player accounts acted on by customer support moderation.)

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

## Part 2 — the 6 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### encoding-and-sanitization

**A1.** `V1.2.4` — The moderation website reads and writes the player database directly and nothing says how its queries are built.

- cites: `process:moderation-website`, `store:player-database`, `flow:moderation-website-to-player-database:read-write-players`
- tier: must-find
- recorded note: A process reaching a store is the fact that makes the requirement apply; the input settles nothing about query construction.

> mark:


### validation-and-business-logic

**A2.** `V2.2.2` — Nothing says which side validates the moderation actions support staff take on player accounts.

- cites: `entity:customer-support`, `process:moderation-website`
- tier: expected
- recorded note: The website acts on accounts on a reviewer's behalf, so the trusted-service-layer rule applies. Weaker than the store record because no interface detail is stated.

> mark:


### web-frontend-security

**A3.** `V3.3.1` — The moderation website is browser-delivered and no cookie attribute is stated.

- cites: `entity:customer-support`, `process:moderation-website`, `flow:customer-support-to-moderation-website:moderate-accounts`
- tier: expected
- recorded note: `interface_kind: web` puts this system in the chapter. Expected rather than must-find because the source names no session mechanism at all.

> mark:


### authentication

**A4.** `V6.1.1` — No documentation defines rate limiting or anti-automation for the moderation website.

- cites: `process:moderation-website`
- tier: expected
- recorded note: A documentation requirement: the subject sits outside the running system, so needs-info by construction.

> mark:


### authorization

**A5.** `V8.1.1` — No authorization documentation defines which support staff may act on which player accounts.

- cites: `process:moderation-website`
- tier: expected
- recorded note: Documentation requirement, as V6.1.1.

> mark:

**A6.** `V8.2.2` — Support staff act on player accounts and nothing restricts which accounts a given member of staff may reach.

- cites: `entity:customer-support`, `process:moderation-website`, `store:player-database`
- tier: must-find
- recorded note: Data-specific access over a store holding every player. The source says the diagram records no authentication on any link, so nothing settles it.

> mark:

## Part 3 — the 18 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a finding here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker connects to the exposed lobby port as another player, because how the lobby authenticates a client is unverified.

- cites: `flow:game-client-to-lobby:matchmaking`, `process:lobby`
- tier: must-find · severity: high/high · verb: `impersonate`
- recorded note: An internet-exposed port with unknown authentication is the highest-signal fact in the model.

> mark:

**2.** An attacker connects directly to a game server on its exposed port, bypassing the lobby, as a player who was never assigned to that match.

- cites: `flow:game-client-to-game-servers:gameplay-traffic`, `process:game-servers`
- tier: must-find · severity: high/high · verb: `impersonate`
- recorded note: The direct client-to-server path is a second entry point that skips whatever matchmaking establishes.

> mark:

**3.** An attacker reaches the moderation website posing as a support agent, since its authentication is unverified.

- cites: `flow:customer-support-to-moderation-website:moderate-accounts`, `process:moderation-website`
- tier: expected · severity: medium/high · verb: `impersonate`
- recorded note: A tool that can act on any player account; its access control is entirely unstated.

> mark:


### tampering

**4.** A player modifies the game client on their own machine and sends manipulated gameplay actions that the servers accept.

- cites: `process:game-client`, `flow:game-client-to-game-servers:gameplay-traffic`
- tier: must-find · severity: high/high · verb: `forge`
- recorded note: The defining threat of this domain: the client runs on hardware the operator explicitly does not control, so client-side state is attacker-controlled input.

> mark:

**5.** An attacker alters match statistics in the stats database to change rankings or rewards.

- cites: `store:stats-database`, `flow:game-servers-to-stats-database:read-write-stats`
- tier: must-find · severity: medium/medium · verb: `alter`
- recorded note: Competitive integrity is the business asset; write authentication on this path is unverified.

> mark:

**6.** An attacker who influences a game server writes fabricated progression onto player records.

- cites: `store:player-database`, `flow:game-servers-to-player-database:update-players`
- tier: expected · severity: medium/medium · verb: `forge`
- recorded note: Three separate writers reach this store, each with unverified authentication.

> mark:


### repudiation

**7.** A support agent's action on a player account cannot be attributed to them, because nothing records who performed a moderation change.

- cites: `process:moderation-website`, `store:player-database`
- tier: must-find · severity: medium/high · verb: `unattributable`
- recorded note: A privileged tool acting directly on records with no audit path described anywhere in the model.

> mark:

**8.** A player disputes a change to their record and no log distinguishes whether the lobby, a game server or the moderation website made it.

- cites: `store:player-database`, `process:game-servers`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: Multiple writers, one store, no recorded provenance.

> mark:


### information-disclosure

**9.** An attacker who reaches the player database reads player account data, whose protection at rest is unverified.

- cites: `store:player-database`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: Report as unverified; needs-info is an acceptable verdict given the model states nothing.

> mark:

**10.** An attacker on the network path reads matchmaking and gameplay traffic, including player identifiers, because encryption on both client links is unverified.

- cites: `flow:game-client-to-lobby:matchmaking`, `flow:game-client-to-game-servers:gameplay-traffic`
- tier: must-find · severity: medium/medium · verb: `intercept`
- recorded note: Both links are recorded as bare port numbers — the model gives no transport protection at all.

> mark:

**11.** A player extracts information from their own client that the server sends but should not reveal, such as other players' positions.

- cites: `process:game-client`, `process:game-servers`
- tier: expected · severity: high/medium · verb: `elicit`
- recorded note: Domain-specific and grounded in the untrusted-client fact; the classic wallhack shape.

> mark:

**12.** A support agent reads player account data beyond what a moderation decision requires, because the website's access to the database is unscoped.

- cites: `process:moderation-website`, `store:player-database`
- tier: expected · severity: medium/medium · verb: `abuse-grant`
- recorded note: Direct read/write to the store, with no narrower grant described.

> mark:


### denial-of-service

**13.** An attacker floods the exposed lobby until players can no longer be matched into games.

- cites: `process:lobby`, `flow:game-client-to-lobby:matchmaking`
- tier: must-find · severity: high/high · verb: `flood`
- recorded note: The lobby is a single availability-critical chokepoint that every session passes through.

> mark:

**14.** An attacker floods a game server's exposed port and disrupts a match in progress for every player in it.

- cites: `process:game-servers`, `flow:game-client-to-game-servers:gameplay-traffic`
- tier: must-find · severity: high/medium · verb: `flood`
- recorded note: Directly reachable match servers are the domain's signature availability problem; a disrupted match cannot be retried.

> mark:

**15.** An attacker drives enough matchmaking requests to exhaust the player database and stall both matchmaking and moderation.

- cites: `store:player-database`, `process:lobby`
- tier: expected · severity: medium/medium · verb: `flood`
- recorded note: One store shared by the player path and the staff path.

> mark:


### elevation-of-privilege

**16.** An attacker who compromises an internet-exposed game server gains write access to player records across the production network.

- cites: `process:game-servers`, `store:player-database`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The exposed element writes the sensitive store directly, with no intermediary and unverified authentication.

> mark:

**17.** An attacker who gets any access to the moderation website acquires privilege over every player account it can reach.

- cites: `process:moderation-website`, `entity:customer-support`
- tier: must-find · severity: medium/high · verb: `abuse-grant`
- recorded note: No role separation inside the tool is described; the blast radius is the whole player base.

> mark:

**18.** A player uses their control over the client to obtain a match assignment or account state they are not entitled to.

- cites: `process:game-client`, `process:lobby`
- tier: expected · severity: medium/medium · verb: `escalate`
- recorded note: Escalation framing of the untrusted-client fact, against the lobby rather than the game servers.

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
`evals/corpus/06-cookbook-online-game/case.json`, which is what
`tests/test_case_review.py` reads:

```json
  "review": {
    "reviewer": "<your name or handle>",
    "date": "<YYYY-MM-DD>",
    "read": ["source.md", "model.json", "claims/asvs.json", "claims/stride.json"],
    "notes": "<counts, and anything you changed>"
  },
```

If this case is named in `UNREVIEWED` in `tests/test_case_review.py`, delete
its line. That list names the cases nobody has read, so it is only accurate
while a reviewed case comes off it. A case not named there is new, and merges
with this block from the start.

`tests/test_case_review.py` checks that `read` covers every framework the case
declares, so every claims file above is required.
