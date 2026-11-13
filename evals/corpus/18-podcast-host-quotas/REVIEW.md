# Review sitting — is `18-podcast-host-quotas`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/18-podcast-host-quotas`.

**A podcast host whose parts state quotas, limits and their absence** — domain `podcast hosting`.

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

> Podcast hosting — what we run, written up by the platform team.
>
> Podcasters publish their shows with us. A podcaster uploads each episode's audio file through the upload API, which is on the internet. Each show gets 50 GB of storage in total, and the upload API refuses an upload that would take a show over its 50 GB. The upload API stores the audio in the media store and the episode details in the show database. Podcasters sign in with an email address and a password.
>
> The transcoder picks up each new audio file from the media store and makes the smaller versions that listeners' apps download. Each transcoder runs at most two jobs at once.
>
> Listeners' podcast apps fetch each show's RSS feed from the feed service, which is public. The feed service reads the show database to build each feed. We do not rate limit the feed service, because apps poll feeds all day and we never want an app to miss a new episode.
>
> Every night our ad partner sends us files of download counts. The stats importer starts a new import job for every file that arrives, with no cap on how many run together, and writes the counts to the stats database.
>
> Leo says the upload API probably also has a size limit on a single file, but he would have to check.
>
> All of this runs in one hosting account. The podcasters, the listeners' apps and the ad partner are outside it.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:podcaster | human | boundary:internet |
| entity:podcast-app | external-system | boundary:internet |
| entity:ad-partner | external-system | boundary:internet |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:upload-api | internet-facing | unknown | boundary:hosting-account | unknown |
| process:transcoder | unknown | unknown | boundary:hosting-account | unknown |
| process:feed-service | internet-facing | web | boundary:hosting-account | unknown |
| process:stats-importer | unknown | unknown | boundary:hosting-account | unknown |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:media-store | boundary:hosting-account | unknown | unknown |
| store:show-database | boundary:hosting-account | unknown | unknown |
| store:stats-database | boundary:hosting-account | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:podcaster>process:upload-api>upload-episodes | entity:podcaster | process:upload-api | unknown | password sign-in with an email address | unknown |
| flow:process:upload-api>store:media-store>store-audio | process:upload-api | store:media-store | unknown | unknown | unknown |
| flow:process:upload-api>store:show-database>store-episode-details | process:upload-api | store:show-database | unknown | unknown | unknown |
| flow:process:transcoder>store:media-store>pick-up-audio | process:transcoder | store:media-store | unknown | unknown | unknown |
| flow:entity:podcast-app>process:feed-service>fetch-feeds | entity:podcast-app | process:feed-service | unknown | unknown | unknown |
| flow:process:feed-service>store:show-database>read-show-details | process:feed-service | store:show-database | unknown | unknown | unknown |
| flow:entity:ad-partner>process:stats-importer>send-download-counts | entity:ad-partner | process:stats-importer | unknown | unknown | unknown |
| flow:process:stats-importer>store:stats-database>write-counts | process:stats-importer | store:stats-database | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:internet | network |
| boundary:hosting-account | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:upload-api` — Stated quota: "Each show gets 50 GB of storage in total, and the upload API refuses an upload that would take a show over its 50 GB." A size limit on one file is hedged, not stated: "Leo says the upload API probably also has a size limit on a single file, but he would have to check."
- `process:transcoder` — Stated concurrency limit: "Each transcoder runs at most two jobs at once." The source does not say where the smaller versions are kept.
- `process:feed-service` — Stated absence of a rate limit: "We do not rate limit the feed service, because apps poll feeds all day and we never want an app to miss a new episode."
- `process:stats-importer` — Stated absence of a concurrency limit: "with no cap on how many run together".
- `flow:entity:podcaster>process:upload-api>upload-episodes` — "Podcasters sign in with an email address and a password."

**Assumptions**

- `process:feed-service` — The feed service presents a web interface. (basis: It serves each show's RSS feed to podcast apps that fetch it, and an RSS feed is a document fetched by its URL, the HTTP family that prompts/extract.md calls web.)
- `entity:podcaster` — Podcasters are placed on the internet because the schema requires a zone. (basis: They reach the upload API on the internet; the source places them outside the hosting account and says no more.)
- `entity:podcast-app` — Podcast apps are placed on the internet because the schema requires a zone. (basis: They fetch feeds from the public feed service; the source places them outside the hosting account and says no more.)
- `entity:ad-partner` — The ad partner is placed on the internet because the schema requires a zone. (basis: The source places it outside the hosting account and does not say how its files arrive.)

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

## Part 2 — the 9 recorded STRIDE threats

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

**1.** An attacker guesses or reuses a podcaster's password at the upload API and publishes or replaces episodes as that podcaster.

- `flow:entity:podcaster>process:upload-api>upload-episodes`, `entity:podcaster`
- severity: medium/medium · verb: `guess-credential`
- Stated: podcasters sign in with an email address and a password. The source states nothing that slows guesses at sign-in.

> mark:

**2.** If the stats importer does not authenticate the ad partner, an attacker sends files of download counts as the ad partner.

- `flow:entity:ad-partner>process:stats-importer>send-download-counts`, `entity:ad-partner`
- severity: medium/medium · verb: `impersonate`
- The source never says how the ad partner's files arrive or how the importer knows who sent them.

> mark:


### tampering

**3.** If the media store does not restrict who writes to it, an attacker replaces an episode's audio in the media store.

- `store:media-store`
- severity: low/medium · verb: `alter`
- The source states nothing about access to the media store.

> mark:


### repudiation

**4.** A podcaster replaces an episode and denies it, and nothing records which account made the change.

- `flow:entity:podcaster>process:upload-api>upload-episodes`, `entity:podcaster`
- severity: low/low · verb: `unattributable`
- The source states no record of who uploads or changes an episode.

> mark:


### information-disclosure

**5.** If the upload API's sign-in is not encrypted in transit, an attacker on the network path reads podcasters' email addresses and passwords.

- `flow:entity:podcaster>process:upload-api>upload-episodes`
- severity: low/high · verb: `intercept`
- The source says nothing about encryption in transit, and passwords cross this flow.

> mark:


### denial-of-service

**6.** An attacker requests RSS feeds from the public feed service as fast as they can, from many clients, until the feed service and the show database stop serving feeds to podcast apps.

- `process:feed-service`, `flow:entity:podcast-app>process:feed-service>fetch-feeds`, `store:show-database`
- severity: high/medium · verb: `flood`
- This case exists for this finding. The source states the absence: "We do not rate limit the feed service". The service is public. An assertion pass that writes the stated absence gives the lane the fact this finding rests on.

> mark:

**7.** An attacker who can send files to the stats importer sends a great many files at once, and the importer starts a job for each one until it exhausts its host.

- `process:stats-importer`, `flow:entity:ad-partner>process:stats-importer>send-download-counts`
- severity: medium/medium · verb: `flood`
- Stated absence: "with no cap on how many run together". The attacker must reach the importer's input, which the source does not describe.

> mark:

**8.** An attacker opens many shows and fills each one to its 50 GB, until the media store runs out of space for every podcaster.

- `process:upload-api`, `store:media-store`
- severity: low/medium · verb: `flood`
- The quota is stated per show: "Each show gets 50 GB of storage in total". The source states no limit on how many shows one person opens.

> mark:


### elevation-of-privilege

**9.** An attacker uploads a crafted audio file that exploits the transcoder and runs code with the transcoder's access to the media store and the hosting account.

- `process:transcoder`, `flow:process:transcoder>store:media-store>pick-up-audio`
- severity: low/high · verb: `escalate`
- The transcoder parses files that any podcaster uploads. The source states nothing about how transcoding is isolated.

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
    "18-podcast-host-quotas": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "0187d56cd7a475ff3ad1285deb15b085f448a2fcc82392accdef6b9bda8fe1ee",
      "model.json": "82bdd429f176fc7405c8c1f26fcba581c267c8ac298fd77eb78c1bcbe075cc7b",
      "claims/stride.json": "e2dd88107eec3b42f6b55897b5d9a2c129d1b307d17cd6a36a539c8c312cc4e6"
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
