# Corrections: 07-cicd-store-deploy

Bootstrap → blessed diff, per `BLESSING.md` step 3. The candidate came from an
agent stand-in running `prompts/extract.md` (no Vertex credentials here — see
`case.json`'s `bootstrap` field and wayfinder ticket 030), so what follows is
signal about the prompt, not about the pinned Flash model.

Worked as step 3 requires: the source text a sentence at a time, asking what the
model must say, then checking whether it says it.

## Corrections

### 1. Flow assets under-tagged on every flow but one

- **Path:** `data_flows[*].assets`
- **Bootstrap:** `[]` on all flows except `set-current-release`, which carried
  `["credentials"]`
- **Blessed:** `["business-critical-data"]` on `push-branches`, `fetch-source`,
  `push-image` and `pull-image`; `["credentials", "business-critical-data"]` on
  `set-current-release`
- **Source reason:** the source code and the container image are the things this
  whole system exists to move, and the flows that carry them are how they move.
  The candidate tagged the *elements* that hold them (`store:git-server`,
  `store:image-registry`, `process:build-runner`) but not the flows between,
  which reads as a rule applied to nouns and not to verbs.

### 2. A stated absence recorded as an unknown

- **Path:** `flow:build-runner-to-public-package-registry:resolve-dependencies.authentication`
- **Bootstrap:** `"unknown"`
- **Blessed:** `"none — the runner does not verify signatures on the packages it downloads"`
- **Source reason:** "the runner does not verify signatures on what it downloads"
  is stated, not absent. The candidate carried the fact in `description` and
  `data_description` but left the attribute an analyst reads at `unknown`, which
  downgrades a grounded finding to a needs-info one. This is checklist item 4 —
  the corpus's most repeated extraction failure — in its exact form.

Worth being precise about the direction, because the inverse error is worse:
this is *not* the invented absence `BLESSING.md` warns about. The source states
the non-verification outright. Nothing else in this case was moved from
`unknown` to a stated value.

### 3. Availability missing from the element whose failure stops trade

- **Path:** `process:store-server.assets`
- **Bootstrap:** `["business-critical-data"]`
- **Blessed:** `["business-critical-data", "availability-critical"]`
- **Source reason:** "running the till software" is what the source says the box
  does, and the same paragraph explains that a release restarts the container.
  The candidate's tag set describes what the server holds and not what it does,
  which loses the impact half of every denial-of-service finding against it.

### 4. The manual-rebuild qualifiers left only in prose

- **Path:** `flow:developer-to-build-runner:manual-rebuild.data_description`
- **Bootstrap:** `"Interactive login and manual rebuild command for main"`
- **Blessed:** `"an interactive login and a rebuild of main that any developer may trigger, requiring no merge and receiving no review"`
- **Source reason:** "any developer", "does not require a merge" and "is not
  reviewed" are three stated qualifiers, and the candidate kept them in the
  element `description` only. `authentication` stays `unknown` and correctly so
  — the source says who may trigger a rebuild, never how they sign in.

### 5. Cosmetic: element names and empty keys

- **Path:** `*.name`, `*.notes`
- **Bootstrap:** title-cased names (`"Build runner"`), `"notes": ""` on every
  element
- **Blessed:** lower-cased names matching the rest of the corpus, empty `notes`
  keys dropped
- **Source reason:** none — corpus consistency only, recorded so the diff is
  complete rather than because it is signal.

## Considered and not corrected

- **The deploy controller's one record is not a Data Store.** "Holds one record:
  which image sha the estate should be on" is data at rest by the letter of
  CONTEXT.md, and a reviewer could reasonably split it out. Kept inside
  `process:deploy-controller` because the source describes no way to reach the
  record except through the controller, so a separate store would add an element
  no flow could legitimately reach. The tampering and denial-of-service claims
  against the record are filed on the process.
- **The retail WAN is not a trust boundary.** It is transport between two zones
  the model already has, and modelling it as a third zone would put both
  endpoints of the store-to-corporate flows in the wrong place.
- **`entity:developer` carries no assets.** Developers are the authority in this
  system rather than something it protects; the authority is expressed by the
  flows they initiate and by the elevation-of-privilege claim.
- **One element for ~1,200 store servers.** The source describes them
  collectively and states no per-store difference; recorded in the element's
  `notes` so a later reviewer can disagree with it specifically.
- **Who initiates was right throughout.** Both traps in this source — the runner
  *picking up* a merge rather than being pushed to, and the store servers
  *asking* the controller rather than being deployed to — came back with the
  direction correct, helped by the source stating "The store servers do not get
  pushed to" outright.

## Signal

Two patterns, one of them the corpus's standing one.

**The stranded qualifier again, and this time on a stated absence.** Correction 2
is checklist item 4 exactly: the fact reached `description` and
`data_description` but not the attribute that governs whether the finding is
grounded or needs-info. Every previous case in this corpus has produced a variant
of this, and this one sharpens it — the cost is not a missing fact but a
*downgraded verdict*, which is invisible in an extraction diff and shows up only
as a weaker threat downstream.

**Attributes are applied to nouns and not to verbs.** Corrections 1 and 3 are the
same shape from two directions: the candidate reasons well about elements that
*hold* something and poorly about flows that *carry* it, and reasons about what
an element contains rather than what it does. Both are asset-tag errors, both
feed impact scoring, and both are cheap to address in `prompts/extract.md` if the
pattern survives re-bootstrapping against the real `extract` node.
