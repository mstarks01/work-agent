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

from analysis_service.identity import build_identity
from analysis_service.report import TokenUsage
from analysis_service.sampling import TierSampling
from evals.harness import baseline, prices
from evals.harness.artifact import ARTIFACT_VERSION, load_artifact
from evals.harness.baseline import (
    BaselineError,
    BaselineIdentity,
    artifact_filename,
    assemble,
    price_sweep,
    verify,
)
from evals.harness.prices import UnitPrices
from evals.harness.provenance import RunProvenance
from tests.factories import SAMPLE_INSTRUCTIONS, sample_fingerprint

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
                "instruction_sha256": SAMPLE_INSTRUCTIONS,
                "generation_fingerprint": sample_fingerprint(
                    served, tiers[tier], requested=requested
                ),
            }
        ]
        for node, (tier, requested, served) in runs.items()
    }
    provenance = RunProvenance.model_validate(
        {
            "build": dict(build_identity()),
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

    def test_re_assembling_one_sweep_replaces_it_rather_than_adding_it(
        self, tmp_path, priced
    ):
        """A sweep is keyed by its own bytes, so the same file is one sweep.

        Re-running ``submit baseline`` over an artifact already laid down
        rewrites its entry. An appended second entry would pass every check:
        the digests and the cost recompute per entry, so a duplicate agrees
        with itself.
        """
        source = write_sweep(tmp_path, payload())
        assemble(tmp_path, "ada", [source])
        directory = assemble(tmp_path, "ada", [source])

        manifest = json.loads((directory / "baseline.json").read_text("utf-8"))
        assert len(manifest["sweeps"]) == 1
        assert verify(directory, root=tmp_path) == []

    def test_a_manifest_naming_one_sweep_twice_is_refused(self, tmp_path, priced):
        """The writer keys them; the verifier still refuses a hand-edited one."""
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, payload())])
        path = directory / "baseline.json"
        manifest = json.loads(path.read_text("utf-8"))
        manifest["sweeps"] = manifest["sweeps"] * 2
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        problems = verify(directory, root=tmp_path)
        assert any("more than one sweep entry" in problem for problem in problems)

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


class TestAnUnknownCacheDiscount:
    """A missing cache-read rate is ``None``, and bills as no discount.

    ``prices.py`` says it never writes a silent zero, and most of litellm's
    map states no cache-read rate at all: 1820 of its 3212 entries price
    input and output and say nothing about cached reads. Zeroing that billed
    a 90%-cached call at a seventh of a plausible cost, which understates a
    number somebody consents to spend against.
    """

    HEAVILY_CACHED = TokenUsage(
        prompt_tokens=100_000, cached_prompt_tokens=90_000, completion_tokens=1_000
    )

    def test_a_stated_rate_is_used(self):
        stated = UnitPrices(
            "m", input_per_token=2e-6, output_per_token=8e-6, cache_read_per_token=2e-7
        )
        assert stated.cached_rate == 2e-7
        assert stated.cost(self.HEAVILY_CACHED) == pytest.approx(
            10_000 * 2e-6 + 90_000 * 2e-7 + 1_000 * 8e-6
        )

    def test_an_absent_rate_bills_the_full_input_price(self):
        unknown = UnitPrices(
            "m", input_per_token=2e-6, output_per_token=8e-6, cache_read_per_token=None
        )
        assert unknown.cached_rate == 2e-6
        assert unknown.cost(self.HEAVILY_CACHED) == pytest.approx(
            100_000 * 2e-6 + 1_000 * 8e-6
        )

    def test_the_absent_rate_never_reads_as_a_discount(self):
        """The whole point: unknown must never be cheaper than known-expensive."""
        unknown = UnitPrices("m", 2e-6, 8e-6, None)
        zeroed = UnitPrices("m", 2e-6, 8e-6, 0.0)
        assert unknown.cost(self.HEAVILY_CACHED) > zeroed.cost(self.HEAVILY_CACHED)

    def test_none_survives_the_json_round_trip(self):
        """A manifest records what was known, so the hole travels with it."""
        unknown = UnitPrices("m", 2e-6, 8e-6, None)
        assert UnitPrices.from_json(unknown.to_json()) == unknown


class TestAnArtifactNameCarriesNoDirectory:
    """A Baseline manifest is a contributor's file, and both readers of it join
    the name onto a directory.

    Nothing stopped ``../`` there, so a manifest could name a JSON outside the
    Baseline it belongs to and have its numbers read as that Baseline's --
    and ``comparison`` folds those numbers into a published README. Neither
    reader wants a path: a Baseline's artifacts sit beside its manifest.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "../other-baseline/sweep.json",
            "/etc/passwd",
            "sub/sweep.json",
            "..",
            "",
            ".hidden.json",
        ],
    )
    def test_a_name_that_is_not_a_plain_file_name_is_refused(self, name):
        with pytest.raises(BaselineError, match="carries no directory"):
            artifact_filename(name)

    def test_an_ordinary_artifact_name_passes(self):
        assert artifact_filename("sweep-1.json") == "sweep-1.json"
