# Review sitting — is `10-cookbook-generic-cms`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/10-cookbook-generic-cms`.

**Content site with a CDN-served asset path and direct admin database access** — domain `content-management`.

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

> Content site — CMS, quick description.
>
> Public site: readers hit the web server over HTTPS, some of them signed in.
> The web server runs the CMS and keeps pages, accounts and comments in a MySQL
> database on the same hosted network.
>
> Images, stylesheets and downloads do not come off the web server. Readers fetch
> those from the CDN over HTTP. When new assets are published the web server
> pushes them up to the CDN's bucket.
>
> There is an admin who maintains the database directly rather than through the
> CMS. That is an unsecured MySQL connection straight to the box, no TLS on it,
> and they do it from wherever they happen to be — there is no jump host.
>
> Nothing here tells you how the push to the CDN is authenticated, and I could
> not tell you whether the database is encrypted on disk.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:reader | human | boundary:public-internet |
| entity:admin | human | boundary:public-internet |
| entity:cdn | external-system | boundary:cdn |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:web-server | internet-facing | web | boundary:hosted-network | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:mysql-database | boundary:hosted-network | unknown | unknown |
| store:cdn-bucket | boundary:cdn | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:reader-to-web-server:page-requests | entity:reader | process:web-server | HTTPS | sign-in exists for some readers; the mechanism, its strength and how sessions are handled are not stated | HTTPS |
| flow:reader-to-cdn:asset-fetch | entity:reader | entity:cdn | HTTP | unknown | none — readers fetch these over HTTP |
| flow:web-server-to-mysql-database:cms-data | process:web-server | store:mysql-database | MySQL | unknown | unknown |
| flow:web-server-to-cdn-bucket:asset-publish | process:web-server | store:cdn-bucket | unknown | unknown | unknown |
| flow:admin-to-mysql-database:direct-administration | entity:admin | store:mysql-database | MySQL | unknown | none — an unsecured MySQL connection, no TLS on it |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:hosted-network | network |
| boundary:cdn | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `entity:reader` — The source does not distinguish signed-in and anonymous readers as separate actors, so they are modelled as one element.
- `entity:admin` — The source says nothing about how the admin authenticates or what credential they hold, so no asset tag is carried here; the direct-administration flow is what grounds claims about this path.
- `entity:cdn` — The source names no CDN provider and does not say whether it is operated by the same party as the hosted network.
- `process:web-server` — The source treats 'the web server' and 'the CMS' as one deployed thing; modelled as a single process.
- `store:mysql-database` — Not tagged credentials: the source says the database holds accounts, and what an account record contains is never stated. Sign-in exists for some readers, but where its secrets live is not described.
- `flow:web-server-to-cdn-bucket:asset-publish` — The source states outright that how this push is authenticated is not described, which is why the attribute is unknown rather than absent.

**Assumptions**

- `process:web-server` — The web server is reachable from the internet. (basis: Described as the 'Public site' that 'readers hit ... over HTTPS'.)
- `entity:admin` — The admin connects from outside the hosted network. (basis: "they do it from wherever they happen to be — there is no jump host".)
- `entity:cdn` — The CDN and its bucket sit in a separate trust zone from the hosted network. (basis: Assets 'do not come off the web server'; the web server 'pushes them up to the CDN's bucket', implying a distinct, separately operated location.)

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

## Part 2 — the 8 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### web-frontend-security

**A1.** `V3.3.1` — Sign-in exists for some readers and no cookie attribute is stated for the session it creates.

- cites: `entity:reader`, `process:web-server`, `flow:reader-to-web-server:page-requests`
- tier: must-find
- recorded note: The submitter says the mechanism and session handling are not stated, which is the requirement.

> mark:

**A2.** `V3.4.1` — Assets are fetched over plain HTTP, so no transport security header protects them.

- cites: `process:web-server`, `entity:cdn`, `flow:reader-to-cdn:asset-fetch`
- tier: must-find
- recorded note: encryption_in_transit is stated absent on the CDN fetch.

> mark:


### session-management

**A3.** `V7.2.2` — Nothing states how a reader session is represented or generated.

- cites: `entity:reader`, `process:web-server`, `flow:reader-to-web-server:page-requests`
- tier: must-find
- recorded note: Sign-in is stated to exist and its session handling is stated not to be described.

> mark:


### encoding-and-sanitization

**A4.** `V1.2.4` — The web server builds MySQL queries for CMS content and nothing says how.

- cites: `process:web-server`, `store:mysql-database`, `flow:web-server-to-mysql-database:cms-data`
- tier: must-find
- recorded note: tech:database fires on a MySQL store reached from an internet-facing process.

> mark:


### secure-communication

**A5.** `V12.2.1` — An administrator reaches MySQL directly over a connection stated to carry no TLS.

- cites: `entity:admin`, `store:mysql-database`, `flow:admin-to-mysql-database:direct-administration`
- tier: must-find
- recorded note: encryption_in_transit is stated absent on a human-to-store flow.

> mark:


### validation-and-business-logic

**A6.** `V2.2.2` — Nothing states which layer validates the content an administrator publishes.

- cites: `process:web-server`, `entity:admin`
- tier: expected
- recorded note: Two human entities with different powers and no stated enforcement point.

> mark:


### authentication

**A7.** `V6.2.1` — No password rule is stated for the reader sign-in the input mentions.

- cites: `entity:reader`, `process:web-server`
- tier: expected
- recorded note: The submitter says the mechanism and its strength are not stated.

> mark:


### authorization

**A8.** `V8.2.1` — Nothing states what separates reader access from administrator access.

- cites: `entity:admin`, `entity:reader`, `process:web-server`
- tier: expected
- recorded note: Two human entities reach the same web server and no rule is named.

> mark:

## Part 3 — the 17 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a finding here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker holding the admin's database credentials connects to MySQL as the admin from anywhere on the internet, since there is no jump host in the way.

- cites: `flow:admin-to-mysql-database:direct-administration`, `entity:admin`
- tier: must-find · severity: medium/high · verb: `use-credential`
- recorded note: The source states the path is reachable from wherever the admin happens to be, which is the same as saying it is reachable from wherever an attacker happens to be.

> mark:

**2.** An attacker publishes assets into the CDN bucket as if they came from the web server, because how that push is authenticated is unverified.

- cites: `flow:web-server-to-cdn-bucket:asset-publish`, `store:cdn-bucket`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: The source says outright that nothing tells you how the push is authenticated; this is the unknown the case is built around.

> mark:

**3.** An attacker takes over a signed-in reader's session, since nothing is stated about how those sessions are handled.

- cites: `flow:reader-to-web-server:page-requests`, `entity:reader`
- tier: expected · severity: medium/medium · verb: `use-credential`
- recorded note: Sign-in is stated to exist and nothing else about it is; the finding available here is the unverified strength, not an absent control.

> mark:


### tampering

**4.** An attacker on the network path rewrites a stylesheet or script in flight before it reaches the reader, because assets are fetched over plain HTTP.

- cites: `flow:reader-to-cdn:asset-fetch`
- tier: must-find · severity: high/high · verb: `alter-in-transit`
- recorded note: The mixed-transport trap: the page arrives over HTTPS and its executable furniture does not, so the protection on the origin link buys nothing.

> mark:

**5.** An attacker who can write to the CDN bucket replaces a published asset and every reader of the site loads it.

- cites: `store:cdn-bucket`, `entity:cdn`
- tier: must-find · severity: medium/high · verb: `plant`
- recorded note: Planting the artifact at rest, kept distinct from modifying it in transit: `plant` against `alter-in-transit` on one artifact, which element agreement alone would merge.

> mark:

**6.** An attacker on the network path modifies the admin's statements in flight, because the connection carries no TLS.

- cites: `flow:admin-to-mysql-database:direct-administration`
- tier: must-find · severity: medium/high · verb: `alter-in-transit`
- recorded note: The missing TLS is stated rather than inferred, and the traffic it protects is database administration.

> mark:

**7.** An attacker with database access edits page content directly and the web server serves the altered page.

- cites: `store:mysql-database`, `process:web-server`
- tier: expected · severity: medium/medium · verb: `alter`
- recorded note: Defacement by the back door the admin path proves is open.

> mark:


### repudiation

**8.** Content is changed directly in the database and no CMS record shows who changed it, because that path bypasses the CMS entirely.

- cites: `flow:admin-to-mysql-database:direct-administration`, `process:web-server`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: The reason this case carries a repudiation lane worth grading: the accountability gap is structural, stated by the source in the words 'rather than through the CMS', and no missing control has to be assumed for it.

> mark:

**9.** A reader disputes a comment recorded against their account and the site cannot show it came from their session, since nothing about how sign-in is handled is stated.

- cites: `store:mysql-database`, `flow:reader-to-web-server:page-requests`
- tier: expected · severity: low/medium · verb: `unattributable`
- recorded note: Comments are named by the source as stored content attributable to accounts, which is what makes the dispute concrete.

> mark:


### information-disclosure

**10.** An attacker sniffing the admin's unsecured MySQL connection reads account and comment records in clear text.

- cites: `flow:admin-to-mysql-database:direct-administration`
- tier: must-find · severity: high/high · verb: `intercept`
- recorded note: The single highest-signal finding in the case: a stated absence of TLS on a link stated to carry the whole database.

> mark:

**11.** An attacker who reaches the database reads reader account records, since at-rest protection is unverified.

- cites: `store:mysql-database`
- tier: expected · severity: medium/high · verb: `read`
- recorded note: The source says outright that whether the database is encrypted on disk is unknown.

> mark:

**12.** An attacker watching a reader's plain-HTTP asset fetches learns which pages that reader is viewing.

- cites: `flow:reader-to-cdn:asset-fetch`, `entity:reader`
- tier: expected · severity: medium/low · verb: `intercept`
- recorded note: The privacy half of the mixed-transport problem; the HTTPS page request hides the URL and the asset fetches give it back.

> mark:


### denial-of-service

**13.** An attacker floods the internet-facing web server with page requests until the public site stops responding.

- cites: `process:web-server`
- tier: expected · severity: medium/medium · verb: `flood`
- recorded note: Baseline exposure claim; the web server is stated to be the whole of the site's dynamic surface.

> mark:

**14.** An attacker who can write to the CDN bucket removes the assets the site depends on so pages render broken.

- cites: `store:cdn-bucket`
- tier: expected · severity: low/medium · verb: `delete`
- recorded note: Same access as the asset-replacement claim, used to deny rather than to alter.

> mark:

**15.** An attacker on the direct MySQL path runs queries expensive enough that the web server cannot serve pages.

- cites: `store:mysql-database`, `flow:admin-to-mysql-database:direct-administration`
- tier: expected · severity: low/high · verb: `flood`
- recorded note: The database is shared between the admin path and the site, and the source describes no separation between them.

> mark:


### elevation-of-privilege

**16.** An attacker who intercepts the admin's unencrypted session obtains database authority that bypasses every control the CMS applies.

- cites: `flow:admin-to-mysql-database:direct-administration`, `store:mysql-database`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The escalation the case is built to grade: authority is not gained inside the application but around it, and the CMS never sees the actor.

> mark:

**17.** An attacker who plants a script in the CDN bucket runs code in every reader's browser in the site's own context.

- cites: `store:cdn-bucket`, `flow:reader-to-cdn:asset-fetch`
- tier: must-find · severity: medium/high · verb: `plant`
- recorded note: Where the two crossings compound: write access in a zone the site does not control becomes execution inside the site's origin, and neither crossing on its own carries that.

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
`evals/corpus/10-cookbook-generic-cms/case.json`, which is what
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
