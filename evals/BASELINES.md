# Contributing a Baseline

How to run this repository's corpus through your own models and contribute the
result, so the published numbers cover a configuration nobody here has
measured.

> For **contributors with a provider key** who want to add a measured
> configuration to the public record. This costs your own money. If you want to
> change the shipped model config and prove the change is an improvement, you
> want [TUNING.md](TUNING.md) — it owns the measurement loop, and this document
> only owns the contribution path. If you want to help for free, read
> [`../CONTRIBUTING.md`](../CONTRIBUTING.md): a vote and a Case Sitting both
> read merged Baselines, so they cost time and nothing else.

## What a Baseline is

One directory under `evals/baselines/`, holding up to ten sweeps of **one
configuration**, with the full report from every case in every sweep. A
**Baseline** is the unit the comparison numbers read, and the glossary entry in
[`../CONTEXT.md`](../CONTEXT.md) is its definition.

Its identity is computed from the sweeps, never typed: the clean repository
commit, the corpus digest, the requested model per tier, the resolved sampling
per tier, and the framework selection. Two sweeps that agree on all five belong
to one Baseline, so if somebody else has already measured your configuration,
your sweep joins their directory instead of starting a second one. The
directory's name is derived from that identity too, which is what makes the
collision happen at the filesystem rather than in review.

The served build the provider answered with is **not** part of the identity. It
is recorded per sweep, so a build that moved under you is a finding about the
provider rather than a second Baseline.

## Before you run

- **Commit everything first.** A sweep run over uncommitted edits cannot become
  a Baseline: the identity names a commit so a reader can open the prompts
  behind the numbers, and a dirty tree makes that name a lie. The sweep still
  runs and is still useful locally — it just cannot be contributed.
- **Push the commit, or work from one already on `main`.** The identity's
  commit has to be an ancestor of the default branch, for the same reason.
- **Run the whole corpus.** No `--case`. A picked subset selects the number the
  corpus exists to prevent, so a partial sweep stays a local tool. Narrowing by
  `--framework` *is* allowed — the full fan-out is over budget on a modest
  quota — and it keys a Baseline of its own.

## What it will cost, and how you accept it

`run` prints what the sweep is expected to cost and waits for you to accept it
before the first request. **There is no ceiling** — you may accept any amount.
The gate exists so you know what you are accepting and how good the number is,
not to cap it. [TUNING.md](TUNING.md#the-money-what-run-asks-before-it-spends)
describes the three labels, the typed acceptance, `--accept-cost` for scripts,
and what happens when a run outspends what you accepted.

Two things are worth knowing before your first sweep. Today **no merged
Baseline exists**, so there is nothing to calibrate an estimate from and the
gate will say so rather than invent a number — you will be accepting `unknown`.
And once a Baseline is merged, a later contributor sweeping the same
configuration gets a real recorded figure instead. Your first contribution is
what turns that guess into a number.

## Running and submitting

Run the corpus, once per sweep, writing each artifact wherever you like —
`evals/runs/` is gitignored and is the natural place:

```bash
python -m evals.harness.run run --mode end-to-end --out evals/runs/sweep-1.json
```

The reports land beside the artifact automatically, and a Baseline needs them:
they are what the free contribution path reads, so a sweep without its reports
cannot be submitted.

You can accumulate up to ten sweeps across as many days as you like — several
sweeps of one configuration are how the run-to-run spread becomes visible — and
then package them all at once:

```bash
python -m evals.harness.run submit baseline \
  --artifact evals/runs/sweep-1.json \
  --artifact evals/runs/sweep-2.json
```

`submit` computes the identity, lays out the directory, writes the manifest,
records what each sweep cost, stamps your login on every sweep, and opens the
pull request. Add `--dry-run` to see the checklist without opening anything.

**Read the checklist.** It is the same set of checks CI runs, so a green
checklist locally usually means a green pull request. Every check either passes
or names exactly what is wrong; this document deliberately does not list them,
because the command is the copy that cannot drift.

## What CI proves, and what it does not

CI proves the artifact **agrees with itself and with the repository**: it
loads, its commit is a clean ancestor of the default branch, the corpus digest
recomputes at that commit, the identity and name and digests all recompute, and
the recorded cost is the recorded token counts times the recorded unit prices.

It does **not** prove that a model ran. Nothing can: every hash recomputes from
values inside the same file, so a determined fabricator can produce a
consistent fabrication. That is a deliberate decision rather than an oversight.
The blast radius is small — a Case Sitting reviews the report text whatever
produced it, so even a fabricated sweep yields real judgements about real
content, and only the model attribution in a published comparison could be
wrong. Your login on each sweep is what discloses that attribution, and your
**Standing** is what every published number states beside it.

Certification is recorded, never required. A sweep of a build nobody has
blessed is the most valuable contribution here, so it cannot be the one thing
that blocks the merge; the maintainer reviewing it is the natural occasion to
bless the fingerprints it observed.

## After it merges

The directory stays. The reports it carries are what a **Review Sitting** votes
over and what the free contribution path reads, so deleting a Baseline would
break the trail behind numbers that were already published. A maintainer
revisits that when `evals/baselines/` outgrows its space, and the decision is
theirs to make in the open rather than an automatic deletion.

Your merged Baseline is then something anybody can vote over without a provider
key — see [VOTING.md](VOTING.md).
