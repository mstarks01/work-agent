# Review sitting — is `08-sso-identity-broker`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/08-sso-identity-broker`.

**Colleague sign-in through one identity broker, with authorization carried in token claims** — domain `identity-and-access`.

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

> Colleague sign-in and the identity broker.
>
> Everything colleagues use signs them in through one identity broker. The broker
> runs on the corporate network. Colleagues sign in to it from wherever they are,
> including their own devices at home.
>
> Once the broker has signed a colleague in it issues them a token. The token
> carries the colleague's staff id and a list of the groups they are in. Every
> application takes that token and decides what the colleague may do from the
> groups in it. Nothing calls back to the broker to ask whether a colleague is
> still allowed in.
>
> The store admin console is one of those applications. It also runs on the
> corporate network. If the token has the store-manager group in it, the console
> lets the holder change prices and void transactions. The console does not check
> which store the colleague belongs to, so a store-manager token works against
> every store.
>
> The broker signs tokens with a signing key it keeps in a key store. The same
> key signs the tokens for every application. Applications fetch the public half
> from the broker to check the signature.
>
> The broker keeps the colleagues and the groups they are in its own directory.
> Group membership is not maintained there by hand — it comes from the HR system
> overnight. The broker pulls the changes once a night, and that is also how
> leavers stop being colleagues. Tokens are good for twelve hours and there is no
> way to pull one back before it expires.
>
> We also let the franchise stores in. Franchise colleagues do not have staff
> accounts with us; their own identity provider vouches for them and the broker
> takes that as a sign-in. We have not written down which colleagues that provider
> is allowed to vouch for.
>
> The broker writes sign-ins to an audit log. Nobody has written down what that
> log records, whether it covers the franchise route, or whether the directory or
> the log are encrypted where they sit. Nor has anyone written down whether
> colleagues are asked for a second factor.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:colleague | human | boundary:outside-corporate-network |
| entity:franchise-identity-provider | external-system | boundary:franchise-partner |
| entity:hr-system | external-system | boundary:outside-corporate-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:identity-broker | internet-facing | web | boundary:corporate-network | unknown |
| process:store-admin-console | unknown | web | boundary:corporate-network | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:directory | boundary:corporate-network | unknown | unknown |
| store:key-store | boundary:corporate-network | unknown | unknown |
| store:audit-log | boundary:corporate-network | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:colleague-to-identity-broker:sign-in | entity:colleague | process:identity-broker | unknown | unknown | unknown |
| flow:franchise-identity-provider-to-identity-broker:vouch-for-colleague | entity:franchise-identity-provider | process:identity-broker | unknown | the broker accepts the provider's assertion as a sign-in; which colleagues the provider may vouch for is not written down | unknown |
| flow:colleague-to-store-admin-console:change-prices-and-void | entity:colleague | process:store-admin-console | unknown | the token alone: its store-manager group decides, the colleague's own store is never checked, and nothing calls back to the broker to re-check them | unknown |
| flow:store-admin-console-to-identity-broker:fetch-public-key | process:store-admin-console | process:identity-broker | unknown | unknown | unknown |
| flow:identity-broker-to-hr-system:nightly-group-pull | process:identity-broker | entity:hr-system | unknown | unknown | unknown |
| flow:identity-broker-to-directory:read-write-directory | process:identity-broker | store:directory | unknown | unknown | unknown |
| flow:identity-broker-to-key-store:read-signing-key | process:identity-broker | store:key-store | unknown | unknown | unknown |
| flow:identity-broker-to-audit-log:write-sign-ins | process:identity-broker | store:audit-log | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:corporate-network | network |
| boundary:outside-corporate-network | network |
| boundary:franchise-partner | tenant |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `entity:franchise-identity-provider` — Franchise colleagues themselves are not modelled: they have no account here, and how they reach their own provider is outside the system this model describes and unstated in the source.
- `process:store-admin-console` — The source describes a class of applications that all take the token and decide from the groups in it, and names this one. It is modelled as the relying application: a second, nameless one would duplicate every flow the console already carries and add no stated fact.

**Assumptions**

- `process:identity-broker` — The identity broker is reachable from outside the corporate network. (basis: Colleagues are stated to sign in to it from wherever they are, including their own devices at home, while it is stated to run on the corporate network.)
- `entity:franchise-identity-provider` — The franchise identity provider is a separate trust party from everywhere else outside the corporate network. (basis: The source introduces the franchise stores as a distinct group let in on distinct terms; it does not say where the provider runs, so the zone is drawn on the party rather than on a network.)
- `entity:hr-system` — The HR system sits outside the corporate network. (basis: The source names it as the system of record the broker pulls from and never places it; grouped with the other unplaced parties rather than assumed internal.)

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

## Part 2 — the 12 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### authentication

**A1.** `V6.1.1` — No documentation defines rate limiting or anti-automation on a sign-in colleagues reach from their own devices at home.

- cites: `entity:colleague`, `process:identity-broker`, `flow:colleague-to-identity-broker:sign-in`
- tier: expected
- recorded note: A documentation requirement: the subject sits outside the running system, so needs-info by construction.

> mark:


### session-management

**A2.** `V7.1.3` — The broker accepts a franchise provider's assertion as a sign-in and nothing documents the trust relationships in that federation.

- cites: `entity:franchise-identity-provider`, `process:identity-broker`, `flow:franchise-identity-provider-to-identity-broker:vouch-for-colleague`
- tier: expected
- recorded note: The source states the gap as which colleagues the provider may vouch for. This requirement asks for the documented relationship rather than the check itself.

> mark:

**A3.** `V7.4.2` — A leaver stops being a colleague on a nightly pull while their twelve-hour token stays usable, and nothing can end it early.

- cites: `entity:hr-system`, `process:identity-broker`, `flow:identity-broker-to-hr-system:nightly-group-pull`
- tier: must-find
- recorded note: The source states all three facts: nightly leaver processing, a twelve-hour lifetime, and no way to pull a token back. This requirement is the one they meet.

> mark:


### authorization

**A4.** `V8.2.2` — The console reads the store-manager group from the token and never checks which store the holder belongs to.

- cites: `entity:colleague`, `process:store-admin-console`, `flow:colleague-to-store-admin-console:change-prices-and-void`
- tier: must-find
- recorded note: The source states the defect outright: a store-manager token works against every store. Data-specific access is the requirement it fails.

> mark:

**A5.** `V8.4.1` — Franchise colleagues and staff colleagues share one console and one token format, with no control keeping one tenant's operations off another's.

- cites: `entity:franchise-identity-provider`, `entity:colleague`, `process:store-admin-console`
- tier: must-find
- recorded note: Two tenants are stated — the organization's own stores and the franchise stores — and the store check that would separate them is stated to be absent.

> mark:


### self-contained-tokens

**A6.** `V9.1.2` — Nothing states which signing algorithms the broker issues under or which an application will accept.

- cites: `process:identity-broker`, `store:key-store`
- tier: expected
- recorded note: Tokens are self-contained and signature-checked, so the chapter applies; the allowlist is what nothing settles.

> mark:

**A7.** `V9.2.4` — One signing key issues tokens for every application and nothing distinguishes the audience a token was issued for.

- cites: `process:identity-broker`, `process:store-admin-console`, `store:key-store`
- tier: must-find
- recorded note: The source states the shared key directly. This requirement is the one a shared issuing key lands on, which is why the case is scored at level 2.

> mark:


### cryptography

**A8.** `V11.4.1` — The broker signs every token and nothing states the hash function behind that signature.

- cites: `process:identity-broker`, `store:key-store`
- tier: expected
- recorded note: Signing is stated; the primitive is not.

> mark:


### secure-communication

**A9.** `V12.2.1` — Colleagues sign in from their own devices at home and no flow states its transport.

- cites: `entity:colleague`, `process:identity-broker`, `flow:colleague-to-identity-broker:sign-in`
- tier: must-find
- recorded note: The broker is stated to be reachable from outside and its transport is stated nowhere. Applicability comes from what the broker presents, not from this silence — see ADR 0014.

> mark:


### security-logging-and-error-handling

**A10.** `V16.1.1` — An audit log exists and nobody has written down what it records or whether it covers the franchise route.

- cites: `process:identity-broker`, `store:audit-log`, `flow:identity-broker-to-audit-log:write-sign-ins`
- tier: must-find
- recorded note: The source states the gap in the inventory's own terms, which is what this requirement asks for.

> mark:

**A11.** `V16.2.1` — Nothing states what metadata a sign-in entry carries, so nothing says an investigation could reconstruct one.

- cites: `store:audit-log`
- tier: expected
- recorded note: Follows V16.1.1: with no inventory there is nothing stating the fields.

> mark:


### data-protection

**A12.** `V14.1.1` — The directory, the audit log and the key store hold colleague and credential data and nothing classifies any of it.

- cites: `store:directory`, `store:audit-log`, `store:key-store`
- tier: expected
- recorded note: The source states nobody wrote down whether the directory or the log are encrypted where they sit, which is the downstream half of an absent classification.

> mark:

## Part 3 — the 23 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `reject` — overstated, unsupported by the text, or not really a finding here.
- `duplicate` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker holding the signing key mints a token for any staff id carrying any groups, and every application accepts it as a genuine sign-in.

- cites: `store:key-store`, `process:identity-broker`
- tier: must-find · severity: low/high · verb: `forge`
- recorded note: The source states one key signs the tokens for every application, so the key is the whole authorization system and not merely one application's.

> mark:

**2.** The franchise identity provider vouches for someone who is not a franchise colleague and the broker signs them in as that person, since nothing records which colleagues it may vouch for.

- cites: `flow:franchise-identity-provider-to-identity-broker:vouch-for-colleague`, `entity:franchise-identity-provider`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: A stated gap rather than an unknown: the source says outright that the restriction has not been written down, which is what makes this grounded rather than speculative.

> mark:

**3.** An attacker signs in to the broker as a colleague, because whether colleagues are asked for a second factor is unverified.

- cites: `flow:colleague-to-identity-broker:sign-in`, `entity:colleague`
- tier: expected · severity: medium/high · verb: `impersonate`
- recorded note: The front door, and explicitly undocumented in the closing paragraph; needs-info is the right verdict, not a confident finding either way.

> mark:

**4.** An attacker who obtains a colleague's token acts as that colleague for the rest of its twelve hours, because nothing calls back to the broker and there is no way to pull a token back.

- cites: `flow:colleague-to-identity-broker:sign-in`, `process:store-admin-console`
- tier: must-find · severity: medium/high · verb: `use-credential`
- recorded note: Two stated qualifiers compound here, and neither is an unknown; the twelve-hour window is the stated blast radius of every other spoofing claim in this case.

> mark:

**5.** An attacker serves the console a signing public key of their own so that tokens the attacker signed verify, since how the console fetches and trusts that key is unverified.

- cites: `flow:store-admin-console-to-identity-broker:fetch-public-key`, `process:store-admin-console`
- tier: expected · severity: low/high · verb: `forge`
- recorded note: The hinge of a claims-based design: the key fetch is what makes a signature mean anything, and the source describes it in one clause without saying how it is protected.

> mark:


### tampering

**6.** An attacker writes a group into the broker's directory and the next token issued to that colleague carries it into every application.

- cites: `store:directory`, `process:identity-broker`
- tier: must-find · severity: low/high · verb: `alter`
- recorded note: The directory is where authorization actually lives in this design; writing to it grants durable authority without touching a key or a token.

> mark:

**7.** An attacker alters what the nightly pull returns so that groups are granted, or so that leavers are never removed.

- cites: `flow:identity-broker-to-hr-system:nightly-group-pull`, `entity:hr-system`
- tier: expected · severity: low/high · verb: `alter-in-transit`
- recorded note: The pull crosses out of the corporate network to a system the source never locates, and it is the only stated path by which access is taken away.

> mark:

**8.** An attacker on the path to the console alters a colleague's price change or void in flight, since protection of that traffic is unverified.

- cites: `flow:colleague-to-store-admin-console:change-prices-and-void`
- tier: expected · severity: low/medium · verb: `alter-in-transit`
- recorded note: Kept distinct from the elevation claim on the same flow: this one is about altering a legitimate action, not about who is allowed to take it.

> mark:

**9.** An attacker alters or removes sign-in records in the audit log so a sign-in leaves no trace.

- cites: `store:audit-log`, `flow:identity-broker-to-audit-log:write-sign-ins`
- tier: expected · severity: low/medium · verb: `delete`
- recorded note: Filed here as an integrity claim against the store; the consequence for attribution is a separate reference in the repudiation lane.

> mark:


### repudiation

**10.** A franchise sign-in cannot be attributed to a person, because the identity was asserted by a provider we do not run and whether the log covers that route at all is unverified.

- cites: `flow:franchise-identity-provider-to-identity-broker:vouch-for-colleague`, `store:audit-log`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: The source raises the franchise coverage question itself, which is the clearest signal in the text that this is the attribution gap worth reporting.

> mark:

**11.** A price change or a void cannot be tied to the store it belongs to, because the console never establishes which store the token holder is from.

- cites: `process:store-admin-console`, `flow:colleague-to-store-admin-console:change-prices-and-void`
- tier: must-find · severity: high/medium · verb: `unattributable`
- recorded note: High likelihood because it is the described steady state rather than an attack condition, and it is the accountability face of the case's central authorization gap.

> mark:

**12.** A disputed sign-in may have no evidence behind it, since what the audit log records is unverified.

- cites: `store:audit-log`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: The existence of a log is stated and its contents are not; treating an unknown log as an adequate one is exactly the error the unknown value exists to prevent.

> mark:


### information-disclosure

**13.** An attacker who reaches the key store reads the signing key, since protection of what it holds at rest is unverified.

- cites: `store:key-store`
- tier: must-find · severity: low/high · verb: `recover-credential`
- recorded note: Recovering the key is a separate action from using it, and the corpus files the use under spoofing; one key for every application is what makes the recovery worth this severity.

> mark:

**14.** An attacker who reaches the directory reads colleague records and everyone's group membership, since protection at rest is unverified.

- cites: `store:directory`
- tier: expected · severity: low/medium · verb: `read`
- recorded note: Named as unverified in the source's closing line, and the group list doubles as a map of who is worth attacking.

> mark:

**15.** An attacker who reaches the audit log reads when and from where colleagues signed in, since protection at rest is unverified.

- cites: `store:audit-log`
- tier: expected · severity: low/low · verb: `read`
- recorded note: Paired with the directory in the same closing sentence; kept separate because they are different stores holding different things.

> mark:

**16.** An attacker on the path between a colleague and the broker reads the sign-in and the token that comes back, since protection of that traffic is unverified.

- cites: `flow:colleague-to-identity-broker:sign-in`
- tier: expected · severity: medium/high · verb: `intercept`
- recorded note: Colleagues are stated to sign in from their own devices at home, so this path is the least controlled one in the model and the token it carries is a bearer credential.

> mark:

**17.** An attacker on the path of the nightly pull reads colleague and leaver records as they cross out of the corporate network.

- cites: `flow:identity-broker-to-hr-system:nightly-group-pull`
- tier: expected · severity: low/medium · verb: `intercept`
- recorded note: A boundary crossing carrying personal data that the source describes without saying anything about how it is protected.

> mark:


### denial-of-service

**18.** An attacker makes the broker unavailable and no colleague can sign in to anything, because everything colleagues use signs them in through it.

- cites: `process:identity-broker`
- tier: must-find · severity: medium/high · verb: `disable`
- recorded note: The source's first sentence states the single point of failure, and the twelve-hour token is the only thing that softens it for colleagues already signed in.

> mark:

**19.** An attacker stops the nightly pull and group changes silently stop reaching the directory while everything else keeps working.

- cites: `flow:identity-broker-to-hr-system:nightly-group-pull`, `store:directory`
- tier: expected · severity: medium/medium · verb: `disable`
- recorded note: The failure is silent by construction: nothing in the described system reads the pull's success, so the first visible symptom is a leaver who still has access.

> mark:

**20.** An attacker destroys or replaces the signing key so that every application rejects every token at once.

- cites: `store:key-store`, `process:identity-broker`
- tier: expected · severity: low/high · verb: `delete`
- recorded note: The same single-key fact that makes the confidentiality claim severe makes this one estate-wide; kept distinct from reading the key, which is a different action.

> mark:


### elevation-of-privilege

**21.** A colleague who is a store manager in one store changes prices and voids transactions in every store, because the console decides from the group alone and never checks which store they belong to.

- cites: `process:store-admin-console`, `flow:colleague-to-store-admin-console:change-prices-and-void`
- tier: must-find · severity: high/high · verb: `abuse-grant`
- recorded note: The case's signature claim and the only one the source states as a completed fact rather than as a gap. High likelihood because no attacker step is required: a legitimate token already carries the authority.

> mark:

**22.** A leaver keeps their access until the nightly pull runs, and a token issued before it goes on working for twelve hours after that.

- cites: `flow:identity-broker-to-hr-system:nightly-group-pull`, `entity:colleague`
- tier: must-find · severity: high/high · verb: `abuse-grant`
- recorded note: Three stated facts compound into one window — nightly removal, twelve-hour tokens, no revocation — and no single sentence of the source contains it, which is what makes it the hardest claim in this case to reach.

> mark:

**23.** An attacker who controls the franchise provider reaches internal applications on the corporate network, because a sign-in it vouches for is treated like any other.

- cites: `entity:franchise-identity-provider`, `process:store-admin-console`
- tier: expected · severity: low/high · verb: `escalate`
- recorded note: The one crossing in this model where trust is granted to a party rather than to a network position, which is the shape this case exists to grade.

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
Append this entry to `reviews` in `evals/corpus/08-sso-identity-broker/case.json`, which is
what `tests/test_case_review.py` reads:

```json
  "reviews": [
    {
      "submitted_by": "<the GitHub login opening the PR>",
      "submitted_for": "<who read the case: a login, or the word anonymous>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "36115502847aa04640666b0dd9f458881e6f7f8968e4d499b58983b3403dc721"},
        {"file": "model.json", "sha256": "825c92681b885f7d36968302a5a38e904c206d9e5ba62936e754b4e3fb5ca6bb"},
        {"file": "claims/asvs.json", "sha256": "871f331895356803f29970c254fb2e418b8c23550f562cc63b234b28e8ad6340"},
        {"file": "claims/stride.json", "sha256": "9bd0d3203a0a3bcb8ca80ed0de9efbc1b1a35a6c621653e1206e65e19c3dd849"}
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
