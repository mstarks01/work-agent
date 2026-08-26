"""A **Baseline**: one directory, one configuration, up to ten sweeps.

Merged baselines live at ``evals/baselines/<derived-name>/`` — the public
record, beside ``evals/runs/`` the private scratch area (#321). A Baseline's
**identity is computed from its artifacts and nobody types it**: the clean
repo commit, the corpus digest, the requested model per tier, the resolved
sampling per tier, and the framework selection. Two sweeps that agree on all
five parts belong to one Baseline; the served build is an observation about a
sweep, never part of the identity, so drifting fingerprints inside one
Baseline are a finding about the provider rather than a second Baseline.

The directory name is derived — the short commit, the strong-tier model slug,
and an 8-hex prefix of the identity hash — so two contributors who sweep one
configuration collide at one directory, and "same baseline" is enforced by
the filesystem rather than by review attention. ``baseline.json`` is the
manifest: the identity, and one entry per sweep. CI re-derives all of it and
fails a mismatch, on the provenance-summary pattern — a stored view that
disagrees with the record is corruption, not a second opinion.

What verification can and cannot prove is #323's decision: every check here
shows the artifact **agrees with itself and with the repository**, never that
a model ran. A fabricator can compute correct hashes for a fabrication; the
``submitted_by`` label on each sweep is the disclosure, and the standing
behind it derives from the one roster at read time. The cost check is pure
arithmetic over the manifest's own recorded unit prices, so an honest
artifact never starts failing because the live price map moved.

Security: the digests make silent edits loud (A08), the loads fail closed
(A10), and nothing here trusts a label it can recompute (A08 again — the
name, the manifest and every file hash are evidence, not assertions).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness.artifact import (
    REPO_ROOT,
    EvalArtifact,
    ProvenanceError,
    _ungraded,
    load_artifact,
)
from evals.harness.prices import UnitPrices, unit_prices
from stride_service.model_tiers import TIER_NAMES
from stride_service.report import TokenUsage

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
    def from_artifact(cls, artifact: EvalArtifact) -> BaselineIdentity:
        if artifact.commit.clean is not True:
            raise BaselineError(
                f"{artifact.path}: ran on a tree that did not match its commit;"
                " a Baseline's identity names a commit so a reader can open the"
                " prompts behind the numbers, and a dirty sweep cannot"
            )
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
                sorted(str(name) for name in artifact.block("frameworks"))
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
        strong = dict(self.models).get(TIER_NAMES[-1], "unknown")
        return f"{self.repo_commit[:7]}-{_slug(strong)}-{self.hash[:8]}"


@dataclass(frozen=True)
class SweepCost:
    """What one sweep spent, priced with stated rates and stated holes."""

    unit_prices: tuple[UnitPrices, ...]
    unpriced: tuple[str, ...]
    fallbacks: tuple[tuple[str, str], ...]
    actual_usd: float

    def to_json(self) -> dict[str, Any]:
        return {
            "unit_prices": [prices.to_json() for prices in self.unit_prices],
            "unpriced": list(self.unpriced),
            "fallbacks": dict(self.fallbacks),
            "actual_usd": self.actual_usd,
        }


def _usage_of(artifact: EvalArtifact) -> dict[str, TokenUsage]:
    return {
        node: TokenUsage(**fields)
        for node, fields in artifact.block("node_usage").items()
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


def price_sweep(artifact: EvalArtifact) -> SweepCost:
    """One sweep's cost under the one rule in :mod:`evals.harness.prices`.

    Priced by the served build; where the map misses it — the suffixed build
    is the expected miss (#324) — the requested route is the stated fallback,
    and a model neither answers for lands in ``unpriced``. Never a silent
    zero: an unpriced model is named, and the total covers only what is
    priced.
    """
    priced: dict[str, UnitPrices] = {}
    unpriced: set[str] = set()
    fallbacks: dict[str, str] = {}
    total = 0.0
    for node, usage in sorted(_usage_of(artifact).items()):
        served, requested = _node_model(artifact, node)
        prices = unit_prices(served)
        if prices is None:
            prices = unit_prices(requested)
            if prices is not None:
                fallbacks[served] = requested
        if prices is None:
            unpriced.add(served)
            continue
        priced[prices.model] = prices
        total += prices.cost(usage)
    return SweepCost(
        unit_prices=tuple(priced[model] for model in sorted(priced)),
        unpriced=tuple(sorted(unpriced)),
        fallbacks=tuple(sorted(fallbacks.items())),
        actual_usd=total,
    )


def _recomputed_cost(artifact: EvalArtifact, recorded: dict[str, Any]) -> float:
    """#323 check 5: recorded units × recorded unit prices, and nothing live."""
    rates = {
        entry["model"]: UnitPrices.from_json(entry)
        for entry in recorded.get("unit_prices", ())
    }
    fallbacks = dict(recorded.get("fallbacks", {}))
    unpriced = set(recorded.get("unpriced", ()))
    total = 0.0
    for node, usage in sorted(_usage_of(artifact).items()):
        served, requested = _node_model(artifact, node)
        if served in unpriced:
            continue
        model = served if served in rates else fallbacks.get(served, requested)
        prices = rates.get(model) or rates.get(served)
        if prices is None:
            raise BaselineError(
                f"{artifact.path}: {served!r} is neither priced nor listed"
                " unpriced; a silent zero is never admissible"
            )
        total += prices.cost(usage)
    return total


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


def assemble(root: Path, author: str, artifact_paths: list[Path]) -> Path:
    """Lay one or more sweeps into their Baseline directory, and write the manifest.

    The convenience half of #321: CI recomputes everything this writes, so
    nothing here is a trust point. Adding sweeps to an existing Baseline is
    the same call — the identity decides the directory, and the cap is
    checked where the directory is verified.
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
    entries: list[dict[str, Any]] = list(manifest.get("sweeps", []))

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
        entries.append(
            {
                "artifact": f"{stem}.json",
                "submitted_by": author,
                "certification": artifact.block("certification"),
                "files": _file_digests(directory, stem),
                "cost": price_sweep(artifact).to_json(),
            }
        )

    manifest["sweeps"] = entries
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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


def verify(
    directory: Path, root: Path = REPO_ROOT, base_ref: str = "main"
) -> list[str]:
    """Every #323 check that needs no PR context, as a list of problems.

    The submit preflight and the repo-wide test both run this, so a
    contributed Baseline fails at the contributor's machine before it fails
    in CI. The git-borne checks (ancestor, corpus digest at the commit) run
    only when the commit is in this clone's history; a shallow checkout
    skips them rather than failing honest work it cannot see.
    """
    problems: list[str] = []
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

    identities = set()
    artifacts: dict[str, EvalArtifact] = {}
    for entry in sweeps:
        filename = str(entry.get("artifact", ""))
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
        try:
            recorded = entry.get("cost", {})
            recomputed_cost = _recomputed_cost(artifact, recorded)
            if not math.isclose(
                recomputed_cost, float(recorded.get("actual_usd", -1.0)), rel_tol=1e-9
            ):
                problems.append(
                    f"{filename}: recorded units x recorded unit prices is"
                    f" {recomputed_cost}, not the recorded"
                    f" {recorded.get('actual_usd')!r}"
                )
        except BaselineError as exc:
            problems.append(str(exc))

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
