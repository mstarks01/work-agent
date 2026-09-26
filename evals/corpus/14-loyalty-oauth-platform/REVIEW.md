# Review sitting — is `14-loyalty-oauth-platform`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/14-loyalty-oauth-platform`.

**A loyalty platform's own OAuth authorization server, serving a public mobile client and confidential partner apps** — domain `identity-and-access`.

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

> Loyalty rewards platform and partner API.
>
> We run a grocery loyalty programme. Members collect points when they shop and
> spend them on gift cards. Everything below runs in our own cloud account, apart
> from the members' phones and the partner apps.
>
> Members use our mobile app. The app signs a member in through our authorization
> server with the authorization code flow and then calls the rewards API with the
> access token it gets back. Nobody wrote down whether the app sends a PKCE
> challenge. The app keeps a refresh token on the phone. A mobile refresh token
> never expires: every time the app uses it, its expiry slides forward another
> ninety days.
>
> Partner apps are web applications that other companies run on their own
> servers, such as a meal-planning service that shows a member's points. Our
> partnerships team registers each partner app by hand and gives it a client ID
> and a client secret. The partner app sends that secret in the body of its token
> requests. When the team registers a redirect URI for a partner app, the
> authorization server accepts any redirect URI that starts with it. The team
> gives every partner app every scope, because working out which scopes each
> partner needs took too long.
>
> A member who connects a partner app signs in on the authorization server's
> sign-in page in their browser and approves a consent screen that lists the
> scopes. The browser then returns to the partner app's redirect URI with an
> authorization code. An authorization code stays valid for ten minutes. The
> member's sign-in on the authorization server is kept in a cookie for thirty
> days. Nobody decided an idle timeout for it.
>
> The authorization server keeps member accounts, email addresses and password
> hashes in the identity database, together with the authorization codes, refresh
> tokens and consents it issues. Members can disconnect a partner app from the
> settings screen of the mobile app, which deletes that app's refresh token from
> the identity database. An access token that was already issued keeps working
> until it expires.
>
> Access tokens are signed JWTs that live for one hour, and they are plain bearer
> tokens. The rewards API checks the signature with a public key it downloads
> when it starts. It does not check which client or which audience a token was
> issued for. The authorization server signs tokens for the support console with
> the same key.
>
> The rewards API reads and writes the points ledger, which holds every member's
> balance and every points transaction. Redeeming points for a gift card needs
> only a valid access token that carries the redeem scope. The ledger records
> which client made each change, and not which member approved it. The rewards
> API also writes every request it receives, headers included, to the API log
> store.
>
> The support console is a web app that closes a member's account. It takes access tokens from the same authorization server, and it is
> served on the same public hostname as the rewards API. Closing an account marks
> it closed in the identity database. Nothing happens to that member's refresh
> tokens, which sit in the same database.
>
> Every connection from a phone, a browser or a partner server uses HTTPS. Nobody
> recorded how the rewards API connects to the ledger, how the support console
> connects to the identity database, or how any of the three stores is protected
> at rest.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:member | human | boundary:member-devices |
| entity:partner-app | external-system | boundary:partner-servers |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:mobile-app | unknown | unknown | boundary:member-devices | mobile app; OAuth public client using the authorization code flow |
| process:authorization-server | internet-facing | web | boundary:rewards-cloud | OAuth authorization server issuing signed JWT access tokens |
| process:rewards-api | internet-facing | web | boundary:rewards-cloud | API that validates JWT signatures only |
| process:support-console | internet-facing | web | boundary:rewards-cloud | web app |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:identity-database | boundary:rewards-cloud | unknown | unknown |
| store:points-ledger | boundary:rewards-cloud | unknown | unknown |
| store:api-log-store | boundary:rewards-cloud | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:member>process:authorization-server>sign-in-and-consent | entity:member | process:authorization-server | HTTPS | password; no idle timeout | HTTPS |
| flow:entity:member>entity:partner-app>return-with-authorization-code | entity:member | entity:partner-app | HTTPS | none; redirect URI matched by prefix | HTTPS |
| flow:process:mobile-app>process:authorization-server>token-requests | process:mobile-app | process:authorization-server | HTTPS | refresh token never expires | HTTPS |
| flow:entity:partner-app>process:authorization-server>token-requests | entity:partner-app | process:authorization-server | HTTPS | client secret | HTTPS |
| flow:process:mobile-app>process:rewards-api>call-rewards-api | process:mobile-app | process:rewards-api | HTTPS | bearer JWT; audience not checked | HTTPS |
| flow:entity:partner-app>process:rewards-api>call-rewards-api | entity:partner-app | process:rewards-api | HTTPS | bearer JWT; audience not checked | HTTPS |
| flow:process:authorization-server>store:identity-database>read-and-write-accounts-and-grants | process:authorization-server | store:identity-database | unknown | unknown | unknown |
| flow:process:rewards-api>store:points-ledger>read-and-write-points | process:rewards-api | store:points-ledger | unknown | unknown | unknown |
| flow:process:rewards-api>store:api-log-store>write-request-log | process:rewards-api | store:api-log-store | unknown | unknown | unknown |
| flow:process:support-console>store:identity-database>close-accounts | process:support-console | store:identity-database | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:rewards-cloud | network |
| boundary:member-devices | network |
| boundary:partner-servers | tenant |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:authorization-server` — Authorization codes stay valid for ten minutes. A member's sign-in is kept in a cookie for thirty days, and nobody decided an idle timeout.
- `store:identity-database` — The source states nobody recorded how any of the three stores is protected at rest.
- `store:points-ledger` — The source states nobody recorded how any of the three stores is protected at rest.
- `store:api-log-store` — The source states nobody recorded how any of the three stores is protected at rest.
- `flow:entity:member>entity:partner-app>return-with-authorization-code` — An authorization code stays valid for ten minutes.
- `flow:process:mobile-app>process:authorization-server>token-requests` — A public client. The source states nobody wrote down whether it sends a PKCE challenge, and that each use slides the refresh token's expiry forward ninety days.
- `flow:entity:partner-app>process:authorization-server>token-requests` — The source states the partner app sends its client secret in the body of its token requests.
- `flow:process:rewards-api>store:points-ledger>read-and-write-points` — The source states nobody recorded how the rewards API connects to the ledger.
- `flow:process:support-console>store:identity-database>close-accounts` — The source states nobody recorded how the support console connects to the identity database.

**Assumptions**

- `entity:member` — The member is placed on the member devices zone because the schema requires a zone for an actor who uses a phone and a browser. (basis: The source places the members' phones outside our account and states members use the app and a browser; it places the person nowhere.)
- `process:authorization-server` — The authorization server is reachable from the internet. (basis: Members' browsers and partner servers of other companies connect to it over HTTPS; the source states no exposure directly.)
- `process:rewards-api` — The rewards API is reachable from the internet. (basis: Phones and partner servers call it, and the support console shares its public hostname; the source states no exposure directly.)

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

## Part 2 — the 18 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### oauth-and-oidc

**A1.** `V10.4.1` — The authorization server matches a partner's redirect URI by prefix rather than by exact comparison.

- `process:authorization-server`, `flow:entity:member>entity:partner-app>return-with-authorization-code`
- Stated outright; the case's first OAuth defect.

> mark:

**A2.** `V10.4.3` — Authorization codes stay valid for ten minutes, longer than a level 3 system allows.

- `process:authorization-server`, `flow:entity:member>entity:partner-app>return-with-authorization-code`
- The level is what makes this a gap: ten minutes is within the level 1 and 2 bound and outside the level 3 one.

> mark:

**A3.** `V10.4.6` — Nothing states whether the code flow requires a PKCE challenge.

- `process:authorization-server`, `flow:process:mobile-app>process:authorization-server>token-requests`, `process:mobile-app`
- The source says nobody wrote it down. Whether the authorization server requires it is a setting, so a fuller description would not settle it better than the configuration.

> mark:

**A4.** `V10.4.8` — A mobile refresh token has no absolute expiry: each use slides it forward ninety days.

- `process:authorization-server`, `flow:process:mobile-app>process:authorization-server>token-requests`
- Stated outright.

> mark:

**A5.** `V10.4.5` — Nothing states how the authorization server stops a copied refresh token of the public mobile client being replayed.

- `process:authorization-server`, `flow:process:mobile-app>process:authorization-server>token-requests`, `process:mobile-app`
- Rotation or sender constraint would be a server setting. The source states only the sliding expiry.

> mark:

**A6.** `V10.4.11` — Every partner app is assigned every scope rather than the scopes it needs.

- `process:authorization-server`, `entity:partner-app`
- Stated outright, with the reason.

> mark:

**A7.** `V10.2.3` — Nothing states which scopes a partner app asks for in its authorization requests.

- `entity:partner-app`, `flow:entity:partner-app>process:authorization-server>token-requests`
- A property of each partner's own client code, which we do not run.

> mark:

**A8.** `V10.4.16` — Partner apps authenticate with a client secret in the request body rather than a public-key method.

- `process:authorization-server`, `flow:entity:partner-app>process:authorization-server>token-requests`
- A level 3 requirement; the stated secret settles it.

> mark:

**A9.** `V10.3.1` — The rewards API does not check that an access token was issued for it.

- `process:rewards-api`, `flow:entity:partner-app>process:rewards-api>call-rewards-api`, `flow:process:mobile-app>process:rewards-api>call-rewards-api`
- Stated outright: the API checks the signature and not the audience.

> mark:

**A10.** `V10.3.5` — Access tokens are plain bearer tokens, so the rewards API cannot tell a stolen token from its holder's.

- `process:rewards-api`, `flow:entity:partner-app>process:rewards-api>call-rewards-api`, `flow:process:mobile-app>process:rewards-api>call-rewards-api`
- A level 3 requirement; 'plain bearer tokens' settles it.

> mark:

**A11.** `V10.4.14` — The authorization server issues bearer access tokens rather than sender-constrained ones.

- `process:authorization-server`
- The server-side half of V10.3.5.

> mark:


### self-contained-tokens

**A12.** `V9.2.3` — Neither the rewards API nor the support console restricts the tokens it accepts to the ones meant for it.

- `process:rewards-api`, `process:support-console`
- Stated for the rewards API; the console shares the key and the source states no check there either.

> mark:

**A13.** `V9.2.4` — One signing key issues tokens for both the rewards API and the support console, and nothing states that the tokens carry an audience that tells them apart.

- `process:authorization-server`, `process:rewards-api`, `process:support-console`
- The shared key is stated; whether the issued tokens name an audience is a property of the token the source does not describe.

> mark:

**A14.** `V9.1.3` — The rewards API downloads its token-verification key at start-up, and nothing states where from or how that source is trusted.

- `process:rewards-api`
- A deployed setting.

> mark:


### session-management

**A15.** `V7.3.1` — The authorization server's sign-in session has no idle timeout.

- `process:authorization-server`, `flow:entity:member>process:authorization-server>sign-in-and-consent`
- Stated outright: nobody decided one.

> mark:

**A16.** `V7.4.2` — Closing a member's account leaves that member's refresh tokens usable.

- `process:support-console`, `store:identity-database`
- Stated outright: nothing happens to the grant store on closure.

> mark:

**A17.** `V7.5.3` — Redeeming points for a gift card asks for nothing beyond a token with the redeem scope.

- `process:rewards-api`, `flow:entity:partner-app>process:rewards-api>call-rewards-api`, `flow:process:mobile-app>process:rewards-api>call-rewards-api`
- A level 3 requirement. Redemption turns points into money, and the source states the whole of what it checks.

> mark:


### security-logging-and-error-handling

**A18.** `V16.2.5` — The rewards API logs every request with its headers, so live bearer tokens are written to the log store.

- `process:rewards-api`, `store:api-log-store`
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

**1.** An attacker who copies a member's mobile refresh token from the phone keeps getting access tokens as that member indefinitely, because the token's expiry slides forward on every use.

- `flow:process:mobile-app>process:authorization-server>token-requests`, `process:mobile-app`, `process:authorization-server`
- severity: medium/high · verb: `use-credential`
- Stated outright: the refresh token never expires and slides ninety days on each use. Nothing the source states bounds the window, and nothing detects a second holder.

> mark:

**2.** A malicious app on the member's phone intercepts the mobile app's authorization code and redeems it as the mobile app, if the app sends no PKCE challenge.

- `flow:process:mobile-app>process:authorization-server>token-requests`, `process:mobile-app`
- severity: medium/high · verb: `use-credential`
- Conditional on the unrecorded PKCE. A public client has no secret, so the code is the whole credential at the token endpoint.

> mark:

**3.** An attacker who obtains a partner app's static client secret requests tokens from the authorization server as that partner app.

- `flow:entity:partner-app>process:authorization-server>token-requests`, `entity:partner-app`, `process:authorization-server`
- severity: low/high · verb: `use-credential`
- A shared secret sent in a request body is the partner's only proof of identity. Low likelihood: the source says nothing of how the secret is kept or leaked.

> mark:

**4.** An attacker guesses members' passwords on the authorization server's sign-in page and signs in as them.

- `flow:entity:member>process:authorization-server>sign-in-and-consent`, `process:authorization-server`
- severity: medium/medium · verb: `guess-credential`
- The sign-in is a password alone and the source states no lockout or rate limit. An expected finding, not a must-find: the silence is ordinary, not a stated gap.

> mark:


### information-disclosure

**5.** An attacker who controls a URL that begins with a partner's registered redirect URI has the authorization server send members' authorization codes to it.

- `flow:entity:member>entity:partner-app>return-with-authorization-code`, `process:authorization-server`
- severity: medium/medium · verb: `elicit`
- The case's stated OAuth defect: prefix matching on redirect URIs, so the authorization server itself sends the code to a URL the attacker owns. A confidential partner still needs its secret to redeem the code, which caps the impact.

> mark:


### tampering

**6.** An attacker with a foothold in the cloud account writes to the points ledger directly and changes members' balances, if the ledger does not authenticate the rewards API.

- `flow:process:rewards-api>store:points-ledger>read-and-write-points`, `store:points-ledger`, `process:rewards-api`
- severity: low/high · verb: `alter`
- Conditional: the source states nobody recorded how the API connects to the ledger.

> mark:

**7.** An attacker who can answer the rewards API's start-up key download serves a public key of their own, and the API then accepts access tokens the attacker signs.

- `process:rewards-api`, `process:authorization-server`
- severity: low/high · verb: `forge`
- The source states the key is downloaded at start-up and does not say from where or how it is checked. Low likelihood; the whole API's trust rests on that one fetch.

> mark:


### repudiation

**8.** A member denies approving a gift-card redemption a partner app made in their name, and the ledger cannot show that they approved it, because it records the client and not the member.

- `flow:entity:partner-app>process:rewards-api>call-rewards-api`, `store:points-ledger`, `entity:member`
- severity: medium/medium · verb: `unattributable`
- Stated outright. The consent screen proves a member connected the app once; it proves nothing about any one redemption.

> mark:


### information-disclosure

**9.** Anyone who can read the API log store collects the bearer access tokens it records and uses them before they expire.

- `flow:process:rewards-api>store:api-log-store>write-request-log`, `store:api-log-store`
- severity: medium/high · verb: `read`
- Stated outright: every request is written with its headers. The tokens are plain bearer tokens that live an hour and carry every scope for partner apps, so a log reader holds live credentials.

> mark:

**10.** An attacker who obtains a copy of the identity database recovers members' refresh tokens and password hashes, since its protection at rest is not recorded.

- `store:identity-database`, `flow:process:authorization-server>store:identity-database>read-and-write-accounts-and-grants`
- severity: low/high · verb: `recover-credential`
- Conditional on the unrecorded at-rest protection. It matters more here than on the other stores because the mobile refresh tokens never expire.

> mark:


### denial-of-service

**11.** An attacker floods the authorization server's sign-in and token endpoints until members and partner apps can no longer get tokens.

- `process:authorization-server`, `flow:entity:member>process:authorization-server>sign-in-and-consent`, `flow:process:mobile-app>process:authorization-server>token-requests`
- severity: medium/medium · verb: `flood`
- Every client depends on the one authorization server, and the source states no rate limit. The rewards API cannot serve anyone once tokens stop.

> mark:


### elevation-of-privilege

**12.** A partner app presents a member's access token to the support console, which accepts it because both services trust the same signing key and neither checks the audience, and it closes members' accounts.

- `process:support-console`, `entity:partner-app`, `process:authorization-server`
- severity: medium/high · verb: `escalate`
- The case's central finding, assembled from three stated facts: no audience check, one signing key for both services, and the console on the same public hostname. Whether the console also checks a role is not stated, which is what keeps the likelihood at medium.

> mark:

**13.** A partner app that only needs to show a member's points redeems that member's points for gift cards, because every partner app holds every scope.

- `flow:entity:partner-app>process:rewards-api>call-rewards-api`, `entity:partner-app`, `process:rewards-api`
- severity: medium/high · verb: `abuse-grant`
- Stated outright: every partner gets every scope. Redemption needs nothing beyond the scope, so the grant a partner legitimately holds reaches money.

> mark:

**14.** A member whose account support closed keeps redeeming points with a refresh token the closure never revoked.

- `store:identity-database`, `store:identity-database`, `flow:process:mobile-app>process:rewards-api>call-rewards-api`
- severity: medium/medium · verb: `abuse-grant`
- Stated outright: closing an account marks it closed in the directory and leaves the grant store untouched. Whether the token endpoint reads the closed flag is not stated.

> mark:

**15.** A partner app that a member disconnected keeps calling the rewards API with an access token issued before the disconnect.

- `store:identity-database`, `entity:partner-app`
- severity: low/low · verb: `abuse-grant`
- Stated, and bounded by the one-hour token life: disconnecting deletes the refresh token in the identity database and revokes nothing already issued. Expected rather than must-find because the window is short and the member already trusted the app.

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
    "14-loyalty-oauth-platform": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "d414cc10c981749e783270475acdaa286ead5d32d22e334e4f6d1e23ae385a29",
      "model.json": "a17b00f9ceb04039f2684393ce1cc35e117e960e62d98e6d30cea3c59a2a6f1a",
      "claims/asvs.json": "b10a09436c79bd42d208c9656d65ecea9252530762b487d67355dd57816bf285",
      "claims/stride.json": "d8e4f0b80d97f41452a348f0b2066a8b5f6b75c9f333cfee08e986dc5be39362"
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
