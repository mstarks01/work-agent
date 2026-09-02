"""The sweep artifact: one shape, written once and read through one loader.

## Why this is a module

``command_run`` used to build the artifact as a dict literal and
:mod:`evals.harness.stability` used to read it back with ``raw.get("scores")``.
Two hand-kept halves, agreeing only by habit — and a key the writer stopped
producing read as a sweep that measured nothing, which is a plausible number
rather than an error.

The keys are now declared: the envelope's here, and every instrument's on its
own entry in :data:`~evals.harness.instruments.INSTRUMENTS`. :func:`build`
writes exactly that set and :func:`load_artifact` refuses a file missing any of
it, so ``ARTIFACT_VERSION`` guards a described shape rather than a number.

## The envelope

:data:`ENVELOPE_KEYS` is the sweep's own facts — what ran, what it ran on, what
answered, whether the run finished, and whether the result is trusted. They are
not readings over the claims, which is why no instrument owns them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from analysis_service.certification import CertifyResult
from analysis_service.report import NodeLatency, TokenUsage
from evals.harness.instruments import INSTRUMENTS, Sweep, artifact_blocks
from evals.harness.provenance import ProvenanceError, RunProvenance

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "evals" / "corpus"

# The eval artifact's schema version. Bump it for any change to the declared
# keys, and promotion will reject the older files by name rather than
# half-understanding them.
#
# * Version 5 adds ``series``: which standings each published series reads,
#   and the scored blocks for every series but the primary one (#326). The
#   top-level scored keys are the primary series — maintainer votes only —
#   where before they read every vote regardless of standing.
# * Version 4 adds ``stopped``: the cases the estimate gate's hold prevented,
#   empty on a sweep that ran to the end (#334). A partial sweep that could
#   not say so would read as a whole one.
# * Version 3 adds ``frameworks``: the selection the sweep actually ran, one
#   of a Baseline's five identity parts (#321), recoverable before only by
#   inference over blocks that write their keys whether or not a framework ran.
# * Version 2 adds the two keys that say which *repository state* produced a
#   sweep, beside the ``provenance`` block that already said which models did.
ARTIFACT_VERSION = 5

#: What a recorded artifact carries where the fact was never captured. Only
#: the sweeps taken before version 2 hold it: :func:`build` computes both keys
#: or raises, so a new artifact can never claim it.
#:
#: A required field that honestly records a deficient provenance, which is the
#: ``bootstrap`` field's design on ``case.json`` — that one stayed true for a
#: year because nothing could omit it. Backfilling a commit inferred from a
#: run's date would be the opposite: a guess at what produced a number,
#: indistinguishable afterwards from an observation.
UNRECORDED = "unrecorded"


#: Files in a case directory that no number is computed from. Everything else
#: is graded against, so the digest is taken by **subtraction**: a file added
#: to a case counts until somebody rules it out, and the cost of being wrong is
#: a digest that moves too often rather than one that misses a changed
#: reference set.
UNGRADED = ("corrections.md",)


def _ungraded(name: str) -> bool:
    return name in UNGRADED or name.startswith("REVIEW")


class RepoCommit(BaseModel):
    """Which commit the sweep ran from, and whether the tree matched it.

    ``clean`` is not a formality. A sweep run over uncommitted prompt edits is
    the ordinary tuning loop — ``TUNING.md`` step 3 asks for exactly that — so
    refusing one would break the workflow this key exists to describe. Naming a
    commit the working tree did not match, without saying so, would be worse:
    the artifact would look reproducible and not be.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: A full or abbreviated hex sha, or :data:`UNRECORDED`. Bounded because a
    #: recorded commit reaches ``git`` argv in
    #: option position -- ``git ls-tree -r -z <commit> --`` -- so a value opening
    #: with ``-`` is read as an option rather than a commit. Every path fails
    #: closed today, and the result is a silently skipped check rather than an
    #: injected argument; a shape the field can state is better than a failure
    #: mode the reader has to work out.
    commit: str = Field(pattern=rf"^([0-9a-f]{{7,40}}|{UNRECORDED})$")
    #: ``None`` exactly when the commit was never recorded. A recorded sweep
    #: always knows, so a missing answer and "the tree was clean" stay distinct.
    clean: bool | None = None

    @model_validator(mode="after")
    def _clean_is_known_iff_the_commit_is(self) -> RepoCommit:
        if (self.commit == UNRECORDED) != (self.clean is None):
            raise ValueError(
                f"commit={self.commit!r} and clean={self.clean!r} disagree about"
                f" whether this sweep's repository state was recorded; {UNRECORDED!r}"
                " takes clean=None and nothing else does"
            )
        return self


def repo_commit() -> RepoCommit:
    """The commit this tree is on, and whether it has uncommitted changes.

    Raises rather than falling back to :data:`UNRECORDED`. A sweep costs money
    and the answer is free, so a repository that cannot say what it is running
    should stop before it spends rather than write an artifact that cannot say
    what produced it.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProvenanceError(
            f"cannot read the commit this sweep would run from: {exc}. An"
            " artifact that cannot name the prompts and reference sets behind"
            " its numbers is not worth what the sweep costs."
        ) from exc
    return RepoCommit(commit=commit, clean=not status.strip())


def corpus_digest() -> str:
    """One digest over every corpus file a number is computed from.

    Keyed on the path as well as the bytes, so a case renamed to another case's
    content moves the digest. Sorted, so it does not depend on how the
    filesystem happened to walk.
    """
    digest = hashlib.sha256()
    for path in sorted(CORPUS_DIR.rglob("*")):
        if not path.is_file() or _ungraded(path.name):
            continue
        digest.update(path.relative_to(CORPUS_DIR).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


@dataclass(frozen=True)
class EvalArtifact:
    """A loaded sweep artifact: its provenance, plus the run that produced it.

    The context fields are not promotion inputs — nothing here changes which
    fingerprints get blessed — but an operator approving a promotion should see
    that the sweep they are certifying reported structural failures or read as
    untrusted. Refusing on them would be the wrong call: whether to bless a
    sweep with one failed case is a judgement, and the tool's job is to make
    sure it is a *made* one.

    ``raw`` is the parsed document, kept whole. Promotion needs none of it, but
    the artifact is the only record a finished sweep leaves, and an offline
    instrument reading some other block of it should go through the loader that
    decides an artifact is admissible rather than opening the file a second time
    under its own rules. Read a block with :meth:`block` rather than off ``raw``:
    the accessor raises on a key this version declares and the file does not
    carry, where ``raw.get`` returned an empty measurement.
    """

    path: Path
    mode: str
    cases: tuple[str, ...]
    trusted: bool
    structural_failures: tuple[str, ...]
    provenance: RunProvenance
    commit: RepoCommit
    corpus_digest: str
    raw: dict[str, Any]

    def block(self, key: str) -> Any:
        """One declared block of the artifact, by key.

        **Raises rather than defaulting.** ``raw.get("scores") or ()`` is what
        this replaces, and it read a sweep whose scores block went missing as a
        sweep that scored nothing — the same number, from opposite facts.

        Checked here rather than at load, because which blocks a reader needs is
        the reader's own business: ``promote`` works from ``provenance`` alone
        and is credential-free by design, so holding every artifact to every
        key would refuse the files promotion exists to read.
        """
        if key not in DECLARED_KEYS:
            raise ProvenanceError(
                f"{key!r} is not a key an artifact declares; the declared set is"
                f" {sorted(DECLARED_KEYS)}"
            )
        if key not in self.raw:
            raise ProvenanceError(
                f"{self.path}: carries no {key!r} block, though an"
                f" artifact_version {ARTIFACT_VERSION} sweep writes one"
            )
        return self.raw[key]


def load_artifact(path: Path | str) -> EvalArtifact:
    """Read a sweep artifact's provenance, or refuse it.

    The version is checked before the shape, as
    :func:`~analysis_service.certification.load_manifest` does: an artifact from
    another schema should be named as such, not reported as a heap of stray
    keys. There is no best-effort read of an unversioned artifact — those
    predate served identities entirely, so anything recovered from one would be
    a guess at what answered.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ProvenanceError(f"{path}: cannot be read: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProvenanceError(f"{path}: not an eval artifact (expected a JSON object)")

    version = raw.get("artifact_version")
    if version != ARTIFACT_VERSION:
        raise ProvenanceError(
            f"{path}: unsupported artifact_version {version!r}; expected"
            f" {ARTIFACT_VERSION}. An artifact without one predates recorded"
            " served identities and cannot be promoted — re-run the sweep"
        )

    block = raw.get("provenance")
    if not isinstance(block, dict):
        raise ProvenanceError(
            f"{path}: no provenance block, so nothing records what actually"
            " served this run"
        )

    stored = block.get("generation_identities")
    record = {
        key: value for key, value in block.items() if key != "generation_identities"
    }
    try:
        provenance = RunProvenance(**record)
    except ValidationError as exc:
        raise ProvenanceError(f"{path}: malformed provenance: {exc}") from exc

    derived = {
        tier: identity.to_json()
        for tier, identity in provenance.tier_identities().items()
    }
    if stored != derived:
        raise ProvenanceError(
            f"{path}: generation_identities disagrees with the node_runs it is"
            " derived from; the artifact has been edited and will not be promoted"
        )
    provenance.verify()
    return EvalArtifact(
        path=path,
        mode=str(raw.get("mode", "")),
        cases=tuple(str(case) for case in raw.get("cases", ())),
        trusted=bool(raw.get("trusted", False)),
        structural_failures=tuple(
            str(failure) for failure in raw.get("structural_failures", ())
        ),
        provenance=provenance,
        commit=_commit_of(path, raw),
        corpus_digest=str(raw.get("corpus_digest", UNRECORDED)),
        raw=raw,
    )


def _commit_of(path: Path, raw: Mapping[str, Any]) -> RepoCommit:
    """The artifact's repository state, or a refusal naming the file."""
    try:
        return RepoCommit(**raw.get("repo_commit", {}))
    except ValidationError as exc:
        raise ProvenanceError(f"{path}: malformed repo_commit: {exc}") from exc


#: The sweep's own facts, as opposed to a reading over its claims. One writer,
#: so they are a literal here rather than a table.
ENVELOPE_KEYS: tuple[str, ...] = (
    "artifact_version",
    "mode",
    "cases",
    "models",
    "gating",
    "certification",
    "provenance",
    "node_usage",
    "node_latency",
    "structural_failures",
    "mode_output",
    "trusted",
    "repo_commit",
    "corpus_digest",
    "frameworks",
    "stopped",
    "series",
)

#: Every key an artifact of this version carries. The envelope's, plus each
#: instrument's own declaration — so an instrument added to the table is a key
#: the loader starts requiring, with no edit here.
DECLARED_KEYS: frozenset[str] = frozenset(ENVELOPE_KEYS).union(
    *(instrument.keys for instrument in INSTRUMENTS.values())
)


def build(
    *,
    mode: str,
    cases: Sequence[str],
    models: Mapping[str, Any],
    certification: CertifyResult,
    provenance: RunProvenance,
    usage: Mapping[str, TokenUsage],
    latency: Mapping[str, NodeLatency],
    structural_failures: Sequence[str],
    payloads: Sequence[Mapping[str, Any]],
    trusted: bool,
    sweep: Sweep,
    commit: RepoCommit,
    corpus: str,
    series: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole artifact: the sweep's envelope, plus every instrument's keys.

    ``provenance`` is what actually generated, per node execution — the record
    ``promote`` reads back. It replaces the ``node_fingerprints`` map, which
    carried the hashes without the served builds they were computed from, so a
    promotion could not be driven from a finished sweep at all
    ([#117](https://github.com/mstarks01/work-agent/issues/117)).

    ``trusted`` rides beside the aggregates so nothing downstream folds an
    uncertified run into a trusted number unaware.

    ``commit`` and ``corpus`` are passed in rather than computed here, because
    a sweep that cannot name its own repository state should stop before it
    spends the money — ``command_run`` resolves both in its first second. By
    the time this runs, the answer has already been paid for.
    """
    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "mode": mode,
        "cases": list(cases),
        "models": dict(models),
        "gating": "tier-1-structural-only",
        "certification": certification.to_json(),
        "provenance": provenance.to_json(),
        "node_usage": {node: entry.model_dump() for node, entry in usage.items()},
        "node_latency": {
            node: {**entry.model_dump(), "mean_ms": round(entry.mean_ms)}
            for node, entry in latency.items()
        },
        "structural_failures": list(structural_failures),
        "mode_output": list(payloads),
        "trusted": trusted,
        "repo_commit": commit.model_dump(),
        "corpus_digest": corpus,
        # The selection that ran, off the graphs that were built — one of a
        # Baseline's five identity parts (#321), so it is a field the code
        # reads rather than an inference over per-framework blocks.
        "frameworks": sorted(sweep.run.frameworks),
        # The cases the estimate gate's hold stopped before. Empty is the
        # ordinary answer; a non-empty list is what stops a partial sweep
        # reading as a whole one, and it fails the Baseline full-corpus rule
        # by construction.
        "stopped": list(sweep.run.stopped_before),
        # Which standings each series reads, and the non-primary series'
        # numbers. ``None`` from a mode that scored nothing, which is a sweep
        # with no series rather than a sweep whose series were empty.
        "series": dict(series) if series is not None else {},
        # Every instrument's own keys, from the table that also printed them.
        # One source for the printed line and the written number is what stops
        # the two disagreeing.
        **artifact_blocks(sweep),
    }
    surplus = sorted(set(artifact) - DECLARED_KEYS)
    if surplus:
        raise ProvenanceError(
            f"the sweep wrote keys this artifact version does not declare:"
            f" {surplus}. Declare them on the instrument that owns them."
        )
    return artifact
