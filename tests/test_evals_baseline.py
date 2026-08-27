"""The Baseline: computed identity, derived name, and checks that recompute.

Everything verification can prove is that the artifact agrees with itself and
with the repository (#323), so the tests here are about agreement: two sweeps
of one configuration land in one directory, a renamed directory is refused, a
silent edit moves a digest, and the cost check is arithmetic over recorded
prices rather than a read of the live map. The artifacts are synthetic but
honest — every fingerprint recomputes — because the loader refuses anything
less.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness import baseline, prices
from evals.harness.artifact import ARTIFACT_VERSION, load_artifact
from evals.harness.baseline import (
    BaselineError,
    BaselineIdentity,
    assemble,
    price_sweep,
    verify,
)
from evals.harness.prices import UnitPrices
from evals.harness.provenance import RunProvenance
from stride_service.sampling import TierSampling, sampling_fingerprint

COMMIT = "c" * 40
CORPUS = "d" * 64


def sampling(temperature: float = 0.2) -> dict[str, TierSampling]:
    return {
        "base": TierSampling(temperature=temperature, seed=7),
        "strong": TierSampling(temperature=temperature, seed=7),
    }


def payload(
    *,
    clean: bool = True,
    temperature: float = 0.2,
    strong_model: str = "openai/gpt-5.6",
    served_strong: str = "gpt-5.6-luna",
    frameworks: tuple[str, ...] = ("stride",),
    usage_nodes: tuple[str, ...] = ("extract", "critic"),
    seed: int = 1,
) -> dict:
    """One admissible artifact document; ``seed`` varies the bytes only."""
    tiers = sampling(temperature)
    runs = {
        "extract": ("base", "openai/gpt-base", "gpt-base-001"),
        "critic": ("strong", strong_model, served_strong),
    }
    node_runs = {
        node: [
            {
                "node": node,
                "tier": tier,
                "requested_model": requested,
                "served_model": served,
                "generation_fingerprint": sampling_fingerprint(served, tiers[tier]),
            }
        ]
        for node, (tier, requested, served) in runs.items()
    }
    provenance = RunProvenance.model_validate(
        {
            "sampling_config_version": 1,
            "tiers_config_version": 1,
            "sampling": tiers,
            "node_runs": node_runs,
        }
    )
    usage = {
        node: {
            "prompt_tokens": 1000,
            "cached_prompt_tokens": 200,
            "completion_tokens": 300,
        }
        for node in usage_nodes
    }
    return {
        "artifact_version": ARTIFACT_VERSION,
        "mode": "end-to-end",
        "cases": ["01-a-case"],
        "trusted": False,
        "structural_failures": [],
        "repo_commit": {"commit": COMMIT, "clean": clean},
        "corpus_digest": CORPUS,
        "frameworks": list(frameworks),
        "certification": {"verdict": "uncertified", "seed": seed},
        "node_usage": usage,
        "provenance": provenance.to_json(),
    }


def write_sweep(directory: Path, document: dict, stem: str = "art") -> Path:
    path = directory / f"{stem}.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    reports = directory / f"{stem}.reports"
    reports.mkdir()
    (reports / "01-a-case.report.json").write_text(
        json.dumps({"claims": [], "seed": document["certification"]["seed"]}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def priced(monkeypatch):
    """A price map the tests own: the suffixed strong build is the miss."""

    def fake(model: str) -> UnitPrices | None:
        rates = {
            "gpt-base-001": UnitPrices("gpt-base-001", 1e-6, 4e-6, 1e-7),
            "openai/gpt-5.6": UnitPrices("openai/gpt-5.6", 2e-6, 8e-6, 2e-7),
        }
        return rates.get(model)

    monkeypatch.setattr(prices, "unit_prices", fake)
    return fake


class TestTheIdentity:
    def test_two_sweeps_of_one_configuration_share_a_name(self, tmp_path):
        first = write_sweep(tmp_path, payload(seed=1), "one")
        second = write_sweep(
            tmp_path, payload(seed=2, served_strong="gpt-5.6-nova"), "two"
        )
        one = BaselineIdentity.from_artifact(load_artifact(first))
        two = BaselineIdentity.from_artifact(load_artifact(second))
        assert one == two, "the served build is an observation, never identity"
        assert one.name == two.name

    def test_the_name_is_derived_and_readable(self, tmp_path):
        path = write_sweep(tmp_path, payload())
        identity = BaselineIdentity.from_artifact(load_artifact(path))
        assert identity.name.startswith(f"{COMMIT[:7]}-gpt-5.6-")
        assert len(identity.name.rsplit("-", 1)[-1]) == 8

    def test_a_sampling_change_is_another_baseline(self, tmp_path):
        one = write_sweep(tmp_path, payload(), "one")
        two = write_sweep(tmp_path, payload(temperature=0.7), "two")
        assert (
            BaselineIdentity.from_artifact(load_artifact(one)).name
            != BaselineIdentity.from_artifact(load_artifact(two)).name
        )

    def test_a_dirty_sweep_has_no_identity(self, tmp_path):
        path = write_sweep(tmp_path, payload(clean=False))
        with pytest.raises(BaselineError, match="did not match its commit"):
            BaselineIdentity.from_artifact(load_artifact(path))


class TestPricing:
    def test_the_suffixed_build_falls_back_to_its_route_and_says_so(
        self, tmp_path, priced
    ):
        cost = price_sweep(load_artifact(write_sweep(tmp_path, payload())))
        assert dict(cost.fallbacks) == {"gpt-5.6-luna": "openai/gpt-5.6"}
        assert cost.actual_usd > 0
        assert not cost.unpriced

    def test_a_model_nobody_prices_is_named_never_zeroed(self, tmp_path, priced):
        document = payload(strong_model="mystery/model", served_strong="mystery-001")
        cost = price_sweep(load_artifact(write_sweep(tmp_path, document)))
        assert cost.unpriced == ("mystery-001",)


class TestAssembleAndVerify:
    def test_a_clean_baseline_assembles_and_verifies(self, tmp_path, priced):
        source = write_sweep(tmp_path, payload())
        directory = assemble(tmp_path, "ada", [source])
        assert directory.parent == tmp_path / "evals" / "baselines"
        manifest = json.loads((directory / "baseline.json").read_text("utf-8"))
        assert manifest["name"] == directory.name
        assert manifest["sweeps"][0]["submitted_by"] == "ada"
        assert verify(directory, root=tmp_path) == []

    def test_two_contributors_collide_at_one_directory(self, tmp_path, priced):
        first = assemble(
            tmp_path, "ada", [write_sweep(tmp_path, payload(seed=1), "one")]
        )
        second = assemble(
            tmp_path, "sam", [write_sweep(tmp_path, payload(seed=2), "two")]
        )
        assert first == second
        manifest = json.loads((first / "baseline.json").read_text("utf-8"))
        assert [entry["submitted_by"] for entry in manifest["sweeps"]] == ["ada", "sam"]
        assert verify(first, root=tmp_path) == []

    def test_a_renamed_directory_is_refused(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, payload())])
        renamed = directory.with_name("7c3a007-hand-typed-00000000")
        directory.rename(renamed)
        problems = verify(renamed, root=tmp_path)
        assert any("derived, never typed" in problem for problem in problems)

    def test_an_edited_report_moves_a_digest(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, payload())])
        report = next(directory.glob("*.reports/*.json"))
        report.write_text('{"claims": ["invented"]}', encoding="utf-8")
        problems = verify(directory, root=tmp_path)
        assert any("digests do not recompute" in problem for problem in problems)

    def test_a_tampered_cost_fails_the_arithmetic(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, payload())])
        manifest_path = directory / "baseline.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["sweeps"][0]["cost"]["actual_usd"] = 0.0
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), "utf-8"
        )
        problems = verify(directory, root=tmp_path)
        assert any("recorded unit prices" in problem for problem in problems)

    def test_a_sweep_over_the_cap_is_refused(self, tmp_path, priced, monkeypatch):
        monkeypatch.setattr(baseline, "SWEEP_CAP", 1)
        sweeps = [
            write_sweep(tmp_path, payload(seed=seed), f"s{seed}") for seed in (1, 2)
        ]
        directory = assemble(tmp_path, "ada", sweeps)
        problems = verify(directory, root=tmp_path)
        assert any("the cap is 1" in problem for problem in problems)

    def test_missing_usage_is_an_incomputable_cost(self, tmp_path, priced):
        document = payload(usage_nodes=("extract",))
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, document)])
        problems = verify(directory, root=tmp_path)
        assert any("no node_usage" in problem for problem in problems)

    def test_disagreeing_artifacts_do_not_assemble(self, tmp_path, priced):
        one = write_sweep(tmp_path, payload(), "one")
        two = write_sweep(tmp_path, payload(temperature=0.9), "two")
        with pytest.raises(BaselineError, match="different Baselines"):
            assemble(tmp_path, "ada", [one, two])

    def test_a_sweep_without_reports_is_refused(self, tmp_path, priced):
        source = tmp_path / "bare.json"
        source.write_text(json.dumps(payload(), indent=2), encoding="utf-8")
        with pytest.raises(BaselineError, match="no reports directory"):
            assemble(tmp_path, "ada", [source])

    def test_a_stray_file_is_named(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, payload())])
        (directory / "notes.txt").write_text("hand-written\n", encoding="utf-8")
        problems = verify(directory, root=tmp_path)
        assert any("files no sweep owns" in problem for problem in problems)


def test_the_repo_wide_walk_holds_every_merged_baseline():
    """The merged tree's Baselines all verify; empty today, armed forever."""
    directories = (
        sorted(path for path in baseline.BASELINES_DIR.iterdir() if path.is_dir())
        if baseline.BASELINES_DIR.is_dir()
        else []
    )
    failures = {
        directory.name: problems
        for directory in directories
        if (problems := verify(directory))
    }
    assert not failures, f"merged Baselines that no longer verify: {failures}"


def test_every_submitter_has_a_roster_line():
    """#323: standing derives from the one roster, submitters included."""
    from evals.harness.roster import DEFAULT_ROSTER_PATH
    from evals.harness.roster import load as load_roster

    if not baseline.BASELINES_DIR.is_dir():
        return
    roster = load_roster(DEFAULT_ROSTER_PATH)
    unrostered = sorted(
        {
            str(entry.get("submitted_by"))
            for directory in baseline.BASELINES_DIR.iterdir()
            if (directory / "baseline.json").is_file()
            for entry in json.loads(
                (directory / "baseline.json").read_text("utf-8")
            ).get("sweeps", [])
            if entry.get("submitted_by") not in roster
        }
    )
    assert not unrostered, f"submitters with no roster line: {unrostered}"


def test_the_corpus_digest_at_head_matches_the_live_computation():
    """The git-blob recomputation mirrors the working-tree one, byte for byte."""
    from evals.harness.artifact import REPO_ROOT, corpus_digest

    head = baseline._git(REPO_ROOT, "rev-parse", "HEAD")
    status = baseline._git(REPO_ROOT, "status", "--porcelain", "--", "evals/corpus")
    if head is None or (status or "").strip():
        pytest.skip("the corpus working tree does not match HEAD here")
    assert baseline.corpus_digest_at(head.strip(), REPO_ROOT) == corpus_digest()
