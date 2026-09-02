# Blessing a golden case

How to turn one or more submitted sources into a golden case the evals can
score against.
This is a one-time, offline authoring task — nothing here runs during a live
analysis. The whole point of the document is **step 3**; everything else is
bookkeeping around it.

> For **case authors, and for anyone holding a Case Sitting** — step 6, which
> is free, offline, and open to outside contributors. New here? Start at
> [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

> **Step 6 has run once, and no other step ever has.** All 13 cases in
> `evals/corpus/` were written by an agent. A person has now read case
> `01-payments-checkout` whole — source, model and both reference sets — and
> signed it off in `case.json`. Nobody has read the other twelve, and nobody has
> run step 3 on any case: the `corrections.md` files record what an agent changed
> against the source text, not what a reviewer caught. Every number the suite
> reports carries that provenance — see the top of [README.md](README.md).

## What a case is

Two artifacts plus the input they're built from:

```
evals/corpus/<NN>-<slug>/
  source.md       the submitted text — exactly what the service would receive
  model.json      the blessed System Model; must pass the shipped validator
  claims/<framework>.json
                  that framework's reference set, written against model.json's IDs
  corrections.md  how the model was corrected against the source, and what that says
  case.json       metadata, plus the sources array declaring the case's input
```

A case may declare **more than one source**, because a job may submit more than
one. `case.json` names them the way a caller does:

```json
"sources": [
  {"kind": "description", "label": "System description",
   "file": "source.md", "sha256": "<digest of that file>"},
  {"kind": "transcript", "label": "Kickoff call",
   "file": "call.md", "sha256": "<digest of that file>"}
]
```

Each entry's `file` is relative to the case directory, so a second source is a
second file beside `source.md` — name it for what it is. `label` must be unique
within the case, and it is the label every `source_label` in `model.json` has to
cite: the service rejects a model citing a label its job never carried, so a
corpus that broke that rule would grade a shape production refuses.
`source_sha256` at the top level is the **aggregate over those refs**, computed
exactly as a report's `InputRef` computes it — not a digest of the text.

The match-label fixtures live alongside, in `evals/calibration_labels/`:
`build_pairs.py` holds the labels, and `pairs.json` is generated from it.

Run `python evals/verify_corpus.py` to check everything mechanical about the
above; it must be green before a case merges. It checks that every declared file
exists and digests as claimed, that labels are unique, that every
`source_label` in `model.json` names a source the case declares, and that
every `source_excerpt` is really found in the source it cites — the same
ladder the service runs, so a case cannot be blessed in a shape the gate
would reject. `--write-sha`
restamps each source's digest and the aggregate over them.

## The workflow

### 1. Write the source text

The input is what a real user would submit: prose, bullets, a rough dump, or a
transcribed call — **semi-structured and incomplete on purpose**. A source with
no gaps tests nothing, because the behaviour that matters most is how the
service handles what the text *doesn't* say (unstated facts become `unknown`).
A case with no unknowns can't exercise that.

**Writing a transcript source.** Match the form real exports have, which was
measured rather than guessed ([#51](https://github.com/mstarks01/work-agent/issues/51)):
attribution names the *participant* and never their role, there are **no**
uncertainty markers like `[inaudible]` (the real failure mode is fluent
fabrication, not visible garbling), and merged turns run around 220 characters.
Write it **cleaned, not raw** — the byte budget forces cleaning, and cue timings
are stripped by it.

**Grading a conversational rule needs care.** `score_extraction` reads element
IDs, boundary crossings, and the attributes in `_SCORED_ATTRIBUTES` — the closed
vocabularies exactly, and each free-text control through `control_state`. So "a
hedge became `unknown`" *is* measured, on any element both models carry. What is
still invisible is the wording: `technology`, `protocol` and `data_description`
are compared by nothing, because two correct readings of one sentence word them
differently. An assertion about those reaches a number only through the
reference threat set in end-to-end mode — a needs-info must-find threat that a
wrongly-confident attribute would suppress, dropping recall. Write each
assertion so it fires through that or through `ExtractionScore`, or it will not
be measured at all.

Size the system so the finished model lands at **8–20 elements**. That's not a
style preference: the reference threat set has to be exhaustively enumerable by a
human, because any real threat the author forgets to write down will score
against the tool as a false positive.

**Sanitization is mandatory for anything based on a real system.** No real
hostnames, IPs, bucket names, account IDs, credentials, customer data, or
employee names. The safest default — and what the synthetic cases do — is to
invent a plausible system rather than sanitize a real one: there's nothing to
leak by omission. If a case *is* derived from a real system, sanitize before you
write the text down, not after.

For a case converted from the OWASP Threat Model Cookbook (CC-BY 4.0), record the
source entry and its licence in `case.json`. If the source diagram is larger than
the 8–20 band, convert a **scoped subset** whose removal doesn't change any
remaining element's attributes, and note what you dropped.

### 2. Draft the model

Run the extraction step over `source.md` and keep its output as the starting
draft of the model:

```sh
python -m evals.harness.run run --mode extraction --case <NN>-<slug>
```

Hand-authoring a diagram from scratch is the expensive path and buys nothing the
correction pass in step 3 doesn't already give.

### 3. Correct the model against the source text

This is the real work. **Read the source text and check the model against it —
do not read the draft model looking for things that seem wrong.** Correcting a
plausible-looking artifact is measurably less thorough than checking it against
the source, so let the *input* drive your attention, not the draft.

In practice: take the source one sentence at a time and ask what the model must
say about that sentence — then go confirm it says it. Run this checklist on each
pass:

1. **Every noun that's a thing** — is there an element for it, of the right type?
   Watch for things mentioned in passing; an actor introduced in a closing
   sentence is the most commonly dropped element.
2. **Every verb that's an interaction** — is there a flow for it? Is its
   direction *who initiates*, not which way the data travels? Pull, poll, and
   consume interactions are routinely reversed.
3. **Every security-relevant attribute** — for authentication, encryption in
   transit, encryption at rest, exposure, and data classification: does the text
   state it? If not, is it `unknown`? A plausible value the text never gave is
   the most common and most damaging error — and inventing an *absence* ("no
   encryption") is worse than inventing a control, because category agents file
   confident findings on it.
4. **Every stated qualifier** — "shared", "never rotated", "full read/write",
   "does not check": is it in an attribute a category agent will read, or did it only
   survive as a quoted excerpt? A qualifier stranded in a quote is invisible
   downstream — literally so: `source_excerpt` is stripped from the model the
   category agents read. This is the single most repeated extraction failure.
5. **Every inference** — does each non-`unknown` value the text *didn't* state
   appear in the model's `assumptions` list, with a basis? An inferred value with
   no matching assumption is a bug.
6. **Zones and asset tags** — does every entity, process, and store sit in a
   trust zone the text implies? Are asset tags driven by what the data *is*, not
   by what the element is *called*?

Record every correction in `corrections.md`: the path, the draft value, the
corrected value, and the reason from the source text. Close with a short summary
of the pattern you saw — that record is what later informs how extraction errors
are weighted.

### 4. Write the reference sets

One per framework the case declares, at `claims/<framework>.json`. Step 4a is
STRIDE's and 4b is ASVS's; they are different jobs because the two packages
assert different things.

#### 4a. STRIDE — the reference threat set

Write one entry per threat, against the corrected model's element IDs:
`category`, `affected_element_ids`, `claim`, `tier`, `severity`, `notes`.

- **`claim` is one sentence, phrased as an attacker action** — *who does what
  to what*. Not a control recommendation, not a description of a weakness. The
  identity rule matches on the `verb` and the element IDs, but the claim
  sentence is what a reviewer and a calibration label read, so a claim written
  as a missing control leaves both with nothing to compare.
- **Enumerate exhaustively, lane by lane.** Every case must carry at least one
  reference threat in each of the six STRIDE categories; `verify_corpus.py`
  enforces it.
- **Tier honestly.** `must-find` means *if the tool misses this, it doesn't
  work*. If everything is must-find the bar is unreachable; if nothing is, it's
  meaningless.
- **Severity is `likelihood` and `impact` only** — the band is derived from the
  shipped matrix, so never write one.
- **Keep same-element threats in different lanes distinct.** Reading a flow
  and modifying it are two different claims; the verb (`read` vs `alter`) is
  exactly what the identity rule separates them by.
- **`notes` is your rationale.** Never scored, always worth writing — it's what
  lets a later reviewer disagree with you specifically.

#### 4b. ASVS — the reference requirement set

Skip this if the case does not declare ASVS. Its **Precondition** answers
`undecidable` when nothing in the model says what a flow carries, and six cases
sit there today for a reason that may be a thin model rather than a system out
of scope — see
[#219](https://github.com/mstarks01/work-agent/issues/219).

Write one entry per requirement you expect a ruling on: `chapter`,
`requirement`, `affected_element_ids`, `claim`, `tier`, `disposition`, `notes`.
No severity — the package grades nothing.

- **Name the requirement the standard's way**, `V6.2.1`. It is what the scorer
  matches on, by string, with no identity composed. So the claim sentence is not
  the thing being compared here, and a paraphrase costs nothing.
- **The set is closed, and that is what makes this different from 4a.** A run at
  the declared **ASVS Level** rules on a known list, so every requirement you
  *omit* is an assertion too: it says a correct run should not raise it. That is
  the `over_applied` cell in the score. STRIDE's set has no such complement.
- **Declare the level in `case.json`, and write against that level only.** A
  record outside it is unmeetable, and `verify_corpus.py` fails it.
- **Do not write a pass.** An ASVS claim says the requirement *applies and the
  input does not show it satisfied*. If the text settles the requirement, the
  entry does not belong in the set at all.
- **Tier honestly**, the same way 4a says.
- **Cover the chapters the model can reach.** The merge bar checks that every
  lane of every carried package has a `must-find` record *somewhere in the
  corpus*, not in every case.
- **`affected_element_ids` may be empty.** Most requirements address a coding
  practice with no position in the graph. One record in 63 uses this today; it is
  legal, and it drops out of candidate-trigger recall by name rather than
  counting as a miss.

**Then say what this submission can conclude**, in `disposition`. The
requirement being in play is one judgement; what the reader should do next is
another, and the applicability matrix cannot see the second. Ask the questions in
this order and stop at the first that answers:

1. Does the requirement's subject exist in this system at all? If not,
   `not-applicable`. This is the entry the standard invites by name — a WebRTC
   chapter against a system with no media path.
2. Does the source **state a fact** that settles the requirement against the
   system? Then `gap-from-prose`. A stated shared account, a password in an
   environment variable, a link stated to carry no TLS.
3. Could a **fuller description** answer it — documentation, a design decision,
   an architectural fact a submitter could write down? Then `needs-more-prose`.
   Requirements phrased as *the documentation defines…* land here.
4. Otherwise name the kind of evidence that would settle it: `needs-code` for a
   property of the source (how a query is built, what a handler returns, what a
   parser accepts), `needs-config` for a deployed setting (a header, a cookie
   attribute, a cipher, a TLS termination, a permission grant), `needs-people`
   for a practice you would have to ask the team about (whether update windows
   are honoured, how a third party issues an initial credential).

**The same requirement takes different dispositions in different cases, and that
is the point.** `V6.1.1` is `needs-more-prose` where the source is merely silent
about rate limiting, and `gap-from-prose` in a case whose source states that
nobody wrote the sign-in down. Write the disposition against *this* source, never
against the requirement in general — a value that depended only on the
requirement would be the table [#418](https://github.com/mstarks01/work-agent/issues/418)
removed.

There is no `pass`. If the text settles the requirement as *satisfied*, the entry
does not belong in the set at all, as above.

### 4b. Assign an action verb to every claim

Each reference claim carries a `verb` from `evals/harness/verbs.py`, chosen as
you write the claim and beside the elements you cite. It names **what the
attacker does** — never the object, which the **Element** IDs already carry, and
never the consequence.

The distinctions the vocabulary draws are the ones the corpus already draws in
its own notes: reading at rest is not reading on the wire, forging a message is
not replaying one, altering data is not destroying it. Read `GLOSS` in that
module and pick the closest; an unrecognised verb fails the corpus lint rather
than silently matching nothing.

Assign it here rather than deriving it later. Later means re-running the
decision with less context, and getting a different answer on exactly the claims
where the difference matters.

`tests/test_verb_coverage.py` fails on a claim that carries no verb, and on a
verb outside the vocabulary. Every one of the corpus's 243 claims has one, so a
new case that skips this step is the only way that test goes red.

### 5. Label the calibration pairs

**Only a package whose claim set is open and written in prose gets pairs.**
Nothing but a labelled pair can say whether two spellings name one attacker
action, so STRIDE needs them. A package whose claim names a catalog requirement
is identified by that requirement, so a prose pair adds nothing a comparison of
identifiers does not already settle, and it contributes none — settled design
([#167](https://github.com/mstarks01/work-agent/issues/167)), not an omission.
`IDENTITY_VALIDATION` in `evals/harness/calibration.py` is that table, and it
answers for every package in `PACKAGES`.

**Every package still gets a collision measurement**, whatever it composes its
identity from: keying two distinct claims alike destroys a finding and nobody
sees it go. `python -m evals.harness.run calibrate` prints one line per
package.

In the same sitting, label candidate threat pairs as match / no-match /
`unclear` / `unsupported` in `build_pairs.py`. **Write `unclear` when the two sentences alone
cannot decide it** — that is a real answer, it leaves the denominator rather
than counting against either side, and it is better than a binary the evidence
does not support. Check the bullet below on specificity first: review sitting
01 returned four `unclear` answers and all four had one cause that bullet now
settles. A pair that carries candidate element IDs carries a candidate
**verb** beside them, assigned from the candidate sentence's own words — never
by reading the reference's, which would make every pair agree by construction
and the measurement worthless. These are what the **false-split count** is measured over, and what lets the
scorer be tested with no live calls at all. The **≥90% rule–label agreement
bar** they also feed is the admission gate for a candidate rule, not a quality
statement about the shipped one. They are not ground truth: a person has read 30 of the 339, in
review sitting 01, so the bar says the identity rule reproduces what an agent
wrote and says almost nothing about whether it is right.

- **Label within a category only** — the prefilter means cross-category pairs
  are never compared.
- **Weight toward hard negatives:** same element, same category, *different
  attacker action*. Easy negatives measure nothing, and a rule that says
  "match" too readily inflates recall silently — the expensive direction to be
  wrong.
- **Include pairs that differ only in which element they cite.** Those are
  matches: matching is decided on the claim, and element agreement is scored
  separately. This rule is why element agreement alone cannot decide claim
  identity — it labels the two apart on purpose — and
  `tests/test_evals_identity.py` measures the size of the gap.
- **A better-explained write-up of one attack is still a match.** One side
  naming the credential, the cause, a figure, or the control the other leaves
  implicit does not make a second claim. **The test: does the extra text change
  what the attacker does, or only how well it is explained?** Review sitting 01
  showed this is the rule a reader cannot apply from the bullet above it — every
  `unclear` answer in that sitting was a pair where one side carried specificity
  the other lacked.
- **But a different route is a different claim.** Same fabricated data landing in
  the same store is *not* one finding when one write-up comes through a server's
  write path and the other through what a client reports: the remedies differ,
  and the route is the finding. Review sitting 01 relabelled a pair for exactly
  this, so the two bullets above have a floor under them.
- **Include candidates that assert facts the model doesn't support.** Label
  them **`unsupported`**, not `no-match`: downstream they're the "unsupported"
  bucket that counts against the tool, and that is a groundedness question the
  identity rule cannot reach. Use the label only when the invented fact is the
  *sole* separator — if the candidate also names a different place or a
  different action, the rule can decide it, so it is a `no-match` the score
  should keep.
- **Keep the set balanced;** `verify_corpus.py` fails if either label drops below
  30%.
- **Do not try to bless every label.** A person has read 30 of the 339, and the
  other 309 are not the backlog — blessing an easy paraphrase buys nothing.
  Spend a sitting on the decision boundaries: same target with a different
  action, same action against a different target, flow against endpoint naming,
  a narrower wording against a broader one, and any pair near a merge recorded
  in `verbs.UNSEPARATED`. Add a fixture when a real matcher failure turns one
  up, and record the new counts with it. A full Case Sitting (step 6) is worth
  more reading time than any of this, because it says whether the corpus itself
  is right.
- **Assign the candidate's affected element IDs on every `match` pair.** The
  sixth field of the tuple, and `verify_corpus.py` fails on a `match` pair
  without one. Answer it from the candidate sentence's own words against
  `model.json`, **before** reading the reference's element list: copying that
  list makes every pair agree by construction and the measurement in
  `tests/test_evals_identity.py` worthless. Follow the reference sets' own
  conventions — a flow, process, store or entity, one or two of them, never a
  boundary. **Every candidate is assigned, whatever its label** — the negative
  half is what prices the rule on false merges. The only exceptions are the
  handful in `verify_corpus.UNASSIGNABLE`, where the sentence names no element
  the model holds or no action the vocabulary holds; record the reason there.

### 6. Bless and merge

One reading session, one pull request, one approval. The reader signs off on
`source.md`, `model.json`, the `claims/` reference sets, and the labelled pairs
**together** — they're one artifact, and reviewing them separately loses the
property that the threat set is exhaustive *against that model*.

**There is a browser path, and it is the shorter one.**

```sh
uv run python webapp/sitting.py
```

Every case in the corpus is in a list on the left, each with a status. Pick
one — a case somebody already signed off is greyed. `--case <case-id>` opens
on one case and takes one value; the list is how you choose the rest.

It shows you the sources and the model, takes your own threat list, and only
*then* reveals the recorded sets — the one rule this method has, enforced by
the server rather than by asking. The press waits until the list says
something, so the sets cannot open on an empty box. The rule holds per case: **Previous** and
**Next** in the case header walk the list, and a case you have not written a
list for arrives blind however you reach it. Each recorded finding carries a
control that takes `agree`, `reject` or `duplicate`, which is the same mark the
by-hand path writes into a `> mark:` slot. When you finish a case it writes
that case's record into your working tree.

**One press submits every case you finished, as one pull request.** A footer
under the list reads `Submit — N cases ready`, counts them, and is the way to
the submit stage; the last **Next** ends there too. The stage lists every case
the press carries, one row each, with a **Drop**. A dropped case moves to a
held-back group with a **Put back**, and goes back to *draft in progress* in
the list — you keep every word you wrote, and the press stops carrying it. The
stage also says how many cases stay unfinished, so you never send four cases
believing you sent five. Below the list it carries the written paths, the
command and the paste text, each over every case the press carries. The button
opens the pull request through the `gh` you are already signed in to. It binds
to loopback and holds no credential of its own; `--no-submit` hides the button
and keeps the command and the paste text. `--list` prints the cases nobody has
read.

**Stop whenever you like.** The moment you post your own list, the app opens a
**Draft Sitting** for that case and keeps your list, your marks, your missing
list and your notes in it as you write them. It lives at
`~/.local/state/work-agent/sittings/<login>/<case-id>.json`, outside this
repository, and it never merges — that is what keeps an unsigned own list out
of a pull request. Close the browser, run the command again tomorrow, and the
case comes back where you left it. A successful submit deletes every draft it
carried, and a case you dropped keeps its own. *Discard this draft* on the
case throws one away and puts that case back on the list to do. A draft the
app cannot read refuses its own case and names the file in the rail; the file
is yours, so repair it or delete it, and every other case still walks.

The rest of this section is what it writes, and what you write by hand if you
would rather. Both paths end in the same files, and the checks cannot tell
them apart.

**This step is now enforced.** The act is a **Case Sitting** (see
`CONTEXT.md`), and it is recorded as an entry appended to `reviews` in
`case.json`:

```json
  "reviews": [
    {
      "submitted_by": "<the GitHub login opening the PR>",
      "submitted_for": "<who read the case: a login, or the word anonymous>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "<the file's digest>"},
        {"file": "model.json", "sha256": "<the file's digest>"},
        {"file": "claims/stride.json", "sha256": "<the file's digest>"}
      ],
      "document": "REVIEW-<the submitting GitHub login>.md",
      "notes": "<counts, and anything you changed>"
    }
  ],
```

**The entry carries two names, because they answer two questions.**

`submitted_by` is the GitHub login of the account whose PR carries the sitting.
It answers for the read. It needs a line in `evals/review/voters.toml`, which
`submit` adds for a first-timer with standing `contributor`, and it is the name
every rule reads: the clearing rule, the **Standing** on the read, the document
name and the allowlist a pull request may write inside.

`submitted_for` is who did the reading. Write your own login where you read the
case yourself. Write `anonymous` where you carry a read for somebody whose own
policy stops them taking part under their name — the field records that the
read happened and who answers for it, without naming a person who cannot be
named. It needs no roster line, it carries no standing and it clears no case,
so widening who may read costs the measurement nothing.

Pass `--submitted-for` to the app to record a read you carry:

```sh
uv run python webapp/sitting.py --submitted-for anonymous
```

Then the filled document opens with `Read by anonymous, submitted by <login>.`,
so nobody later reads the file name as the author.

### A reader with no clone: one file out, one file back

A reader who cannot install a toolchain — or whose own policy stops them
putting this repository on their machine — still reads the case. The whole
sitting goes into one standalone HTML file.

```sh
uv run python webapp/offline_sitting.py --submitted-for anonymous
```

That writes `sitting.html`, about 360 KB, carrying every case's sources,
blessed **System Model** and recorded sets, plus the digest of each file as it
stands. Send it. **The browser is the runtime**, so nothing is installed,
nothing is signed and no platform is left out.

The reader opens it, picks cases from the rail, writes their own list for a
case before that case's sets open, and marks each record `agree`, `reject` or
`duplicate`. **Download my answers** saves one JSON file. **Load a saved file**
takes it back, so a read that runs over several days needs no browser storage
and no second copy: the envelope is the save file.

They can change any mark up to the moment they send it — nothing is a record
until you import it. They cannot rewrite a case's own list once that case's
sets are open, for the reason the app refuses the same thing: a list written
afterwards would be evidence of an order that did not happen.

When the file comes back:

```sh
python -m evals.harness.run sitting-import sitting-<login>.json
python -m evals.harness.run submit sitting
```

The import treats the file as untrusted. It resolves every case id against the
corpus, re-checks the own list against the same `MIN_OWN_LIST`, refuses a mark
naming no recorded finding, and **recomputes every digest from your own tree** —
the envelope's digests only say which words the reader saw. If a read file
changed while the file was out, the import names it and writes nothing: generate
the page again and ask for that case to be re-read. One bad case writes none of
them, so you never have to work out which half applied.

Two limits worth knowing. The recorded sets are in the page's own source, so
the gate rests on the reader rather than on a server — [#373](https://github.com/mstarks01/work-agent/issues/373)
already ruled that the gate protects the evidence in the document, not the
reader. And a reader in a browser cannot edit a reference set, so a correction
arrives as prose in their notes and you make the change.

`read` pins the bytes the sitting
covered: a later PR that edits a read file puts the case back on the list
fail-closed.
`document` names the filled copy, committed beside the case, because only the
filled copy shows the method ran. The generated `REVIEW.md` carries this
entry pre-filled with the current digests.
`python -m evals.harness.run submit sitting` opens the PR.

**A sitting pull request may change an answer, never the question it answers.**
Under each case it carries `case.json`, your own `REVIEW-<login>.md` and the
`claims/` reference sets, so a correction to a recorded set travels with the
record that justifies it. `model.json` and every file `sources` lists stay
outside it — they are what you read, and a submission that edits one fails the
scope check by name.

It also carries `tests/test_case_review.py`, and there the rule is stricter
than a path: the only change it may hold is the deletion of your cases'
`UNREVIEWED` entries. That file is a module `pytest` imports, so a submission
that changed anything else in it would run code nobody asked for in every
checkout. The check compares the file to the base ref with those entries taken
out of both sides.

`tests/test_case_review.py` fails on a new case that arrives without a
clearing sitting, and names every case still waiting in its `UNREVIEWED`
list. Thirteen of the 13 cases that shipped before this was enforced are still
on that list. It names work nobody has done, not an exemption, and it is
meant to shrink to nothing.

**Why it cannot be replaced by a lint.** Review sitting 01 found a reference claim
asserting the model emits training data in a case with no training pipeline. A
mechanical version of that check — flag a claim using a word absent from the case's
source and model — fires on 231 of 243 claims, because a claim is *supposed* to
describe an attack in words the system description never uses. Narrowing to the
asset vocabulary fails too. This step is the only instrument for that class of
defect.

`REVIEW-02.md` in case 01 is the first run, and the template for the rest: read the
source and write your own threat list *before* opening the recorded one, or the
sitting measures nothing.

Take the shape from it and the mark names from a generated `REVIEW.md`. That
document is a merged reader's own words, so a vocabulary sweep stops at it, and
it still spells the marks `doubt` and `dup`. The set is now `agree`, `reject`
and `duplicate` — `MARKS` in `evals/harness/sitting.py` — and the app and the
import both refuse anything else.

Merge checklist:

- [ ] `python evals/verify_corpus.py` is green
- [ ] the model was corrected against the source text, per step 3
- [ ] `corrections.md` records every correction and the pattern behind it
- [ ] sanitization confirmed; provenance and licence recorded in `case.json`
- [ ] `sources` declared in `case.json`, one entry per input file
- [ ] every `source_label` in `model.json` names one of those labels
- [ ] every `source_excerpt` is a verbatim span of the source it cites, with `…` marking any cut
- [ ] digests and the aggregate stamped (`--write-sha`)
- [ ] tier assignment reviewed: some must-find, not all
- [ ] a reference set written for **every** framework the case declares, per step 4
- [ ] no ASVS entry asserts a requirement is satisfied — the package never reports a pass
- [ ] every `match` pair's candidate element IDs read against the candidate's own words

## Growing a case from real runs

Reference sets aren't meant to be exhaustive up front — they converge from real
output. Each scoring run surfaces grounded, plausible threats the tool produced
that simply aren't in the reference set (`unlisted_for_promotion` in the
artifact). Review the recurring ones and promote them into the reference set at
the next blessing pass — which is just steps 4–6 again, for that one case.
Promoting a threat is always a reviewed change with a human explaining why; it's
never automatic.

**A promoted threat arrives carrying `grounds`, and loses them.** A reference
threat keeps its six fields, so write step 4a's entry as you would any other and
drop the grounds on the way in. That is deliberate, not an oversight: a
hand-authored ground would be graded by nothing. Grounds are produced by a
category agent and checked against the case's real `source.md` at merge time, so
adding them here would mean extending `ReferenceThreat` *and* writing a scorer
to measure agreement between a human's choice of evidence and an agent's — which
is not a property this corpus exists to measure, and would put every reference
threat on the maintenance path for it. Do not carry what you do not grade.

The grounds are still worth *reading* before you promote. A threat whose only
ground is an `unknown-attribute` is telling you the case leaves that attribute
unstated, which is a fact about your `model.json`; an `absent-attribute` says
the case states the control is missing, which is a different fact about the
same file; and a `quote` ground points at the sentence in `source.md` that a
real reference entry should have been written from.
