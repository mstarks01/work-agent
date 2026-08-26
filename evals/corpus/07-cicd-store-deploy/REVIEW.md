# Review sitting — is `07-cicd-store-deploy`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/07-cicd-store-deploy`.

**Build and deploy pipeline pushing container images to a 1,200-store estate** — domain `ci-cd-release`.

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

> Build and deploy pipeline for the in-store estate.
>
> We run about 1,200 stores. Each store has a small server in the back office
> running the till software as a container. This is how a change a developer
> writes ends up on those servers.
>
> Developers push their branches to our self-hosted git server, which sits on the
> corporate network. A merge to `main` is what starts a build.
>
> The build runner picks the merge up, fetches the source, resolves the
> dependency lockfile and produces a container image. The runner lives in its own
> build environment, separate from the corporate network. It pushes the finished
> image to our image registry, which is also in the build environment. Images are
> tagged with the commit sha.
>
> When the image is pushed the runner calls the deploy controller and asks it to
> make that image the current release. The deploy controller runs on the
> corporate network and holds one record: which image sha the estate should be
> on. The token the runner uses to call the controller is a shared build token
> that is the same for every pipeline and has not been rotated since the pipeline
> was set up.
>
> The store servers do not get pushed to. Every store server asks the deploy
> controller once a minute what the current release is, and if it differs from
> what it is running it pulls that image from the registry and restarts the
> container. The registry allows the pull; nobody has written down what a store
> server presents to it, or what it presents to the controller.
>
> The image registry is reachable from the store estate over the WAN. Traffic
> between the stores and the corporate network goes over the retail WAN.
>
> Two things worth saying that are not on the main path. Dependency resolution
> reaches out to the public package registry on the internet, and the lockfile is
> taken as given — the runner does not verify signatures on what it downloads.
> And any developer can log in to the build runner and kick off a rebuild of
> `main` by hand; that path does not require a merge and is not reviewed.
>
> Nobody has documented whether the git server encrypts what it stores, or
> whether the image registry does.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:developer | human | boundary:corporate-network |
| entity:public-package-registry | external-system | boundary:internet |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:build-runner | internal | unknown | boundary:build-environment | unknown |
| process:deploy-controller | internal | unknown | boundary:corporate-network | unknown |
| process:store-server | internal | unknown | boundary:store-estate | container runtime running the till software |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:git-server | boundary:corporate-network | unknown | unknown |
| store:image-registry | boundary:build-environment | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:developer-to-git-server:push-branches | entity:developer | store:git-server | unknown | unknown | unknown |
| flow:developer-to-build-runner:manual-rebuild | entity:developer | process:build-runner | unknown | unknown | unknown |
| flow:build-runner-to-git-server:fetch-source | process:build-runner | store:git-server | unknown | unknown | unknown |
| flow:build-runner-to-public-package-registry:resolve-dependencies | process:build-runner | entity:public-package-registry | unknown | none — the runner does not verify signatures on the packages it downloads | unknown |
| flow:build-runner-to-image-registry:push-image | process:build-runner | store:image-registry | unknown | unknown | unknown |
| flow:build-runner-to-deploy-controller:set-current-release | process:build-runner | process:deploy-controller | unknown | a shared build token, the same for every pipeline, never rotated since the pipeline was set up | unknown |
| flow:store-server-to-deploy-controller:poll-current-release | process:store-server | process:deploy-controller | unknown | unknown | unknown |
| flow:store-server-to-image-registry:pull-image | process:store-server | store:image-registry | unknown | unknown | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:corporate-network | network |
| boundary:build-environment | network |
| boundary:store-estate | network |
| boundary:internet | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:store-server` — One element stands for the whole fleet of ~1,200 identical servers; the source describes them collectively and states no per-store difference.
- `store:git-server` — The source treats the git server both as somewhere source rests and as something developers push to; modelled as a Data Store, which is where the source code actually lives.

**Assumptions**

- `entity:developer` — Developers sit on the corporate network. (basis: The git server they push to is stated to sit on the corporate network; the source places developers nowhere else.)
- `process:build-runner` — The build runner is not reachable from the internet. (basis: Stated to live in its own build environment; the only internet interaction stated is outbound dependency resolution.)
- `process:deploy-controller` — The deploy controller is not reachable from the internet. (basis: Stated to run on the corporate network and to be reached by store servers over the retail WAN.)
- `process:store-server` — The store servers are not reachable from the internet. (basis: Back-office servers reached over the retail WAN; the source states they do not get pushed to at all.)

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

## Part 2 — the 24 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a finding here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker holding the shared build token calls the deploy controller as if it were the build runner and names an image sha of their own choosing as the current release.

- cites: `flow:build-runner-to-deploy-controller:set-current-release`, `process:deploy-controller`
- tier: must-find · severity: medium/high · verb: `use-credential`
- recorded note: The one credential the source describes in full, and it describes it as shared across every pipeline and never rotated. Holding it is indistinguishable from being the runner.

> mark:

**2.** An attacker on the retail WAN presents itself to the image registry as a store server and pulls the estate's container images.

- cites: `flow:store-server-to-image-registry:pull-image`, `store:image-registry`
- tier: must-find · severity: medium/medium · verb: `impersonate`
- recorded note: The source says the registry allows the pull and that nobody has written down what a store server presents to it; needs-info is the right verdict, not silence.

> mark:

**3.** An attacker polls the deploy controller while claiming to be a store server, since what a store server presents to the controller is unverified.

- cites: `flow:store-server-to-deploy-controller:poll-current-release`, `process:deploy-controller`
- tier: expected · severity: medium/low · verb: `impersonate`
- recorded note: Lower impact than the registry pull because the poll returns one sha, but it is the same undocumented identity and belongs on the record.

> mark:

**4.** An attacker pushes branches to the git server as a developer, because how developers authenticate to it is unverified.

- cites: `flow:developer-to-git-server:push-branches`, `entity:developer`
- tier: expected · severity: medium/medium · verb: `impersonate`
- recorded note: Pushing a branch alone does not reach the estate — a merge to main is what starts a build — which is why this sits below the release-setting claims.

> mark:

**5.** An attacker logs in to the build runner as a developer and triggers a rebuild of main, since how that login is authenticated is unverified.

- cites: `flow:developer-to-build-runner:manual-rebuild`, `process:build-runner`
- tier: expected · severity: medium/high · verb: `impersonate`
- recorded note: Distinct from the elevation claim on the same flow: this is an outsider becoming a developer, not a developer using the authority they legitimately have.

> mark:


### tampering

**6.** An attacker alters the deploy controller's current-release record so that every store server pulls and runs an image of the attacker's choosing within a minute.

- cites: `process:deploy-controller`, `process:store-server`
- tier: must-find · severity: medium/high · verb: `alter`
- recorded note: The single record is the whole control plane of the estate, and the poll loop turns one write into 1,200 restarts with no further attacker action.

> mark:

**7.** An attacker publishes a package that the lockfile resolves to and the runner bakes it into the image unchecked, because signatures on downloads are not verified.

- cites: `flow:build-runner-to-public-package-registry:resolve-dependencies`, `process:build-runner`
- tier: must-find · severity: medium/high · verb: `plant`
- recorded note: A stated absence, not an unknown — the source says outright that the runner does not verify signatures, so this is grounded rather than needs-info.

> mark:

**8.** An attacker replaces the image stored under a commit-sha tag in the registry, so store servers pull attacker content while the recorded release is unchanged.

- cites: `store:image-registry`, `process:store-server`
- tier: must-find · severity: medium/high · verb: `plant`
- recorded note: Worth keeping separate from the controller-record claim: this one leaves the release record honest, so nothing in the described system would show a change.

> mark:

**9.** An attacker modifies source held on the git server so the change is carried into the next image the runner builds.

- cites: `store:git-server`
- tier: expected · severity: low/high · verb: `alter`
- recorded note: The slowest path to the estate of the three tampering entry points, since it waits for a build, but the source states no protection on the content at rest.

> mark:

**10.** An attacker on the retail WAN alters the container image as a store server pulls it, since protection of that traffic is unverified.

- cites: `flow:store-server-to-image-registry:pull-image`
- tier: expected · severity: low/high · verb: `alter-in-transit`
- recorded note: The image crosses from the build environment to the store estate over a WAN the source describes without saying anything about how it is protected.

> mark:

**11.** An attacker on the retail WAN alters the answer to a store server's poll so that store installs a different image sha from the rest of the estate.

- cites: `flow:store-server-to-deploy-controller:poll-current-release`
- tier: expected · severity: low/medium · verb: `alter-in-transit`
- recorded note: Per-store rather than estate-wide, which is what separates it from writing the controller's record.

> mark:


### repudiation

**12.** A build that reached the estate cannot be attributed to the developer who started it, because any developer can trigger a manual rebuild on an unreviewed path whose authentication is unverified.

- cites: `flow:developer-to-build-runner:manual-rebuild`, `process:build-runner`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: The source states the path is not reviewed and does not require a merge, so the git history that would otherwise carry attribution is bypassed by construction.

> mark:

**13.** The deploy controller cannot tell which pipeline set a release, because every pipeline presents the same shared build token.

- cites: `flow:build-runner-to-deploy-controller:set-current-release`, `process:deploy-controller`
- tier: must-find · severity: high/medium · verb: `unattributable`
- recorded note: High likelihood because it is not an attack condition but the described steady state — the token is stated to be the same for every pipeline.

> mark:


### information-disclosure

**14.** An attacker who reads the build runner's configuration recovers the shared build token and can thereafter set the estate's release.

- cites: `process:build-runner`, `flow:build-runner-to-deploy-controller:set-current-release`
- tier: must-find · severity: medium/high · verb: `recover-credential`
- recorded note: Recovering the credential is a separate action from using it, and the corpus files the use under spoofing; the never-rotated qualifier is what makes recovery durable.

> mark:

**15.** An attacker who reaches the git server's storage reads the estate's source code, since protection of what it stores is unverified.

- cites: `store:git-server`
- tier: expected · severity: low/medium · verb: `read`
- recorded note: The source names this gap explicitly in its closing line, which is the sentence most likely to be dropped in extraction.

> mark:

**16.** An attacker who reaches the image registry's storage reads the built images and whatever is baked into them, since protection at rest is unverified.

- cites: `store:image-registry`
- tier: expected · severity: low/medium · verb: `read`
- recorded note: Paired with the git-server claim in the same closing sentence; kept separate because they are different stores with different reachability.

> mark:

**17.** An attacker on the retail WAN reads the container image as a store server pulls it, since protection of that traffic is unverified.

- cites: `flow:store-server-to-image-registry:pull-image`
- tier: expected · severity: low/medium · verb: `intercept`
- recorded note: Reading the image in transit and altering it in transit are two claims on one flow. The elements are identical, so the action verb is the only thing that separates them: `intercept` against `alter-in-transit`.

> mark:


### denial-of-service

**18.** An attacker sets the current release to an image that does not start, and every store server restarts into it and stops serving tills.

- cites: `process:deploy-controller`, `process:store-server`
- tier: must-find · severity: medium/high · verb: `alter`
- recorded note: The estate-wide blast radius is the poll loop working as designed; this is the availability face of the same write the tampering lane files as integrity.

> mark:

**19.** An attacker floods the deploy controller until the estate's polls fail and no new release can reach any store.

- cites: `process:deploy-controller`
- tier: expected · severity: medium/low · verb: `flood`
- recorded note: Impact is low because a store server that cannot poll keeps running the image it has; what stops is deployment, not selling.

> mark:

**20.** An attacker makes the image registry unreachable over the WAN so store servers cannot complete a pull and the estate is left split across two releases.

- cites: `flow:store-server-to-image-registry:pull-image`, `store:image-registry`
- tier: expected · severity: low/medium · verb: `disable`
- recorded note: The interesting consequence is not downtime but divergence: the source describes no ordering or rollback across 1,200 independent pullers.

> mark:


### elevation-of-privilege

**21.** Any developer turns code of their own choosing into the software running in 1,200 stores by triggering the manual rebuild, a path the source states requires no merge and receives no review.

- cites: `flow:developer-to-build-runner:manual-rebuild`, `process:store-server`
- tier: must-find · severity: medium/high · verb: `abuse-grant`
- recorded note: The case's signature shape: authority flows upward through the build, so the weakest gate on the input side is the real authority over the estate.

> mark:

**22.** An attacker who compromises the build runner controls what every store server runs, because the runner both writes the image and holds the token that names the release.

- cites: `process:build-runner`, `process:store-server`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The runner concentrates both halves of the deploy path, so the build environment is effectively a higher-privilege zone than the estate it feeds.

> mark:

**23.** An attacker who takes one store server uses whatever it presents to reach the deploy controller on the corporate network and the registry in the build environment.

- cites: `process:store-server`, `process:deploy-controller`
- tier: expected · severity: low/high · verb: `escalate`
- recorded note: A back-office box in one of 1,200 stores is the least defensible element in the model and it is stated to reach across two boundaries.

> mark:

**24.** A malicious dependency executes with the build runner's authority during the build, carrying an attacker from the public internet into the build environment.

- cites: `flow:build-runner-to-public-package-registry:resolve-dependencies`, `process:build-runner`
- tier: expected · severity: medium/high · verb: `escalate`
- recorded note: Distinct from the tampering claim on the same flow: that one is about what ends up in the image, this one is about code running on the runner at build time.

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

**Then record the sitting.** Save this filled document as
`REVIEW-<your GitHub login>.md` beside the original — the filled copy is the
evidence, and the generated `REVIEW.md` stays derived and unfilled. Append
this entry to `reviews` in `evals/corpus/07-cicd-store-deploy/case.json`, which is what
`tests/test_case_review.py` reads:

```json
  "reviews": [
    {
      "reviewer": "<your GitHub login>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "1bfb96ef3374b697ef78e76661daa3d2b227792a3b20d2d1ee1d526cde02652c"},
        {"file": "model.json", "sha256": "2138cfd009525ccadcb48ab6dd83dcab3c8b9edaae5f5e0121f356f9b220a348"},
        {"file": "claims/stride.json", "sha256": "a1fa75acf29fff06ad85624f37ce91b7904d0085e17be6175135e8a935be9360"}
      ],
      "document": "REVIEW-<your GitHub login>.md",
      "notes": "<counts, and anything you changed>"
    }
  ],
```

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
and that the reviewer has a line in `evals/review/voters.toml` — a first-time
contributor adds their own, standing `contributor`. Then
`python -m evals.harness.run submit sitting` opens the PR.
