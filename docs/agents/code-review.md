# Code review checkpoints

**A completed checkpoint round ends in an annotated tag named `reviewed/<date>`.**
That tag is the fixed point the next round starts from, so "review the code
since the last review" resolves to a command rather than to a question.

## Two instruments, two questions

Review runs twice, at two scales, and each scale sees what the other cannot.

- **A pre-merge review** reads one pull request's diff before the merge. It
  asks whether this change is correct.
- **A checkpoint round** reads a range of merged commits. It asks what the tree
  now holds that no single diff showed.

Run both. Nine audit rounds produced the evidence, and it points both ways at
once.

| Round | Findings | From the previous round's own fixes |
|---|---|---|
| 5 | 6 | 4 |
| 6 | 6 | 3 |
| 7 | 11 | 9 shared one rule that a fix gave a second reader |
| 8 | 3 | 3 |
| 9 | 2 | 1 |

A defect in a recent fix is the dominant class, and it sits inside one diff, so
a reader of that diff before the merge is the instrument that fits it.

The rest of the table is the argument for the round. A defect can be legal in
every diff that touches it and become reachable only through a later pull
request. No diff carries that fact; the tree does. The `reviewed/2026-09-08`
tag message records one case of each kind.

### What a pre-merge review reads

The diff, against the five defect classes below, and against the fix habits at
the end of this guide.

Write the result in the pull request body, under the heading
`## Five-class read`, with one entry for each class by its name in the table
below. Give each class its own entry with text after the name. CI requires the
section on each pull request that changes a code path:
`src/*`, `webapp/*`, `evals/harness/*`, `evals/*.py`, `prompts/*`,
`frameworks/*`, `domains/*`, `config/*`, `diagram_reader/*`, `tests/*`, `.githooks/*`,
`.github/workflows/*`, `.github/scripts/*`, `pyproject.toml` or `uv.lock`. An
asterisk also matches a slash, and a rename counts as a change to its old and
its new path.
The check `Five-class read` (`.github/workflows/five-class.yml`) runs again
when the body is edited. It does not read an HTML comment or a fenced code
block. `.github/scripts/five_class_read.py` holds the class names and the code
paths. `tests/test_five_class_read.py` holds the names to the table below, and
holds each path that a live workflow sweeps to the code paths.

A pull request that changes a module that decides what a prompt says the code
does also needs a `## Prompt read` section. The section has one entry for each
prompt that the check names for the module, with text after the name. The entry
says whether each outcome sentence in the prompt is still true. The table
`.github/scripts/prompt_outcomes.toml` is the one home of the pairing. Each
`outcome` row pins one prompt sentence to the modules that decide it, and each
`reader` row pairs a module with the prompts it decides where no sentence is
pinned yet. The check derives the map from modules to prompts from the table,
and reads the base branch's copy of it. A row writes `{package}` for a
framework package's directory, so a change under one package's module directory
asks for a read of that package's own texts, and a new package needs no row.
`tests/test_five_class_read.py` holds each row to the tree, holds the package
rows to `PACKAGES`, and holds every prompt to some row.
`tests/test_one_reader_lints.py` refuses a pairing written in Python.

A pull request that changes a file with a row in
`.github/scripts/held_wording.toml` names each held phrase of that file in the
same section. The entry says what the pull request did with the phrase: it
changed it by a measured change, with the ledger row, or it kept it. The check
reads the base branch's copy of this table too.

The check has a limit. GitHub runs the copy of `five-class.yml` that the pull
request carries. The workflow runs the base branch's copy of the script, but a
pull request that edits the workflow can remove that step or the whole check.
So the check does not guard a pull request that changes
`.github/workflows/five-class.yml`. The reviewer of such a pull request reads
the workflow diff.

### The standards read before the merge

The author of a diff reads it against the five classes, but the author also
believes the design is right, so the author misses the defects that come from
that design. Before the merge of each pull request that changes a code path,
a second reader reads the diff against the standards. That reader is a
sub-task or a person who did not write the diff.

Give the reader only these inputs:

- the diff of the pull request head, and the files it touches;
- `AGENTS.md`, `GLOSSARY.md` and each guide in `docs/agents/`;
- the smell list and the classes that come back, below.

Do not give the reader the author's reasons. Start the read when the head is
pushed, so it runs while CI runs. The author fixes each finding, or answers it
with evidence in the pull request body. A fix makes a new head, and a new head
needs a new read.

**The smell list.** Each smell is a judgement call, and a rule in these guides
overrides it:

| Smell | What the reader looks for |
|---|---|
| Mysterious Name | a name that does not say what the value holds or what the function does |
| Duplicated Code | the same steps in two hunks or two files |
| Feature Envy | a function that reads another object's fields more than its own |
| Data Clumps | the same few parameters or fields that always go together |
| Primitive Obsession | a bare string, number or bool that stands for a domain concept |
| Repeated Switches | the same branch on the same value in more than one place |
| Shotgun Surgery | one change that needs edits in many files |
| Divergent Change | one module that changes for unrelated reasons |
| Speculative Generality | a parameter, hook or layer that no caller needs |
| Message Chains | a caller that walks a long chain of attributes |
| Middle Man | a function or class that only passes a call on |
| Refused Bequest | a subclass that does not fit what its base says |

**The classes that come back in fixes.** The review of 2026-11-02 found these
in the fix pull requests of the review before it:

| Class | What the reader checks |
|---|---|
| A second reader | each new helper or rule: is there one reader, and does a lint refuse a second? |
| A claim with no guard | each claim that a rule has one reader (`CLAIM`): does it sit on a guard or name one in backticks? A guard is the reader of a `ONE_READERS` row or the one site of a one-site `==` assert. Only a ruling keeps a claim in `UNGUARDED_CLAIMS` |
| A closed set with no `Literal` | each table or tuple of string keys that code reads as a set |
| A default only a test omits | each new default: does a caller outside `tests/` omit it? |
| A type that says the wrong thing | each new exception: does its base describe it? |
| A name for the wrong thing | each rename: does the new name say what the value holds? |

## The five defect classes

Every finding in this repository's record — ten audit rounds, the range review
of `reviewed/2026-09-09...5571f4e` and the whole-tree hunt that followed it —
falls into one of five classes. They are distinct because each has its own test
and its own repair.

| # | Class | What it looks like here | Repair | Decidable? |
|---|---|---|---|---|
| 1 | **One rule, two readers** | the CI gate and `--list` read "is this case read" differently; a client gate that disagrees with the server's; two readers each tested against its own expectation | Delete a reader. Where two must stay, test them against each other | Partly. A scan finds a literal spelled twice; a rule needs the pair named |
| 2 | **A table with a hole** | `SERVED_TRUST` as a constant; `_FORM_RULES["openai"]` present and short; a default that answers for a row nobody asked | A table keyed by the registry, checked against the registry | Yes: `test_framework_neutrality.py`, `test_vendor_neutrality.py` |
| 3 | **A shape not listed** | `str.isdigit`; a date in a minor-version group; `window.open(…, "noopener")` returning `null`; a state nothing creates | List every shape the producer can emit, from its documentation | No. It needs the producer's documentation |
| 4 | **A bound predicted, not measured** | a cost computed from input sizes; a per-scan bound beside an unbounded count of scans | Two measurements, one admitted and one refused, and the neighbours named | No. It needs a run |
| 5 | **A fact with no reader** | an answer the page never reads; a field nothing reads; prose naming a removed thing; content placed only to satisfy a check; a refusal the page swallows | Give the fact one reader the code follows, or delete it | Mostly: `test_dead_code_lints.py`, `test_reader_lints.py` |

Class 5 is the largest by count and the cheapest to find, because most of it is
a grep. Class 1 is the largest by damage: it holds the fixes that passed their
tests in runs 5 to 9, and #640. Measured on 2026-09-06, a
whole-tree sweep of the decidable classes over code older than the last round
found nothing that breaks a flow; the findings that mattered sat in code
younger than the round. So read `git diff reviewed/<last>...HEAD` and the fix
diffs first. Then sweep the whole tree for each finding, as
[Sweep the tree for each finding](#sweep-the-tree-for-each-finding) says.

Put the result in the pull request, so the record sits with the change. A
pre-merge review cuts no tag.

### What the round reads after that

The round reads the same range. Its question narrows, because each pull request in the
range already had a reader: the round hunts across pull requests, over the whole
tree, and on surfaces no diff touched. The tag message names which pull requests
in the range got a pre-merge review, so the next round knows which half of the
range is new ground.

### Sweep the tree for each finding

**A defect that a review finds must not exist anywhere else in the tree.**
This applies to each finding, however small: a bug, a stale name, a word that
`GLOSSARY.md` gives another meaning, a wrong sentence in a doc, or damaged data
in a ledger row.

- Sweep the whole tree for the exact mechanism of each finding, not for a
  general pattern. A general pattern returns noise.
- Skip a sweep only with a stated reason, for example a lint that already
  refuses the shape, or a search that proves one site. Put the reason in the
  report.
- Fix each instance that the sweep finds, in the same way as the finding.
- When a mechanism comes back in a later round, add a lint that refuses it.
  Then it cannot come back again.
- Count the findings in three groups:
  - code newer than the last `reviewed/*` tag;
  - older code, for a class whose definition did not change in this round;
  - older code, for a class whose definition this round made wider, such as
    new claim words or a wider scope.

  The second count must go down from round to round. A count that does not go
  down shows a mechanism that needs a lint. The third count shows old defects
  that the narrower definition did not count, not new defects.

### Read the prompts against the code they describe

A third axis, and it is the one no diff shows. A prompt states facts the code
owns — which fields admit `unknown`, what a flow ID looks like, what the
service does with a repeated ID, which quotes it checked. Each sentence is
true when it is written, and it goes stale in silence, because the prompt and
the code are read by different people on different days and nothing compares
them.

The decidable half is linted: `tests/test_prompt_lints.py` holds each closed
vocabulary, the verb menu, every computed key a critic is shown, and every verb
a lane may file. **Grow that table when a round finds a new pair** — a one-line
entry there is worth more than the finding it replaces.

A sentence that states an outcome of the code, such as a refusal, a repair, a
drop or an open question, goes in an `outcome` row of
`.github/scripts/prompt_outcomes.toml`, with a check function of the row's
name in `tests/test_prompt_outcomes.py`. One test runs over the rows: it checks
the sentence and runs the row's check, so a change to either side fails it. A
row names the `validation.IssueCode` values its sentence is the rule against,
and the test holds the rows to that set. Where a prompt can take a sentence
from a table that the code reads, render the sentence from the table instead,
as `render_predicates` does.

Wording in model text that breaks a wording rule, such as the SPEND lint or
the GLOSSARY Element term, changes only with a measured prompt change. The
maintainer rules such wording held until that change lands, and
`.github/scripts/held_wording.toml` holds one row for each held phrase, with
the rule and the reason. A round
reads that table and does not report a held row as a finding. The SPEND and
MONEY lints read model text and skip only the held rows of their rule.

An exemplar teaches the model a draft shape, so it must pass every check that
real lane output meets. `tests/test_exemplar_fan_in.py` sends each exemplar
draft through `fan_in` against a fixture of its exemplar system. A new check in
the fan-in covers the exemplars with no new lint.

The rest is judgement and is this axis's real work. Run it in each round.
Read every shipped example against the rule above it, and every reference label against the policy the
prompt states. No lint sees this kind of defect. The #1082 audit lists eleven,
for example an exemplar that places a component from a sentence about what
reaches it, a lane skill that invites an action its own table refuses, and a
corpus label that routes a question the other way from the output contract.

## Find the last checkpoint

```
git tag -l 'reviewed/*' --sort=-creatordate | head -1
```

Read what it covered, and what it deliberately left:

```
git show reviewed/2026-08-21
```

Then diff against it:

```
git diff reviewed/2026-08-21...HEAD
git log reviewed/2026-08-21..HEAD --oneline
```

Three dots, so the comparison runs from the merge-base.

## Record a new one

Tag the **head of the range the review covered**, never a later one. The PR
that fixes the findings lands after the tag, so the next review reads it as the
first commits of its own range. A tag on the fix PR puts that PR outside every
review: the review before it ended at the range head, and the review after it
starts at the tag.

```
git tag -a reviewed/<date> <sha> -F -
git push origin reviewed/<date>
```

`<date>` is **the day the round ran**. Rounds run several times a day, so the
name collides; a second round that day takes a letter, `reviewed/2026-08-27b`,
and a third takes `c`.

Tags `reviewed/2026-09-04` to `reviewed/2026-09-22` carry names that are not
their dates. Each tag message carries the true date, and nothing reads the
name. Today's plain name may already be taken, so take the letter.

Sort by `creatordate`, never by name. `creatordate` is the order that answers
"since when".

The message carries what a later reader cannot recover from the diff:

- the range reviewed, as `<base>...<head>`, with the size at the time
- which pull requests in the range had a pre-merge review, so the next round
  knows which half of its own range nobody read before the merge
- which axes ran, and what each read as its source of truth
- how many findings each axis produced, and where each was fixed
- how many findings were in each of the three count groups: newer code, older
  code under an unchanged definition, and older code under a wider definition
- **what was left open by decision**, with the reason — this is the part that
  stops the next review re-reporting a settled question as a new finding.
  Held wording in model text goes in `.github/scripts/held_wording.toml`, and
  the message points to that table instead of listing the rows

## Why a tag

The checkpoint is a fact about a commit, so it belongs on the commit. A date
in a guide drifts the moment somebody rewrites the guide, and a note in a
tracker needs a network call and a lookup to answer "since when". A tag is in
every clone, needs no service, and is what `git diff` already takes.

`reviewed/*` sits beside the repository's other tag namespaces, `archive/*`
and `backup/*`, and reads the same way: the branch is gone, the tag is the
record.

## Habits that fixes need

Each habit answers a defect that an audit round found in the previous round's
fixes. Every one of those defects passed the tests that shipped with it, so
these are rules about the fix itself, not about testing harder. The tag
messages from `reviewed/2026-09-03` to `reviewed/2026-09-09` record them.

A fix is the riskiest code in the tree. It is new, it lands fast, and the
attention that found the defect is gone by the time the repair is written.

### Review your own fix the way you reviewed the defect

Read the fix diff against the five defect classes above, the same way a
pre-merge review reads anybody else's diff. This includes the fix that closes a
finding you reported an hour ago.

Let a fix to a hot path sit long enough to read it once more. Do not merge a
fix minutes after the last keystroke.

**Read it a second time for idiom, not only for the five classes.** The class
read asks whether the fix is correct; this one asks whether it reads like the
code around it. Load the `python-style` skill and check the fix against the
conventions of the files it lands in — a name that means one thing here and
another thing thirty lines up, a helper named for a verb it does not perform, a
loop of asserts where the file beside it parametrizes, a block spelled twice
where the class already holds the helper.

None of that changes behaviour, which is exactly why the class read walks past
it and why it survives into the tree. PR #992 is that second read over PR #991.

### Prefer deleting a reader over adding a guard

When the defect is a rule with two readers, make one reader call the other. A
guard copied into the second reader contains this defect and leaves the class
open; one shared helper removes the class.

Where two readers must stay, test them against each other, so they cannot
disagree in silence. That is the fallback, not the fix to reach for first. The
`reviewed/2026-09-09` tag message records a case.

### Add the lint that refuses a second reader

A fix that makes one reader adds, in the same pull request, a lint that refuses
a second reader. The lint walks the tree for the expression that applies the
rule and names each site; every site but the one reader fails it. Put it in
`tests/test_one_reader_lints.py`, or in the lint file that holds that kind of
rule. Show that it fails on a deliberate second reader, then revert that.

The lint reads a named file set from `tests/source_tree.py`, such as `PYTHON`
or `guide_files()`, and never a list of roots of its own. If the lint must read
less than a set, name each part it leaves out with `without` and give the
reason. A lint module declares itself with `pytestmark = pytest.mark.lint`.

Without the lint, each new reader of the rule needs a pull request of its own.
Where no AST pattern can find the rule, say so in the pull request body and
name the test that holds the readers against each other.

### Make the harness that proved the bug the regression test

The input that reproduced the defect is the honest one. A simpler test written
after the repair tends to check the repair rather than the defect, and it passes
for a reason the author chose.

### Ship no bound without two measurements

A constant that bounds work needs a measured case it **must admit** and a
measured case it **must refuse**, and the commit message carries both numbers.

A cost computed from the input sizes can order real cases backwards. On the
repair rung, English prose at 288M ran 0.39 s, and a repetitive source at 112M
ran 5.95 s. No reading of the code produces that; only running it does.

If you cannot produce both numbers, the bound is a guess. Say so, or measure.

Then name what sits beside the new bound. A ceiling makes the next unbounded
value the weak one, so the pull request body lists the neighbours and says which
of them are bounded. A bound on one scan beside an unbounded count of scans
bounds nothing.

### When a fix breaks an existing test, suspect the fix

The default assumption is that the test is right and the fix is wrong. Read the
test and find out what it protects before you touch its premise.

`test_rekey_refuses_a_move_the_components_cannot_satisfy` protects one such
premise. `rekey` reads a ledger written before a rule change, so a fix that asks
a ledger row to prove itself against the *current* rule makes `rekey`
impossible to run.

A test that fails on a correct change is a real thing, and pinned lists move
that way. It is the second explanation to reach for, not the first.
