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
| process:identity-broker | unknown | web | boundary:corporate-network | unknown |
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
| flow:entity:colleague>process:identity-broker>sign-in | entity:colleague | process:identity-broker | unknown | unknown | unknown |
| flow:entity:franchise-identity-provider>process:identity-broker>vouch-for-colleague | entity:franchise-identity-provider | process:identity-broker | unknown | the broker accepts the provider's assertion as a sign-in; which colleagues the provider may vouch for is not written down | unknown |
| flow:entity:colleague>process:store-admin-console>change-prices-and-void | entity:colleague | process:store-admin-console | unknown | the token alone: its store-manager group decides, the colleague's own store is never checked, and nothing calls back to the broker to re-check them | unknown |
| flow:process:store-admin-console>process:identity-broker>fetch-public-key | process:store-admin-console | process:identity-broker | unknown | unknown | unknown |
| flow:process:identity-broker>entity:hr-system>nightly-group-pull | process:identity-broker | entity:hr-system | unknown | unknown | unknown |
| flow:process:identity-broker>store:directory>read-write-directory | process:identity-broker | store:directory | unknown | unknown | unknown |
| flow:process:identity-broker>store:key-store>read-signing-key | process:identity-broker | store:key-store | unknown | unknown | unknown |
| flow:process:identity-broker>store:audit-log>write-sign-ins | process:identity-broker | store:audit-log | unknown | unknown | unknown |

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

- `entity:franchise-identity-provider` — The franchise identity provider is a separate trust party from everywhere else outside the corporate network. (basis: The source introduces the franchise stores as a distinct group let in on distinct terms; it does not say where the provider runs, so the zone is drawn on the party rather than on a network.)
- `entity:hr-system` — The HR system sits outside the corporate network. (basis: The source names it as the system of record the broker pulls from and never places it; grouped with the other unplaced parties rather than assumed internal.)
- `entity:franchise-identity-provider` — franchise identity provider's trust_zone is boundary:franchise-partner, which the schema requires and no source states. (basis: Their own identity provider establishes a distinct authority relationship. It does not establish the provider's physical network or hosting location.)
- `store:directory` — directory's trust_zone is boundary:corporate-network, which the schema requires and no source states. (basis: Its own directory establishes the broker's directory relationship, not the directory's network location.)
- `store:key-store` — key store's trust_zone is boundary:corporate-network, which the schema requires and no source states. (basis: The broker keeping its key in a key store does not locate that store. A remote key-management service is compatible with the description.)
- `store:audit-log` — audit log's trust_zone is boundary:corporate-network, which the schema requires and no source states. (basis: Writing to an audit log does not place its storage on the writer's network. Centralized or externally hosted logging remains possible.)
- `entity:colleague` — colleague's trust_zone is boundary:outside-corporate-network, which the schema requires and no source states. (basis: Home sign-ins support an external-origin scenario. They do not place all colleagues outside the corporate network or exclude VPN connectivity.)
- `entity:hr-system` — HR system sits in boundary:outside-corporate-network, which the schema requires and no source states. (basis: The source does not establish whether the HR system is inside or outside the corporate network. The zone is retained because the schema requires one.)

**Reviewed aliases** — other names a reader ruled identify the same element, each with the words in the source that support it. An extraction using one is named differently, not wrong.

- `entity:franchise-identity-provider — own identity provider` — Supported; the provider vouches for franchise colleagues. Source: > their own identity provider vouches for them
- `entity:franchise-identity-provider — identity provider` — The shorter name, where it is the provider that vouches for franchise colleagues to the broker. It stays distinct from the corporate broker; a separately extracted franchise colleague is a supported actor, not a duplicate. Ruled by the maintainer on 2026-09-16 (#961 step 6). Source: > their own identity provider vouches for them and the broker
- `boundary:franchise-partner — franchise stores` — The partner boundary is an organisational identity-trust boundary, not a network segment; 'franchise stores' represents it where it preserves that separation and the provider's affiliation. 'Home devices' does not. Ruled by the maintainer on 2026-09-16 (#961 step 6). Source: > We also let the franchise stores in.

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

**A1.** `V6.1.1` — The input carries no documentation of rate limiting or anti-automation on a sign-in colleagues reach from their own devices at home.

- `entity:colleague`, `process:identity-broker`, `flow:entity:colleague>process:identity-broker>sign-in`
- A documentation requirement: the subject sits outside the running system, so needs-info by construction.

> mark:


### session-management

**A2.** `V7.1.3` — The broker accepts a franchise provider's assertion as a sign-in and nothing documents the trust relationships in that federation.

- `entity:franchise-identity-provider`, `process:identity-broker`, `flow:entity:franchise-identity-provider>process:identity-broker>vouch-for-colleague`
- The source states the gap as which colleagues the provider may vouch for. This requirement asks for the documented relationship rather than the check itself.

> mark:

**A3.** `V7.4.2` — A leaver stops being a colleague on a nightly pull while their twelve-hour token stays usable, and nothing can end it early.

- `entity:hr-system`, `process:identity-broker`, `flow:process:identity-broker>entity:hr-system>nightly-group-pull`
- The source states all three facts: nightly leaver processing, a twelve-hour lifetime, and no way to pull a token back. This requirement is the one they meet.

> mark:


### authorization

**A4.** `V8.2.2` — The console reads the store-manager group from the token and never checks which store the holder belongs to.

- `entity:colleague`, `process:store-admin-console`, `flow:entity:colleague>process:store-admin-console>change-prices-and-void`
- The source states the defect outright: a store-manager token works against every store. Data-specific access is the requirement it fails.

> mark:

**A5.** `V8.4.1` — Franchise colleagues and staff colleagues share one console and one token format, with no control keeping one tenant's operations off another's.

- `entity:franchise-identity-provider`, `entity:colleague`, `process:store-admin-console`
- Two tenants are stated — the organization's own stores and the franchise stores — and the store check that would separate them is stated to be absent.

> mark:


### self-contained-tokens

**A6.** `V9.1.2` — Nothing states which signing algorithms the broker issues under or which an application will accept.

- `process:identity-broker`, `store:key-store`
- Tokens are self-contained and signature-checked, so the chapter applies; the allowlist is what nothing settles.

> mark:

**A7.** `V9.2.4` — One signing key issues tokens for every application, and nothing states whether a token carries an audience restriction or whether an application checks one.

- `process:identity-broker`, `process:store-admin-console`, `store:key-store`
- The source states the shared key directly, which is what makes this requirement apply. The audience claim and its check are code facts the description does not carry.

> mark:


### cryptography

**A8.** `V11.4.1` — The broker signs every token and nothing states the hash function behind that signature.

- `process:identity-broker`, `store:key-store`
- Signing is stated; the primitive is not.

> mark:


### secure-communication

**A9.** `V12.2.1` — Colleagues sign in from their own devices at home and no flow states its transport.

- `entity:colleague`, `process:identity-broker`, `flow:entity:colleague>process:identity-broker>sign-in`
- The broker is stated to be reachable from outside and its transport is stated nowhere. Applicability comes from what the broker presents, not from this silence — see ADR 0014. The submitter can state what protects this link, and in this corpus they do: case 05 states transport absent on every internal link and that reference reads gap-from-prose. A property a description settles when it is written down is settled by a description when it is not, so the primary route is prose and the configuration is the alternate.

> mark:


### security-logging-and-error-handling

**A10.** `V16.1.1` — An audit log exists and the input carries nothing written down about what it records or whether it covers the franchise route.

- `process:identity-broker`, `store:audit-log`, `flow:process:identity-broker>store:audit-log>write-sign-ins`
- The source states the gap in the inventory's own terms, which is what this requirement asks for.

> mark:

**A11.** `V16.2.1` — Nothing states what metadata a sign-in entry carries, so nothing says an investigation could reconstruct one.

- `store:audit-log`
- Follows V16.1.1: with no inventory there is nothing stating the fields. The fields on an entry come from the call site or from the logging configuration, so either route settles it.

> mark:


### data-protection

**A12.** `V14.1.1` — The directory, the audit log and the key store hold colleague and credential data and nothing classifies any of it.

- `store:directory`, `store:audit-log`, `store:key-store`
- The source states nobody wrote down whether the directory or the log are encrypted where they sit, which is the downstream half of an absent classification.

> mark:

## Part 3 — the 21 recorded STRIDE threats

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

**1.** An attacker holding the signing key mints a token for any staff id carrying any groups, and every application accepts it as a genuine sign-in.

- `store:key-store`, `process:identity-broker`
- severity: low/high · verb: `forge`
- The source states one key signs the tokens for every application, so the key is the whole authorization system and not merely one application's.

> mark:

**2.** An attacker signs in to the broker as a colleague, because whether colleagues are asked for a second factor is unverified.

- `flow:entity:colleague>process:identity-broker>sign-in`, `entity:colleague`
- severity: medium/high · verb: `impersonate`
- The front door, and explicitly undocumented in the closing paragraph; needs-info is the right verdict, not a confident finding either way.

> mark:

**3.** An attacker who obtains a colleague's token acts as that colleague for the rest of its twelve hours, because nothing calls back to the broker and there is no way to pull a token back.

- `flow:entity:colleague>process:identity-broker>sign-in`, `process:store-admin-console`
- severity: medium/high · verb: `use-credential`
- Two stated qualifiers compound here, and neither is an unknown; the twelve-hour window is the stated blast radius of every other spoofing claim in this case.

> mark:

**4.** An attacker serves the console a signing public key of their own so that tokens the attacker signed verify, since how the console fetches and trusts that key is unverified.

- `flow:process:store-admin-console>process:identity-broker>fetch-public-key`, `process:store-admin-console`
- severity: low/high · verb: `forge`
- The hinge of a claims-based design: the key fetch is what makes a signature mean anything, and the source describes it in one clause without saying how it is protected.

> mark:


### tampering

**5.** An attacker writes a group into the broker's directory and the next token issued to that colleague carries it into every application.

- `store:directory`, `process:identity-broker`
- severity: low/high · verb: `alter`
- The directory is where authorization actually lives in this design; writing to it grants durable authority without touching a key or a token.

> mark:

**6.** An attacker alters what the nightly pull returns so that groups are granted, or so that leavers are never removed.

- `flow:process:identity-broker>entity:hr-system>nightly-group-pull`, `entity:hr-system`
- severity: low/high · verb: `alter-in-transit`
- The pull crosses out of the corporate network to a system the source never locates, and it is the only stated path by which access is taken away.

> mark:

**7.** An attacker on the path to the console alters a colleague's price change or void in flight, since protection of that traffic is unverified.

- `flow:entity:colleague>process:store-admin-console>change-prices-and-void`
- severity: low/medium · verb: `alter-in-transit`
- Kept distinct from the elevation claim on the same flow: this one is about altering a legitimate action, not about who is allowed to take it.

> mark:

**8.** An attacker alters or removes sign-in records in the audit log so a sign-in leaves no trace.

- `store:audit-log`, `flow:process:identity-broker>store:audit-log>write-sign-ins`
- severity: low/medium · verb: `delete`
- Filed here as an integrity claim against the store; the consequence for attribution is a separate reference in the repudiation lane.

> mark:


### repudiation

**9.** A franchise sign-in cannot be attributed to a person, because the identity was asserted by a provider we do not run and whether the log covers that route at all is unverified.

- `flow:entity:franchise-identity-provider>process:identity-broker>vouch-for-colleague`, `store:audit-log`
- severity: medium/medium · verb: `unattributable`
- The source raises the franchise coverage question itself, which is the clearest signal in the text that this is the attribution gap worth reporting.

> mark:

**10.** A disputed sign-in may have no evidence behind it, since what the audit log records is unverified.

- `store:audit-log`
- severity: medium/medium · verb: `unattributable`
- The existence of a log is stated and its contents are not; treating an unknown log as an adequate one is exactly the error the unknown value exists to prevent.

> mark:


### information-disclosure

**11.** An attacker who reaches the key store reads the signing key, since protection of what it holds at rest is unverified.

- `store:key-store`
- severity: low/high · verb: `recover-credential`
- Recovering the key is a separate action from using it, and the corpus files the use under spoofing; one key for every application is what makes the recovery worth this severity.

> mark:

**12.** An attacker who reaches the directory reads colleague records and everyone's group membership, since protection at rest is unverified.

- `store:directory`
- severity: low/medium · verb: `read`
- Named as unverified in the source's closing line, and the group list doubles as a map of who is worth attacking.

> mark:

**13.** An attacker who reaches the audit log reads whatever it records of colleague sign-ins, and the source says outright that nobody wrote down what it records or whether it is protected where it sits.

- `store:audit-log`
- severity: low/low · verb: `read`
- Paired with the directory in the same closing sentence; kept separate because they are different stores holding different things. The source states the gap in its own words, so the unknown contents are the finding rather than an assumption about them.

> mark:

**14.** An attacker on the path between a colleague and the broker reads the sign-in and the token that comes back, since protection of that traffic is unverified.

- `flow:entity:colleague>process:identity-broker>sign-in`
- severity: medium/high · verb: `intercept`
- Colleagues are stated to sign in from their own devices at home, so this path is the least controlled one in the model and the token it carries is a bearer credential.

> mark:

**15.** An attacker on the path of the nightly pull reads colleague and leaver records as they cross out of the corporate network.

- `flow:process:identity-broker>entity:hr-system>nightly-group-pull`
- severity: low/medium · verb: `intercept`
- A boundary crossing carrying personal data that the source describes without saying anything about how it is protected.

> mark:


### denial-of-service

**16.** An attacker makes the broker unavailable and no colleague can sign in to anything, because everything colleagues use signs them in through it.

- `process:identity-broker`
- severity: medium/high · verb: `disable`
- The source's first sentence states the single point of failure, and the twelve-hour token is the only thing that softens it for colleagues already signed in.

> mark:

**17.** An attacker stops the nightly pull and group changes silently stop reaching the directory while everything else keeps working.

- `flow:process:identity-broker>entity:hr-system>nightly-group-pull`, `store:directory`
- severity: medium/medium · verb: `disable`
- The failure is silent by construction: nothing in the described system reads the pull's success, so the first visible symptom is a leaver who still has access.

> mark:

**18.** An attacker destroys the one signing key and the broker issues no new token for any application until the key is replaced, and nothing records how a replacement would reach the applications.

- `store:key-store`, `process:identity-broker`
- severity: low/high · verb: `delete`
- The same single-key fact that makes the confidentiality claim severe makes this one estate-wide; kept distinct from reading the key, which is a different action. Already-issued tokens keep verifying against a cached public half, so the loss is new issuance rather than instant estate-wide rejection.

> mark:


### elevation-of-privilege

**19.** A colleague who is a store manager in one store changes prices and voids transactions in every store, because the console decides from the group alone and never checks which store they belong to.

- `process:store-admin-console`, `flow:entity:colleague>process:store-admin-console>change-prices-and-void`
- severity: high/high · verb: `abuse-grant`
- The case's signature claim and the only one the source states as a completed fact rather than as a gap. High likelihood because no attacker step is required: a legitimate token already carries the authority.

> mark:

**20.** A leaver keeps their access until the nightly pull runs, and a token issued before it goes on working for twelve hours after that.

- `flow:process:identity-broker>entity:hr-system>nightly-group-pull`, `entity:colleague`
- severity: high/high · verb: `abuse-grant`
- Three stated facts compound into one window — nightly removal, twelve-hour tokens, no revocation — and no single sentence of the source contains it, which is what makes it the hardest claim in this case to reach.

> mark:


### spoofing

**21.** An abused franchise identity provider vouches for an identity outside its permitted scope, if the broker does not constrain which identities a franchise may assert.

- `entity:franchise-identity-provider`, `process:identity-broker`, `flow:entity:franchise-identity-provider>process:identity-broker>vouch-for-colleague`
- severity: low/medium · verb: `forge`
- Drafted from Baseline 6bff717-gpt-5.6-terra-24dda4db draft S-03, ruled a relevant threat scenario by the maintainer on 2026-09-20 (audit QA-2026-09-20-01). Conditional. The broker accepts franchise assertions and the permitted identity scope is undocumented, so an abused provider could assert an unauthorized identity where the broker fails to constrain that scope. It is a distinct mechanism from reference 1, which it does not satisfy.

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
  "generated": "<YYYY-MM-DD>",
  "cases": {
    "08-sso-identity-broker": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "36115502847aa04640666b0dd9f458881e6f7f8968e4d499b58983b3403dc721",
      "model.json": "963ee0bee098fc3d83caa04610b2967c3a61ccc650e7fbd18b47b6b756354163",
      "claims/asvs.json": "5fbdf49a4d299d911b4597850d39d5ac6a8ddfb751fc2de23a668c6001cb171a",
      "claims/stride.json": "7dfe3e0a09ce18f512e75a49d4ae8f347a9be19d7bc24fbc87abf4b450ba2ff1"
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
