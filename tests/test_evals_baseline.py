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

from analysis_service.report import TokenUsage
from analysis_service.vendors import VENDORS, vendor_for
from evals.harness import baseline, prices
from evals.harness.artifact import load_artifact
from evals.harness.baseline import (
    BASELINE_RULES,
    DIRTY_MARKER,
    BaselineError,
    BaselineIdentity,
    artifact_filename,
    assemble,
    configuration_label,
    price_sweep,
    verify,
)
from evals.harness.prices import UnitPrices
from tests.eval_factories import SWEEP_COMMIT, sweep_document


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
        first = write_sweep(tmp_path, sweep_document(seed=1), "one")
        second = write_sweep(
            tmp_path, sweep_document(seed=2, served_strong="gpt-5.6-nova"), "two"
        )
        one = BaselineIdentity.from_artifact(load_artifact(first))
        two = BaselineIdentity.from_artifact(load_artifact(second))
        assert one == two, "the served build is an observation, never identity"
        assert one.name == two.name

    def test_the_name_is_derived_and_readable(self, tmp_path):
        path = write_sweep(tmp_path, sweep_document())
        identity = BaselineIdentity.from_artifact(load_artifact(path))
        assert identity.name.startswith(f"{SWEEP_COMMIT[:7]}-gpt-5.6-")
        assert len(identity.name.rsplit("-", 1)[-1]) == 8

    def test_a_sampling_change_is_another_baseline(self, tmp_path):
        one = write_sweep(tmp_path, sweep_document(), "one")
        two = write_sweep(tmp_path, sweep_document(temperature=0.7), "two")
        assert (
            BaselineIdentity.from_artifact(load_artifact(one)).name
            != BaselineIdentity.from_artifact(load_artifact(two)).name
        )

    def test_a_dirty_sweep_has_no_identity(self, tmp_path):
        path = write_sweep(tmp_path, sweep_document(clean=False))
        with pytest.raises(BaselineError, match="did not match its commit"):
            BaselineIdentity.from_artifact(load_artifact(path))


class TestARouteHasToSayWhichWeightsAnswered:
    """A Baseline's sweeps are comparable, or the Baseline says nothing.

    The second of the Baseline's own rules, beside the dirty-tree one. It
    reads ``Vendor.routes_to_one_provider`` rather than a vendor's name, so a
    row added tomorrow in front of more than one provider is refused by this
    without an edit here.
    """

    #: The registry's own answer, so this suite states the vendors rather than
    #: restating the field. A row that changes its answer moves these lists.
    AGGREGATED = sorted(
        name for name, vendor in VENDORS.items() if not vendor.routes_to_one_provider
    )

    def test_some_vendor_answers_each_way(self):
        """A rule no row triggers is a rule nothing below actually exercises."""
        assert self.AGGREGATED, "no vendor routes to more than one provider"
        assert set(self.AGGREGATED) != set(VENDORS)

    @pytest.mark.parametrize("name", AGGREGATED)
    def test_an_aggregated_route_cannot_name_a_baseline(self, name, tmp_path):
        route = f"{vendor_for(name).prefix}some-model"
        path = write_sweep(tmp_path, sweep_document(strong_model=route))
        with pytest.raises(BaselineError, match="more than one upstream"):
            BaselineIdentity.from_artifact(load_artifact(path))

    @pytest.mark.parametrize("name", AGGREGATED)
    def test_the_sweep_is_still_a_sweep_with_a_label(self, name, tmp_path):
        """Nothing refuses to *run* the vendor, and the label proves it.

        ``configuration_label`` reads the same five parts with the Baseline's
        rules off, so a run on an aggregator is named, comparable to itself,
        and simply never published as a Baseline.
        """
        route = f"{vendor_for(name).prefix}some-model"
        artifact = load_artifact(
            write_sweep(tmp_path, sweep_document(strong_model=route))
        )
        assert configuration_label(artifact)

    @pytest.mark.parametrize("name", AGGREGATED)
    def test_the_label_says_the_route_names_no_single_backend(self, name, tmp_path):
        """The marker, for the same reason the dirty one exists.

        The five parts cannot tell two upstream providers apart on one route,
        so a bare name would claim the models describe the weights. This rule
        shipped with its refusal and no marker, and every vote cast on an
        aggregator's sweep carried a label that read as reproducible.
        """
        route = f"{vendor_for(name).prefix}some-model"
        aggregated = load_artifact(
            write_sweep(tmp_path, sweep_document(strong_model=route), "agg")
        )

        assert configuration_label(aggregated).endswith("-multiprovider")

    @pytest.mark.parametrize("name", AGGREGATED)
    def test_assemble_refuses_it_too(self, name, tmp_path):
        """The rule has one reader, so both entry points get it."""
        route = f"{vendor_for(name).prefix}some-model"
        path = write_sweep(tmp_path, sweep_document(strong_model=route))
        with pytest.raises(BaselineError, match="more than one upstream"):
            assemble(tmp_path, "someone", [path])


class TestTheConfigurationLabel:
    """What a vote records about the sweep that produced the finding (#802)."""

    def test_a_clean_sweep_is_labelled_by_its_baseline_name(self, tmp_path):
        """One reader, so a vote and a Baseline cannot spell one sweep twice."""
        artifact = load_artifact(write_sweep(tmp_path, sweep_document()))
        assert (
            configuration_label(artifact)
            == BaselineIdentity.from_artifact(artifact).name
        )

    def test_two_configurations_take_two_labels(self, tmp_path):
        one = load_artifact(write_sweep(tmp_path, sweep_document(), "one"))
        two = load_artifact(
            write_sweep(tmp_path, sweep_document(temperature=0.7), "two")
        )
        assert configuration_label(one) != configuration_label(two)

    def test_a_dirty_sweep_is_labelled_and_says_so(self, tmp_path):
        """The Baseline refuses this sweep; the vote still records what ran.

        ``TUNING.md`` step 3 runs a sweep from an edited tree on purpose, and a
        reviewer's answer is worth keeping. Two dirty trees at one commit take
        one label, which is why the label says the commit does not describe the
        prompts rather than implying it does.
        """
        clean = load_artifact(write_sweep(tmp_path, sweep_document(), "clean"))
        dirty = load_artifact(
            write_sweep(tmp_path, sweep_document(clean=False), "dirty")
        )
        assert configuration_label(dirty) == configuration_label(clean) + DIRTY_MARKER

    def test_every_baseline_rule_carries_a_marker_of_its_own(self):
        """A refusal with no marker lets a label claim the rule held.

        The table is the one reader of "what does a Baseline require beyond the
        five parts", and both ``from_artifact`` and ``configuration_label``
        walk it. A rule added with an empty or a shared marker makes two
        different sweeps take one label, which is the failure the marker exists
        to remove.
        """
        markers = [rule.marker for rule in BASELINE_RULES]

        assert all(marker.startswith("-") for marker in markers)
        assert len(set(markers)) == len(markers)

    def test_a_sweep_failing_both_rules_carries_both_markers(self, tmp_path):
        """The markers compose, so one label never hides the other rule."""
        name = next(
            vendor for vendor, row in VENDORS.items() if not row.routes_to_one_provider
        )
        route = f"{vendor_for(name).prefix}some-model"
        one = load_artifact(
            write_sweep(tmp_path, sweep_document(strong_model=route), "one")
        )
        both = load_artifact(
            write_sweep(
                tmp_path, sweep_document(clean=False, strong_model=route), "both"
            )
        )

        # The same five parts either side, so the only difference is which
        # markers each label carries, in the order the table lists the rules.
        parts = BaselineIdentity.from_artifact(one, as_baseline=False).name

        assert configuration_label(one) == f"{parts}-multiprovider"
        assert configuration_label(both) == f"{parts}{DIRTY_MARKER}-multiprovider"


class TestPricing:
    def test_the_suffixed_build_falls_back_to_its_route_and_says_so(
        self, tmp_path, priced
    ):
        cost = price_sweep(load_artifact(write_sweep(tmp_path, sweep_document())))
        assert dict(cost.fallbacks) == {"gpt-5.6-luna": "openai/gpt-5.6"}
        assert cost.actual_usd > 0
        assert not cost.unpriced

    def test_a_model_nobody_prices_is_named_never_zeroed(self, tmp_path, priced):
        # A known vendor, so the fixture can compute a fingerprint; a model
        # identifier no price map carries, which is what the test is about.
        document = sweep_document(
            strong_model="vertex_ai/mystery-model", served_strong="mystery-001"
        )
        cost = price_sweep(load_artifact(write_sweep(tmp_path, document)))
        assert cost.unpriced == ("mystery-001",)


class TestAssembleAndVerify:
    def test_a_clean_baseline_assembles_and_verifies(self, tmp_path, priced):
        source = write_sweep(tmp_path, sweep_document())
        directory = assemble(tmp_path, "ada", [source])
        assert directory.parent == tmp_path / "evals" / "baselines"
        manifest = json.loads((directory / "baseline.json").read_text("utf-8"))
        assert manifest["name"] == directory.name
        assert manifest["sweeps"][0]["submitted_by"] == "ada"
        assert verify(directory, root=tmp_path) == []

    def test_two_contributors_collide_at_one_directory(self, tmp_path, priced):
        first = assemble(
            tmp_path, "ada", [write_sweep(tmp_path, sweep_document(seed=1), "one")]
        )
        second = assemble(
            tmp_path, "sam", [write_sweep(tmp_path, sweep_document(seed=2), "two")]
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
        source = write_sweep(tmp_path, sweep_document())
        assemble(tmp_path, "ada", [source])
        directory = assemble(tmp_path, "ada", [source])

        manifest = json.loads((directory / "baseline.json").read_text("utf-8"))
        assert len(manifest["sweeps"]) == 1
        assert verify(directory, root=tmp_path) == []

    def test_a_manifest_naming_one_sweep_twice_is_refused(self, tmp_path, priced):
        """The writer keys them; the verifier still refuses a hand-edited one."""
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, sweep_document())])
        path = directory / "baseline.json"
        manifest = json.loads(path.read_text("utf-8"))
        manifest["sweeps"] = manifest["sweeps"] * 2
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        problems = verify(directory, root=tmp_path)
        assert any("more than one sweep entry" in problem for problem in problems)

    def test_a_renamed_directory_is_refused(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, sweep_document())])
        renamed = directory.with_name("7c3a007-hand-typed-00000000")
        directory.rename(renamed)
        problems = verify(renamed, root=tmp_path)
        assert any("derived, never typed" in problem for problem in problems)

    def test_an_edited_report_moves_a_digest(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, sweep_document())])
        report = next(directory.glob("*.reports/*.json"))
        report.write_text('{"claims": ["invented"]}', encoding="utf-8")
        problems = verify(directory, root=tmp_path)
        assert any("digests do not recompute" in problem for problem in problems)

    def test_a_tampered_cost_fails_the_arithmetic(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, sweep_document())])
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
            write_sweep(tmp_path, sweep_document(seed=seed), f"s{seed}")
            for seed in (1, 2)
        ]
        directory = assemble(tmp_path, "ada", sweeps)
        problems = verify(directory, root=tmp_path)
        assert any("the cap is 1" in problem for problem in problems)

    def test_missing_usage_is_an_incomputable_cost(self, tmp_path, priced):
        document = sweep_document(usage_nodes=("extract",))
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, document)])
        problems = verify(directory, root=tmp_path)
        assert any("no node_usage" in problem for problem in problems)

    def test_disagreeing_artifacts_do_not_assemble(self, tmp_path, priced):
        one = write_sweep(tmp_path, sweep_document(), "one")
        two = write_sweep(tmp_path, sweep_document(temperature=0.9), "two")
        with pytest.raises(BaselineError, match="different Baselines"):
            assemble(tmp_path, "ada", [one, two])

    def test_a_sweep_without_reports_is_refused(self, tmp_path, priced):
        source = tmp_path / "bare.json"
        source.write_text(json.dumps(sweep_document(), indent=2), encoding="utf-8")
        with pytest.raises(BaselineError, match="no reports directory"):
            assemble(tmp_path, "ada", [source])

    def test_a_stray_file_is_named(self, tmp_path, priced):
        directory = assemble(tmp_path, "ada", [write_sweep(tmp_path, sweep_document())])
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


class TestAFrameworkNameIsASlug:
    """`frameworks` was declared as a plain list and validated element by
    element nowhere, so the value carried into a Baseline's identity -- and from
    there into the published table -- was whatever a contributor wrote, with no
    length bound at all while every model name beside it had one."""

    @pytest.mark.parametrize(
        "name",
        ["Stride", "a b", "a`b", "x" * 300, "", "a/b", "a.b", "-a", "a-"],
    )
    def test_a_name_that_is_not_a_slug_is_refused(self, name):
        with pytest.raises(BaselineError, match="not a framework name"):
            baseline._framework_name(name)

    @pytest.mark.parametrize("name", ["stride", "asvs", "some-new-package"])
    def test_a_registered_shape_passes(self, name):
        assert baseline._framework_name(name) == name


class TestOneReaderForRecordedDollars:
    """Four sites read `actual_usd` from a committed manifest; one checked it."""

    @pytest.mark.parametrize(
        "raw",
        [float("inf"), float("nan"), -1.0, "abc", "2.25", "1_000", True, None, 10**400],
    )
    def test_a_value_that_is_not_money_reads_as_absent(self, raw):
        """The writer emits a JSON number. Everything else is not money: a
        string `float()` would read, a boolean that is an `int`, and an integer
        too large for a float, which raises `OverflowError` rather than
        `ValueError`.
        """
        assert baseline.recorded_usd({"actual_usd": raw}) is None

    @pytest.mark.parametrize("cost", ["12.5", 12.5, None, [1]])
    def test_a_cost_that_is_not_a_table_reads_as_absent(self, cost):
        assert baseline.recorded_usd(cost) is None

    @pytest.mark.parametrize("raw", [0.0, 1.5, 3])
    def test_a_real_amount_reads_back(self, raw):
        assert baseline.recorded_usd({"actual_usd": raw}) == float(raw)

    def test_every_reader_answers_the_same_way(self):
        """Compared against each other, not each against its own expectation.

        `math.isfinite` guarded the consent path alone, so the published
        comparison table, the contribution summary and the baseline re-check all
        read the same field without it. A NaN poisons a mean and a total.
        """
        poisoned = {"actual_usd": float("nan")}

        assert baseline.recorded_usd(poisoned) is None
        assert (baseline.recorded_usd(poisoned) or 0.0) == 0.0


def test_the_ancestor_check_measures_against_the_remote_main_where_one_exists(tmp_path):
    """A pull-request checkout in CI carries origin/main and no local main, so
    a check that asked for main read every merged Baseline as a fork-only
    commit. The contribution workflow failed on every pull request since the
    first Baseline landed. A clone with no remote still measures against its
    own main."""
    import subprocess

    def git(cwd, *args):
        subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)

    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    local = tmp_path / "local"
    git(tmp_path, "init", "-b", "main", str(local))
    git(local, "config", "user.email", "t@example.test")
    git(local, "config", "user.name", "T")
    (local / "f").write_text("x", encoding="utf-8")
    git(local, "add", "f")
    git(local, "commit", "-m", "seed")
    assert baseline.default_base_ref(local) == "main"

    git(local, "remote", "add", "origin", str(origin))
    git(local, "push", "-u", "origin", "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(origin), str(clone))
    git(clone, "checkout", "--detach", "origin/main")
    git(clone, "branch", "-D", "main")
    assert baseline.default_base_ref(clone) == "origin/main"
