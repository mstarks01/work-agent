"""A **Baseline**: one directory, one configuration, up to ten sweeps.

Merged baselines live at ``evals/baselines/<derived-name>/``, which is the
public record, beside ``evals/runs/``, the private scratch area (#321). A
Baseline's identity is computed from its artifacts, and nobody types it. It has
five parts: the clean repository commit, the corpus digest, the requested model
per tier, the resolved sampling per tier, and the framework selection. Two
sweeps that agree on all five belong to one Baseline. The served build is an
observation about a sweep rather than part of the identity, so fingerprints that
drift inside one Baseline are a finding about the provider rather than a second
Baseline.

The directory name is derived, from the short commit, the strong-tier model
slug, and an 8-hex prefix of the identity hash. Two contributors who sweep one
configuration therefore collide at one directory, and the filesystem enforces
"same baseline" rather than review attention. ``baseline.json`` is the manifest,
and holds the identity plus one entry per sweep. CI re-derives all of it and
fails a mismatch, on the provenance-summary pattern: a stored view that
disagrees with the record is corruption rather than a second opinion.

What verification can and cannot prove is #323's decision. Every check here
shows the artifact agrees with itself and with the repository, and never that a
model ran. A fabricator can compute correct hashes for a fabrication. The
``submitted_by`` label on each sweep is the disclosure, and the standing behind
it derives from the one roster at read time. The cost check is pure arithmetic
over the manifest's own recorded unit prices, so an honest artifact never starts
failing because the live price map moved.

On security: the digests make silent edits loud (A08), the loads fail closed
(A10), and nothing here trusts a label it can recompute. The name, the manifest
and every file hash are evidence rather than assertions (A08 again).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from analysis_service.model_tiers import TierName
from analysis_service.vendors import vendor_for_route

# The tier whose model names a baseline. Spelled, not indexed: this read
# ``TIER_NAMES[-1]`` until `review` was appended to the vocabulary and every
# baseline silently became ``<commit>-unknown-<hash>``. A position in a
# vocabulary is not a fact about which tier does the analysis.
_NAMING_TIER: TierName = "strong"
from analysis_service.report import TokenUsage
from evals.harness.archive import archive_bytes
from evals.harness.artifact import (
    REPO_ROOT,
    EvalArtifact,
    ProvenanceError,
    _ungraded,
    load_artifact,
)
from evals.harness.prices import UnitPrices, price_calls

EVALS_ROOT = Path(__file__).resolve().parents[1]
BASELINES_DIR = EVALS_ROOT / "baselines"

#: One Baseline holds at most this many sweeps (#321). A contributor may
#: submit one and add more in later PRs; the published numbers state the
#: run count the same way they state the voter standings.
SWEEP_CAP = 10


class BaselineError(ValueError):
    """The directory, an artifact or the manifest breaks a Baseline rule."""


def _slug(model: str) -> str:
    """The readable half of a model name: its last path segment, tamed."""
    return re.sub(r"[^a-z0-9._]+", "-", model.rsplit("/", 1)[-1].lower()).strip("-")


@dataclass(frozen=True)
class BaselineIdentity:
    """The five computed parts. Equality is Baseline membership."""

    repo_commit: str
    corpus_digest: str
    models: tuple[tuple[str, str], ...]
    sampling: str
    frameworks: tuple[str, ...]

    @classmethod
    def from_artifact(
        cls, artifact: EvalArtifact, *, as_baseline: bool = True
    ) -> BaselineIdentity:
        """The five parts, computed from one sweep.

        ``as_baseline`` turns on the **Baseline's own rules** rather than the
        identity's, so :func:`configuration_label` can name a sweep a Baseline
        refuses. Pass ``False`` only where the caller says what a refused sweep
        means; a Baseline's answer is that it means nothing it can publish.

        The rules are :data:`BASELINE_RULES`, and both are about whether a
        later reader can reproduce what was run. This raises on the first one
        a sweep fails; :func:`configuration_label` appends every failed rule's
        marker instead, and the two read one table.
        """
        identity = cls._parts(artifact)
        if not as_baseline:
            return identity
        for rule in BASELINE_RULES:
            if why := rule.unmet(artifact, identity):
                raise BaselineError(f"{artifact.path}: {why}")
        return identity

    @classmethod
    def _parts(cls, artifact: EvalArtifact) -> BaselineIdentity:
        """The five parts and nothing else, with no Baseline rule applied."""
        requested: dict[str, set[str]] = {}
        for execution in artifact.provenance.executions:
            requested.setdefault(execution.tier, set()).add(execution.requested_model)
        conflicted = {tier for tier, models in requested.items() if len(models) > 1}
        if conflicted:
            raise BaselineError(
                f"{artifact.path}: more than one requested model on"
                f" tier(s) {sorted(conflicted)}; the identity carries one"
                " requested model per tier"
            )
        sampling = json.dumps(
            {
                tier: block.model_dump()
                for tier, block in artifact.provenance.sampling.items()
            },
            sort_keys=True,
        )
        return cls(
            repo_commit=artifact.commit.commit,
            corpus_digest=artifact.corpus_digest,
            models=tuple(
                sorted((tier, models.pop()) for tier, models in requested.items())
            ),
            sampling=sampling,
            frameworks=tuple(
                sorted(_framework_name(name) for name in artifact.block("frameworks"))
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_commit": self.repo_commit,
            "corpus_digest": self.corpus_digest,
            "models": dict(self.models),
            "sampling": json.loads(self.sampling),
            "frameworks": list(self.frameworks),
        }

    @property
    def hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_json(), sort_keys=True).encode("utf-8")
        ).hexdigest()

    @property
    def name(self) -> str:
        """``<short-commit>-<strong-model-slug>-<hash8>``, e.g. ``7c3a007-gpt-5.6-3f9a1c2e``."""
        strong = dict(self.models).get(_NAMING_TIER, "unknown")
        return f"{self.repo_commit[:7]}-{_slug(strong)}-{self.hash[:8]}"


class _BaselineRule(NamedTuple):
    """One rule a **Baseline** applies on top of the five identity parts.

    ``unmet`` answers with the sentence a Baseline refuses the sweep with, or
    with ``""`` where the sweep satisfies the rule. ``marker`` is what a vote's
    label carries instead, for a sweep that runs anyway.
    """

    marker: str
    unmet: Callable[[EvalArtifact, BaselineIdentity], str]


def _unmet_clean(artifact: EvalArtifact, identity: BaselineIdentity) -> str:
    if artifact.commit.clean is True:
        return ""
    return (
        "ran on a tree that did not match its commit; a Baseline's identity"
        " names a commit so a reader can open the prompts behind the numbers,"
        " and a dirty sweep cannot"
    )


def _unmet_one_provider(artifact: EvalArtifact, identity: BaselineIdentity) -> str:
    unnamed = sorted(
        {
            vendor.name
            for _, route in identity.models
            if not (vendor := vendor_for_route(route)).routes_to_one_provider
        }
    )
    if not unnamed:
        return ""
    return (
        f"ran on {unnamed}, whose route may be served by more than one upstream"
        " provider; a Baseline's sweeps have to be comparable, so it is not"
        " named after a route that does not say which weights answered. Run the"
        " sweep and read it; do not publish it as a Baseline"
    )


#: Every rule a **Baseline** applies beyond the five parts, each with the
#: marker a vote's configuration label carries when a sweep fails it.
#:
#: **One table, two readers that cannot drift.**
#: :meth:`BaselineIdentity.from_artifact` raises on the first entry that fires
#: and :func:`configuration_label` appends every firing entry's marker. Each
#: rule says something the five parts cannot, so each needs a marker of its
#: own: a dirty tree means the commit does not describe the prompts, and a
#: route in front of more than one provider means the models do not describe
#: the weights. The second rule shipped with the refusal and no marker, so a
#: vote on an aggregator's sweep carried a label that read as reproducible.
BASELINE_RULES: tuple[_BaselineRule, ...] = (
    _BaselineRule(marker="-dirty", unmet=_unmet_clean),
    _BaselineRule(marker="-multiprovider", unmet=_unmet_one_provider),
)

#: Kept as a name because it reads in a message and in a test. The table above
#: is what appends it.
DIRTY_MARKER = BASELINE_RULES[0].marker


def configuration_label(artifact: EvalArtifact) -> str:
    """Which configuration produced a sweep, as a vote records it.

    A vote's ``config`` and a Baseline's name answer one question -- which
    models, sampling, frameworks, commit and corpus produced the finding -- so
    one reader answers it for both (#802). A second spelling composed in
    ``webapp/review.py`` would name one sweep two ways, and nothing compares
    the two.

    A sweep a Baseline refuses is labelled rather than refused here.
    ``evals/TUNING.md`` step 3 runs one from an edited tree on purpose, and a
    reviewer's answer about a finding is worth keeping whatever produced it.
    What a Baseline may not publish and what a vote may record are different
    questions.

    Every rule it fails contributes its marker, from :data:`BASELINE_RULES`.
    A rule with a refusal and no marker would let this label a sweep as if the
    rule held, which is the reading a marker exists to remove.
    """
    identity = BaselineIdentity.from_artifact(artifact, as_baseline=False)
    return identity.name + "".join(
        rule.marker for rule in BASELINE_RULES if rule.unmet(artifact, identity)
    )


def money(raw: Any) -> float | None:
    """One recorded figure in dollars, or ``None`` where it is not money.

    **The one rule for what money looks like in a file a contributor writes**,
    and two questions read it: what a manifest recorded for a whole sweep, and
    what an artifact recorded for one node. A second copy of these rules would
    admit in one place what it refused in the other.

    A manifest is a contributor's file, and `float()` accepts "inf" and "nan": a
    non-finite or negative figure poisons a mean, a total and a comparison, and
    renders as an acceptable offer in the consent gate. `math.isfinite` guarded
    the consent path alone, which left the published table, the contribution
    summary and the baseline re-check reading the same field without it.

    The shape is the one :meth:`SweepCost.to_json` writes: a JSON number. A
    string is refused even when `float()` would read it -- "1_000" and
    non-ASCII digits both would -- and so is a boolean, which is an `int` to
    `float()`. A JSON integer too large for a float raises `OverflowError`,
    which is not a `ValueError`.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def recorded_usd(cost: Any, key: str = "actual_usd") -> float | None:
    """A committed manifest's recorded dollars, or ``None`` if it is not money.

    ``key`` names which figure: ``actual_usd``, the arithmetic over recorded
    tokens, or ``reported_usd``, what the providers said they charged. One
    reader for both, because :func:`money` decides what a figure has to look
    like and neither question changes that.

    ``cost`` itself is whatever the manifest holds under that key, and a scalar
    there is not a cost.
    """
    if not isinstance(cost, Mapping):
        return None
    return money(cost.get(key))


@dataclass(frozen=True)
class SweepCost:
    """What one sweep spent, priced with stated rates and stated holes."""

    unit_prices: tuple[UnitPrices, ...]
    unpriced: tuple[str, ...]
    fallbacks: tuple[tuple[str, str], ...]
    actual_usd: float
    #: What the providers said they charged, summed over the sweep, or ``None``
    #: where none of them said anything — which is every sweep on a direct
    #: vendor.
    #:
    #: **Beside ``actual_usd`` and never instead of it.** The two answer
    #: different questions: one is recorded tokens at recorded rates, which any
    #: reader can recompute from the artifact, and the other is what an account
    #: was charged, which no reader can derive. A sweep on a gateway route has
    #: only the second, because ``unpriced`` holds every model in it.
    reported_usd: float | None

    def to_json(self) -> dict[str, Any]:
        return {
            "unit_prices": [prices.to_json() for prices in self.unit_prices],
            "unpriced": list(self.unpriced),
            "fallbacks": dict(self.fallbacks),
            "actual_usd": self.actual_usd,
            "reported_usd": self.reported_usd,
        }


def usage_of(artifact: EvalArtifact) -> dict[str, TokenUsage]:
    """One sweep's recorded token usage per node.

    Public because the estimate gate reprices these same counts at another
    run's rates (#324), and two readers of one block are two chances to
    disagree about its shape.
    """
    return {
        node: TokenUsage(**fields)
        for node, fields in artifact.block("node_usage").items()
    }


def charges_of(artifact: EvalArtifact) -> dict[str, float]:
    """One sweep's recorded charges per node, as the providers reported them.

    Public for the reason :func:`usage_of` is: the block has more than one
    reader, and two readers of one block are two chances to disagree about its
    shape.

    A figure that is not money is dropped rather than summed.
    ``node_charges`` is written by :func:`evals.harness.artifact.build` from a
    fold this repository owns, so a bad value there means a hand-edited
    artifact — and :func:`money` is the rule that says what a recorded figure
    has to look like, wherever it was written.
    """
    return {
        node: value
        for node, raw in artifact.block("node_charges").items()
        if (value := money(raw)) is not None
    }


def _node_model(artifact: EvalArtifact, node: str) -> tuple[str, str]:
    """(served, requested) for a node, from its last recorded execution."""
    executions = artifact.provenance.node_runs.get(node)
    if not executions:
        raise BaselineError(
            f"{artifact.path}: node_usage names {node!r}, which provenance"
            " never recorded; the cost cannot be attributed"
        )
    last = executions[-1]
    return last.served_model, last.requested_model


def _calls(artifact: EvalArtifact) -> list[tuple[str, str, TokenUsage]]:
    """The sweep's billable calls as ``(served, requested, usage)`` triples."""
    calls = []
    for node, usage in sorted(usage_of(artifact).items()):
        served, requested = _node_model(artifact, node)
        calls.append((served, requested, usage))
    return calls


def price_sweep(artifact: EvalArtifact) -> SweepCost:
    """One sweep's cost, through :func:`evals.harness.prices.price_calls`.

    Never a silent zero: an unpriced model is named, and the total covers
    only what is priced.
    """
    priced = price_calls(_calls(artifact))
    reported = charges_of(artifact)
    return SweepCost(
        unit_prices=priced.unit_prices,
        unpriced=priced.unpriced,
        fallbacks=priced.fallbacks,
        actual_usd=priced.total_usd,
        # ``None`` and not 0.0 where nothing reported: a sweep on a vendor that
        # states no charge has no such figure, and a zero there would read as a
        # sweep that was charged nothing.
        reported_usd=sum(reported.values()) if reported else None,
    )


def _reported_problems(
    filename: str, artifact: EvalArtifact, recorded: Mapping[str, Any]
) -> list[str]:
    """The manifest's reported charge against the artifact's own node charges.

    **A different kind of check from the one above, and the difference is the
    point.** The recorded actual is arithmetic, so CI re-multiplies recorded
    units by recorded rates and compares. A reported charge cannot be
    recomputed from anything — it is a provider's statement — so the only
    honest re-check is that the manifest repeats what the artifact holds, and
    that the artifact's own block adds up to it.

    Both directions are checked, because each catches what the other cannot: a
    manifest naming a figure the artifact never recorded, and an artifact whose
    charges nothing in the manifest reports.
    """
    summed = charges_of(artifact)
    expected = sum(summed.values()) if summed else None
    stated = recorded_usd(recorded, "reported_usd")
    if expected is None and stated is None:
        return []
    if expected is None:
        return [
            (
                f"{filename}: the manifest reports a charge of {stated!r}, and"
                " the artifact records none; a figure no sweep produced is not"
                " a cost"
            )
        ]
    if stated is None or not math.isclose(expected, stated, rel_tol=1e-9):
        return [
            (
                f"{filename}: the artifact's node charges sum to {expected},"
                f" not the manifest's reported {recorded.get('reported_usd')!r}"
            )
        ]
    return []


def _recomputed_cost(artifact: EvalArtifact, recorded: Mapping[str, Any]) -> float:
    """#323 check 5: recorded units × recorded unit prices, and nothing live."""
    rates = {
        entry["model"]: UnitPrices.from_json(entry)
        for entry in recorded.get("unit_prices", ())
    }
    for served, requested in dict(recorded.get("fallbacks", {})).items():
        if requested in rates:
            rates.setdefault(served, rates[requested])
    unpriced = set(recorded.get("unpriced", ()))
    calls = [call for call in _calls(artifact) if call[0] not in unpriced]
    stranded = sorted({served for served, requested, _ in calls if served not in rates})
    if stranded:
        raise BaselineError(
            f"{artifact.path}: {stranded} is neither priced nor listed"
            " unpriced; a silent zero is never admissible"
        )
    return price_calls(calls, rates=rates).total_usd


#: A registered package name, which is the only thing this field ever holds.
#: The artifact declares ``frameworks`` as a plain list and validates no element,
#: so before this the value reaching the published table was whatever a
#: contributor wrote -- unbounded in length, unlike every model name beside it.
_FRAMEWORK_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: How long one may be. A slug shape alone is not a bound: 300 lowercase
#: letters are a slug, and the whole point here is that this field had no
#: length while every model name beside it had one.
#:
#: Public, because ``submit`` reads the same rule before a declared name
#: reaches a path and had no bound at all. One number, two readers that call
#: it, rather than two numbers that drift.
FRAMEWORK_NAME_MAX = 40


def _framework_name(value: object) -> str:
    """One framework name out of an artifact, or a refusal.

    Shaped rather than merely stringified. It is carried into a Baseline's
    identity, which is recomputed and compared on every verification and
    rendered into a committed, published table; a name is a slug, and anything
    that is not one is not a name.
    """
    name = str(value)
    if len(name) > FRAMEWORK_NAME_MAX or not _FRAMEWORK_NAME.fullmatch(name):
        raise BaselineError(
            f"{name!r} is not a framework name; a Baseline's identity carries"
            " the packages the sweep ran, and a package name is a slug"
        )
    return name


def artifact_filename(value: object) -> str:
    """One sweep's artifact name, refused unless it is a plain file name.

    A Baseline manifest is a contributor's file, and both readers of it join the
    name onto a directory. Nothing stops ``../`` there, so a manifest could name
    a JSON outside the Baseline it belongs to and have its numbers read as that
    Baseline's -- and ``comparison`` folds them into a published README.

    Neither reader wants a path: a Baseline's artifacts sit beside its manifest
    by construction. So the field says so, rather than each reader guarding it
    and one of them forgetting.
    """
    name = str(value)
    if not name or name != Path(name).name or name.startswith("."):
        raise BaselineError(
            f"{name!r} is not an artifact file name; a Baseline's artifacts sit"
            " beside its manifest, so the name carries no directory"
        )
    return name


def _file_digests(directory: Path, sweep_stem: str) -> dict[str, str]:
    """Every file one sweep owns, digested, keyed by path inside the Baseline."""
    digests: dict[str, str] = {}
    for path in sorted(directory.glob(f"{sweep_stem}*")):
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in files:
            if file.is_file():
                rel = file.relative_to(directory).as_posix()
                digests[rel] = hashlib.sha256(file.read_bytes()).hexdigest()
    return digests


def refresh_manifests(root: Path, write: bool) -> int:
    """Recompute the file digests every Baseline manifest under ``root`` records.

    What a migration calls after it rewrites an archived file, so the manifest
    seals the bytes on disk. Prints each manifest it moves; ``write`` false is
    a dry run.
    """
    changed = 0
    for manifest_path in sorted(root.rglob("baseline.json")):
        directory = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        moved = False
        for entry in manifest.get("sweeps", []):
            stem = str(entry.get("artifact", "")).removesuffix(".json")
            recomputed = _file_digests(directory, stem)
            if entry.get("files") != recomputed:
                entry["files"] = recomputed
                moved = True
        if not moved:
            continue
        print(f"{manifest_path}: file digests recomputed")
        if write:
            # The spelling ``assemble`` writes, so a refreshed manifest and a
            # freshly assembled one are the same bytes.
            manifest_path.write_text(
                archive_bytes("manifest", manifest), encoding="utf-8"
            )
        changed += 1
    return changed


def assemble(root: Path, author: str, artifact_paths: list[Path]) -> Path:
    """Lay one or more sweeps into their Baseline directory, and write the manifest.

    The convenience half of #321: CI recomputes everything this writes, so
    nothing here is a trust point. Adding sweeps to an existing Baseline is
    the same call — the identity decides the directory, and the cap is
    checked where the directory is verified.

    **A sweep is keyed by its own bytes, so re-assembling one replaces it.**
    The stem is a digest of the artifact, which makes re-running ``submit
    baseline`` over the same file the same sweep rather than a second one.
    Appending it twice was invisible to :func:`verify` — the file digests and
    the cost arithmetic both recompute per entry and a duplicate agrees with
    itself — and it doubled the directory's recorded cost, spent one of the
    ten cap slots, and printed a range across two identical values, which is
    the fake spread ``comparison.py`` exists to prevent.
    """
    artifacts = [load_artifact(path) for path in artifact_paths]
    identities = {BaselineIdentity.from_artifact(artifact) for artifact in artifacts}
    if len(identities) != 1:
        raise BaselineError(
            "the artifacts disagree on the identity, so they are sweeps of"
            " different Baselines; submit them separately"
        )
    identity = identities.pop()
    directory = root / "evals" / "baselines" / identity.name
    directory.mkdir(parents=True, exist_ok=True)

    manifest_path = directory / "baseline.json"
    manifest: dict[str, Any] = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"name": identity.name, "identity": identity.to_json(), "sweeps": []}
    )
    # Keyed by artifact filename rather than a list, so a sweep already in the
    # manifest keeps its place and is rewritten instead of appended again.
    entries: dict[str, dict[str, Any]] = {
        str(entry.get("artifact", "")): entry for entry in manifest.get("sweeps", [])
    }

    for source, artifact in zip(artifact_paths, artifacts, strict=True):
        payload = source.read_bytes()
        stem = f"{author}-{hashlib.sha256(payload).hexdigest()[:8]}"
        reports = source.with_suffix(".reports")
        if not reports.is_dir() or not any(reports.iterdir()):
            raise BaselineError(
                f"{source}: has no reports directory beside it; the reports"
                " are what the free sitting path reads, so a Baseline stores"
                " sweeps with their reports or not at all"
            )
        (directory / f"{stem}.json").write_bytes(payload)
        target_reports = directory / f"{stem}.reports"
        target_reports.mkdir(exist_ok=True)
        for file in sorted(reports.rglob("*")):
            if file.is_file():
                target = target_reports / file.relative_to(reports)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(file.read_bytes())
        entries[f"{stem}.json"] = {
            "artifact": f"{stem}.json",
            "submitted_by": author,
            "certification": artifact.block("certification"),
            "files": _file_digests(directory, stem),
            "cost": price_sweep(artifact).to_json(),
        }

    manifest["sweeps"] = list(entries.values())
    manifest_path.write_text(archive_bytes("manifest", manifest), encoding="utf-8")
    return directory


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def corpus_digest_at(commit: str, root: Path = REPO_ROOT) -> str | None:
    """The corpus digest recomputed from a commit's tree, not the checkout's.

    Mirrors :func:`evals.harness.artifact.corpus_digest` byte for byte —
    path-keyed, sorted, ungraded files skipped — over ``git`` blobs. None
    when the commit is not in this clone's history.
    """
    listing = _git(root, "ls-tree", "-r", "-z", commit, "--", "evals/corpus")
    if listing is None:
        return None
    entries = []
    for row in listing.split("\0"):
        if not row:
            continue
        meta, path = row.split("\t", 1)
        blob = meta.split()[2]
        entries.append((path, blob))
    digest = hashlib.sha256()
    for path, blob in sorted(entries):
        name = path.rsplit("/", 1)[-1]
        if _ungraded(name):
            continue
        payload = subprocess.run(
            ["git", "cat-file", "blob", blob],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        relative = path.removeprefix("evals/corpus/")
        digest.update(relative.encode("utf-8"))
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def default_base_ref(root: Path) -> str:
    """The name the ancestor check measures against in this clone.

    ``origin/main`` where the clone has one, which is every clone with a
    remote and every pull-request checkout in CI, where no local ``main``
    exists at all. A clone with no remote falls back to its own ``main``.
    The contribution workflow failed on every pull request since the first
    Baseline landed because the check asked for ``main`` in a checkout that
    carried only ``origin/main``, and read a merged Baseline as a fork-only
    commit.
    """
    if _git(root, "rev-parse", "--verify", "--quiet", "origin/main") is not None:
        return "origin/main"
    return "main"


def verify(
    directory: Path, root: Path = REPO_ROOT, base_ref: str | None = None
) -> list[str]:
    """Every #323 check that needs no PR context, as a list of problems.

    The submit preflight and the repo-wide test both run this, so a
    contributed Baseline fails at the contributor's machine before it fails
    in CI. The git-borne checks (ancestor, corpus digest at the commit) run
    only when the commit is in this clone's history; a shallow checkout
    skips them rather than failing honest work it cannot see.
    """
    problems: list[str] = []
    base_ref = base_ref or default_base_ref(root)
    manifest_path = directory / "baseline.json"
    if not manifest_path.is_file():
        return [f"{directory.name}: carries no baseline.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{manifest_path}: cannot be read: {exc}"]

    sweeps = manifest.get("sweeps", [])
    if not sweeps:
        problems.append(f"{directory.name}: names no sweeps")
    if len(sweeps) > SWEEP_CAP:
        problems.append(
            f"{directory.name}: holds {len(sweeps)} sweeps; the cap is"
            f" {SWEEP_CAP} (#321)"
        )
    # One entry per sweep. A repeated filename is one sweep counted twice, and
    # every other check here passes it: the digests and the cost recompute per
    # entry, so a duplicate agrees with itself while doubling the directory's
    # cost and the run count the published spread rests on.
    filenames = [str(entry.get("artifact", "")) for entry in sweeps]
    repeated = sorted({name for name in filenames if filenames.count(name) > 1})
    if repeated:
        problems.append(
            f"{directory.name}: {repeated} named by more than one sweep entry;"
            " a sweep is keyed by its own bytes, so the same artifact is one"
            " sweep and not two"
        )

    identities = set()
    artifacts: dict[str, EvalArtifact] = {}
    for entry in sweeps:
        filename = artifact_filename(entry.get("artifact", ""))
        path = directory / filename
        try:
            artifact = load_artifact(path)
            identities.add(BaselineIdentity.from_artifact(artifact))
            artifacts[filename] = artifact
        except (ProvenanceError, BaselineError) as exc:
            problems.append(str(exc))
    if problems:
        return problems

    identity = identities.pop() if len(identities) == 1 else None
    if identity is None:
        problems.append(
            f"{directory.name}: the sweeps disagree on the identity, so this"
            " directory holds more than one Baseline"
        )
        return problems

    if directory.name != identity.name:
        problems.append(
            f"{directory.name}: the identity recomputes to {identity.name};"
            " the directory name is derived, never typed"
        )
    if manifest.get("name") != identity.name:
        problems.append(
            f"baseline.json names {manifest.get('name')!r}, not the identity's"
        )
    if manifest.get("identity") != identity.to_json():
        problems.append(
            "baseline.json's identity does not recompute from the artifacts"
        )

    listed: set[str] = set()
    for entry in sweeps:
        filename = str(entry.get("artifact", ""))
        artifact = artifacts[filename]
        stem = filename.removesuffix(".json")
        recomputed = _file_digests(directory, stem)
        listed.update(recomputed)
        if entry.get("files") != recomputed:
            problems.append(f"{filename}: the recorded digests do not recompute")
        if entry.get("certification") != artifact.block("certification"):
            problems.append(
                f"{filename}: the manifest's certification is not the artifact's"
            )
        if not str(entry.get("submitted_by", "")).strip():
            problems.append(f"{filename}: carries no submitted_by label (#323)")
        missing_usage = sorted(
            set(artifact.provenance.node_runs) - set(artifact.block("node_usage"))
        )
        if missing_usage:
            problems.append(
                f"{filename}: no node_usage for {missing_usage}; the actual"
                " cost is not computable"
            )
        recorded = entry.get("cost")
        if not isinstance(recorded, Mapping):
            problems.append(f"{filename}: cost is {recorded!r}, not a table")
            continue
        try:
            recomputed_cost = _recomputed_cost(artifact, recorded)
            actual = recorded_usd(recorded)
            if actual is None or not math.isclose(
                recomputed_cost, actual, rel_tol=1e-9
            ):
                problems.append(
                    f"{filename}: recorded units x recorded unit prices is"
                    f" {recomputed_cost}, not the recorded"
                    f" {recorded.get('actual_usd')!r}"
                )
        except BaselineError as exc:
            problems.append(str(exc))
        problems += _reported_problems(filename, artifact, recorded)

    strays = sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
        and path != manifest_path
        and path.relative_to(directory).as_posix() not in listed
    )
    if strays:
        problems.append(f"{directory.name}: files no sweep owns: {strays}")

    if _git(root, "cat-file", "-e", f"{identity.repo_commit}^{{commit}}") is not None:
        if (
            _git(root, "merge-base", "--is-ancestor", identity.repo_commit, base_ref)
            is None
        ):
            problems.append(
                f"{identity.repo_commit[:12]}: not an ancestor of {base_ref};"
                " a fork-only commit is refused because the identity names a"
                " commit a reader can open (#323)"
            )
        recomputed_digest = corpus_digest_at(identity.repo_commit, root)
        if (
            recomputed_digest is not None
            and recomputed_digest != identity.corpus_digest
        ):
            problems.append(
                f"{directory.name}: the corpus digest at"
                f" {identity.repo_commit[:12]} recomputes to"
                f" {recomputed_digest[:12]}…, not the artifacts'"
            )
        expected_cases = _case_ids_at(identity.repo_commit, root)
        for filename, artifact in sorted(artifacts.items()):
            if expected_cases is not None and set(artifact.cases) != expected_cases:
                problems.append(
                    f"{filename}: ran {len(artifact.cases)} case(s) where the"
                    f" commit's corpus holds {len(expected_cases)}; the case"
                    " set must be the whole corpus, because a picked subset"
                    " selects the number the corpus exists to prevent (#321)"
                )
    return problems


def _case_ids_at(commit: str, root: Path) -> set[str] | None:
    """The corpus's case directories at a commit, for the full-corpus rule."""
    listing = _git(root, "ls-tree", "--name-only", commit, "evals/corpus/")
    if listing is None:
        return None
    return {
        row.removeprefix("evals/corpus/").strip("/")
        for row in listing.splitlines()
        if row.strip()
    }
