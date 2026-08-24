"""Served identities in the artifact, and the promotion that reads them back.

Zero provider calls. The property under test is a round trip: a sweep observes
which build answered for each tier, writes it down, and a later ``promote``
blesses *that* — with no step where an operator retypes a model string from
memory, and no step where the requested route stands in for the served build
([#117](https://github.com/mstarks01/work-agent/issues/117)).

The fingerprint is the thing being protected. It is recomputed from the served
build and the tier's sampling every time it is used, so the hashes in an
artifact are evidence to be checked rather than values to be copied forward.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness.artifact import ARTIFACT_VERSION, load_artifact
from evals.harness.certify import plan_promotion
from evals.harness.provenance import (
    ProvenanceError,
    provenance_of,
)
from evals.harness.reference import load_case
from evals.harness.run import main
from stride_service.certification import load_manifest
from stride_service.deployment import (
    BLESSED_FINGERPRINTS_VAR,
    SAMPLING_VAR,
)
from stride_service.graph import tier_node_by_graph_node
from stride_service.report import NodeRun
from stride_service.sampling import load_sampling, sampling_fingerprint
from tests.factories import (
    DEFAULT_FRAMEWORKS,
    TEST_TIER_ENV,
    repo_tiers,
    served_build,
)
from tests.test_evals_run_grounds import CASE_DIR
from tests.test_evals_run_grounds import sweep as drive_sweep

TIER_NODE_BY_GRAPH_NODE = tier_node_by_graph_node(DEFAULT_FRAMEWORKS)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLING_PATH = REPO_ROOT / "config" / "sampling.toml"
TIERS_VERSION = repo_tiers().version

BASE_REQUESTED = "openai/gpt-4.1-mini"
BASE_SERVED = "openai/gpt-4.1-mini-2026-04-01"
STRONG_REQUESTED = "vertex_ai/gemini-2.5-pro"
STRONG_SERVED = "vertex_ai/gemini-2.5-pro-002"

_TIER_OF = repo_tiers().resolve_tier


def tier_of(graph_node: str) -> str:
    return _TIER_OF(TIER_NODE_BY_GRAPH_NODE[graph_node])


@pytest.fixture
def sampling():
    return load_sampling(SAMPLING_PATH)


def node_run(node: str, requested: str, served: str, sampling) -> NodeRun:
    """One node execution as the runner would have recorded it."""
    return NodeRun(
        node=node,
        model=served,
        requested_model=requested,
        sampling_fingerprint=sampling_fingerprint(
            served, sampling.for_tier(tier_of(node))
        ),
        duration_ms=1200,
    )


#: The strong-tier node this sweep drives. Per framework since schema 3.0, so
#: it is spelled once here rather than at each site that names a node.
CRITIC_NODE = "critic_stride"


def sweep(sampling, **served: str) -> list[NodeRun]:
    """A two-tier sweep's executions: ``extract`` on base, the critic on strong."""
    return [
        node_run("extract", BASE_REQUESTED, served.get("base", BASE_SERVED), sampling),
        node_run(
            CRITIC_NODE, STRONG_REQUESTED, served.get("strong", STRONG_SERVED), sampling
        ),
    ]


def provenance(sampling, executions=None):
    return provenance_of(
        executions if executions is not None else sweep(sampling),
        tier_of=tier_of,
        sampling=sampling,
        tiers_config_version=TIERS_VERSION,
    )


def write_artifact(tmp_path, record, **overrides) -> Path:
    """An artifact shaped exactly as ``command_run`` writes one."""
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "mode": "analysis",
        "cases": ["01-payments-checkout"],
        "trusted": True,
        "structural_failures": [],
        "repo_commit": {"commit": "0" * 40, "clean": True},
        "corpus_digest": "0" * 64,
        "provenance": record.to_json(),
    } | overrides
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# --- the artifact carries what the run observed ---------------------------


class TestArtifactSerialization:
    """What survives a sweep, and stays distinguishable once it has."""

    def test_the_served_build_survives_serialization(self, tmp_path, sampling):
        loaded = load_artifact(write_artifact(tmp_path, provenance(sampling)))

        identities = loaded.provenance.tier_identities()
        assert identities["base"].served_models == (BASE_SERVED,)
        assert identities["strong"].served_models == (STRONG_SERVED,)

    def test_requested_and_served_stay_distinguishable(self, tmp_path, sampling):
        """The whole point: an alias must never read as the build that answered.

        Certification is about what executed, so a promotion that could not
        tell the configured route from the observed build would bless the
        route — and provider-side routing means the two are not the same claim.
        """
        loaded = load_artifact(write_artifact(tmp_path, provenance(sampling)))

        base = loaded.provenance.tier_identities()["base"]
        assert base.requested_models == (BASE_REQUESTED,)
        assert base.served_models == (BASE_SERVED,)
        assert base.requested_models != base.served_models

    def test_fingerprints_and_their_sampling_both_survive(self, tmp_path, sampling):
        # Both halves, because either alone is unusable: a hash with no sampling
        # block cannot be recomputed, and sampling with no hash certifies
        # nothing.
        loaded = load_artifact(write_artifact(tmp_path, provenance(sampling)))

        expected = sampling_fingerprint(BASE_SERVED, sampling.for_tier("base"))
        assert loaded.provenance.tier_identities()["base"].fingerprints == (expected,)
        assert loaded.provenance.sampling["base"] == sampling.for_tier("base")
        assert loaded.provenance.sampling_config_version == sampling.version

    def test_every_execution_is_kept_not_just_one_per_node(self, tmp_path, sampling):
        """A build that moves mid-sweep is two observations, not a conflict."""
        executions = [
            node_run("extract", BASE_REQUESTED, BASE_SERVED, sampling),
            node_run("extract", BASE_REQUESTED, "openai/gpt-4.1-mini-b", sampling),
        ]
        loaded = load_artifact(
            write_artifact(tmp_path, provenance(sampling, executions))
        )

        assert len(loaded.provenance.node_runs["extract"]) == 2
        assert loaded.provenance.tier_identities()["base"].ambiguous

    def test_a_deterministic_node_contributes_no_identity(self, sampling):
        # A FunctionNode has no served build and so no generation identity; it
        # must not appear as a node with an empty one.
        executions = [
            *sweep(sampling),
            NodeRun(node="merge", duration_ms=3),
        ]

        record = provenance(sampling, executions)

        assert "merge" not in record.node_runs

    def test_the_observation_map_the_verdict_uses_comes_from_this_record(
        self, sampling
    ):
        """One record behind both the artifact and the certification verdict."""
        record = provenance(sampling)

        observations = record.observations()

        assert set(observations) == {"extract", CRITIC_NODE}
        assert observations["extract"] == frozenset(
            {sampling_fingerprint(BASE_SERVED, sampling.for_tier("base"))}
        )

    def test_a_real_sweep_records_what_its_graph_actually_served(self, monkeypatch):
        """End to end through the shipped executor, not hand-built node runs.

        Everything else here constructs :class:`NodeRun` objects directly, which
        proves the record round-trips but not that a sweep produces one. This
        drives the real graph over a real case with scripted models, so the
        served build in the artifact is one the executor read back off a
        response — the step that was missing when a promotion had nothing to
        read.
        """
        run = drive_sweep(monkeypatch, load_case(CASE_DIR), None)

        identity = run.provenance.tier_identities()["strong"]
        assert identity.requested_models == ("fake-pro-001",)
        assert identity.served_models == (served_build("fake-pro-001"),)
        # The verdict and the artifact are two views of this one record.
        assert set(run.observations) == set(run.provenance.node_runs)

    def test_a_fingerprint_without_a_served_build_is_refused(self, sampling):
        # NodeRun's own validator forbids this pairing, so the record is built
        # from a bypassed instance — the check exists because the alternative to
        # raising is blessing an empty served identity.
        broken = NodeRun.model_construct(
            node="extract",
            model=None,
            requested_model=BASE_REQUESTED,
            sampling_fingerprint="a" * 64,
            duration_ms=1,
        )

        with pytest.raises(ProvenanceError, match="served"):
            provenance(sampling, [broken])


# --- loading fails closed -------------------------------------------------


class TestLoadingFailsClosed:
    def test_an_unversioned_artifact_is_refused_by_name(self, tmp_path, sampling):
        path = write_artifact(tmp_path, provenance(sampling))
        payload = json.loads(path.read_text())
        del payload["artifact_version"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ProvenanceError, match="artifact_version"):
            load_artifact(path)

    def test_a_future_artifact_version_is_refused_rather_than_guessed_at(
        self, tmp_path, sampling
    ):
        path = write_artifact(
            tmp_path, provenance(sampling), artifact_version=ARTIFACT_VERSION + 1
        )

        with pytest.raises(ProvenanceError, match="unsupported artifact_version"):
            load_artifact(path)

    def test_an_artifact_with_no_provenance_block_is_refused(self, tmp_path, sampling):
        path = write_artifact(tmp_path, provenance(sampling))
        payload = json.loads(path.read_text())
        del payload["provenance"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ProvenanceError, match="no provenance block"):
            load_artifact(path)

    def test_an_edited_summary_is_refused_rather_than_believed(
        self, tmp_path, sampling
    ):
        """The summary is a rendering of ``node_runs``, never a second source.

        Editing it is how a served build the sweep never saw would otherwise
        reach a promotion, since the summary is the half a reader skims.
        """
        path = write_artifact(tmp_path, provenance(sampling))
        payload = json.loads(path.read_text())
        identities = payload["provenance"]["generation_identities"]
        identities["base"]["served_models"] = ["openai/something-else"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ProvenanceError, match="disagrees with the node_runs"):
            load_artifact(path)

    def test_an_edited_fingerprint_is_refused(self, tmp_path, sampling):
        """A serialized hash is checked against the canonical function, not read.

        Both copies are edited so the summary still agrees with the record;
        what catches it is recomputing from the served build beside it.
        """
        path = write_artifact(tmp_path, provenance(sampling))
        payload = json.loads(path.read_text())
        forged = "b" * 64
        payload["provenance"]["node_runs"]["extract"][0]["generation_fingerprint"] = (
            forged
        )
        payload["provenance"]["generation_identities"]["base"]["fingerprints"] = [
            forged
        ]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ProvenanceError, match="does not follow from"):
            load_artifact(path)

    def test_an_edited_sampling_block_is_refused(self, tmp_path, sampling):
        # Moving the sampling out from under a recorded hash is the same
        # forgery from the other side, and the same recomputation catches it.
        path = write_artifact(tmp_path, provenance(sampling))
        payload = json.loads(path.read_text())
        payload["provenance"]["sampling"]["base"]["temperature"] = 0.9
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ProvenanceError, match="does not follow from"):
            load_artifact(path)

    def test_invalid_json_is_refused(self, tmp_path):
        path = tmp_path / "artifact.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ProvenanceError, match="invalid JSON"):
            load_artifact(path)

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ProvenanceError, match="cannot be read"):
            load_artifact(tmp_path / "nope.json")


# --- planning a promotion -------------------------------------------------


class TestPromotionPlan:
    def test_the_plan_recomputes_every_fingerprint_it_will_bless(self, sampling):
        plan = plan_promotion(provenance(sampling))

        by_tier = {entry.tier: entry for entry in plan.tiers}
        assert by_tier["base"].fingerprint == sampling_fingerprint(
            BASE_SERVED, sampling.for_tier("base")
        )
        assert by_tier["strong"].fingerprint == sampling_fingerprint(
            STRONG_SERVED, sampling.for_tier("strong")
        )

    def test_the_plan_blesses_the_served_build_never_the_requested_one(self, sampling):
        plan = plan_promotion(provenance(sampling))

        assert plan.served_builds == {"base": BASE_SERVED, "strong": STRONG_SERVED}
        # The requested route would produce a different hash entirely, which is
        # the whole reason it must not be substituted.
        assert plan.served_builds["base"] != BASE_REQUESTED

    def test_a_sweep_that_observed_nothing_cannot_be_promoted(self, sampling):
        with pytest.raises(ProvenanceError, match="nothing to bless"):
            plan_promotion(provenance(sampling, []))

    def test_two_served_builds_on_one_tier_are_not_silently_collapsed(self, sampling):
        """Both builds produced numbers in the same aggregate; neither wins by order."""
        executions = [
            node_run("extract", BASE_REQUESTED, BASE_SERVED, sampling),
            node_run("extract", BASE_REQUESTED, "openai/gpt-4.1-mini-b", sampling),
        ]

        with pytest.raises(ProvenanceError, match="answered by 2 different"):
            plan_promotion(provenance(sampling, executions))

    def test_an_explicit_choice_resolves_the_ambiguity(self, sampling):
        executions = [
            node_run("extract", BASE_REQUESTED, BASE_SERVED, sampling),
            node_run("extract", BASE_REQUESTED, "openai/gpt-4.1-mini-b", sampling),
        ]

        plan = plan_promotion(
            provenance(sampling, executions), {"base": "openai/gpt-4.1-mini-b"}
        )

        assert plan.served_builds == {"base": "openai/gpt-4.1-mini-b"}
        # What is *not* being blessed is carried too, so the operator sees it.
        assert plan.tiers[0].observed_served_models == (
            BASE_SERVED,
            "openai/gpt-4.1-mini-b",
        )

    def test_a_choice_can_narrow_the_observations_but_never_add_to_them(self, sampling):
        """``--served`` selects; it is not a channel for asserting a build."""
        with pytest.raises(ProvenanceError, match="never observed that build"):
            plan_promotion(provenance(sampling), {"base": "openai/invented-build"})

    def test_a_choice_for_an_unexercised_tier_is_refused(self, sampling):
        executions = [node_run("extract", BASE_REQUESTED, BASE_SERVED, sampling)]

        with pytest.raises(ProvenanceError, match="did not exercise"):
            plan_promotion(provenance(sampling, executions), {"strong": STRONG_SERVED})

    def test_a_tier_the_sweep_never_ran_is_left_out_of_the_plan(self, sampling):
        executions = [node_run("extract", BASE_REQUESTED, BASE_SERVED, sampling)]

        plan = plan_promotion(provenance(sampling, executions))

        assert set(plan.served_builds) == {"base"}


# --- the CLI writes both files, or neither -------------------------------


EMPTY_MANIFEST = "version = 2\n\n[tiers]\nbase = []\nstrong = []\n"


@pytest.fixture
def promotion_env(tmp_path, monkeypatch):
    """A deployment whose sampling file and manifest are throwaway copies.

    The manifest starts present and empty, which is the state the repo ships
    and the state every deployment is in before its first sweep.
    """
    sampling_copy = tmp_path / "sampling.toml"
    sampling_copy.write_text(SAMPLING_PATH.read_text(encoding="utf-8"), "utf-8")
    manifest_copy = tmp_path / "blessed.toml"
    manifest_copy.write_text(EMPTY_MANIFEST, encoding="utf-8")
    for name, value in (
        TEST_TIER_ENV
        | {
            SAMPLING_VAR: str(sampling_copy),
            BLESSED_FINGERPRINTS_VAR: str(manifest_copy),
        }
    ).items():
        monkeypatch.setenv(name, value)
    return sampling_copy, manifest_copy


class TestPromoteCommand:
    def test_a_preview_shows_the_identities_and_writes_nothing(
        self, tmp_path, sampling, promotion_env, capsys
    ):
        _, manifest_copy = promotion_env
        artifact = write_artifact(tmp_path, provenance(sampling))

        assert main(["promote", str(artifact)]) == 0

        out = capsys.readouterr().out
        assert BASE_SERVED in out
        assert BASE_REQUESTED in out
        assert sampling_fingerprint(BASE_SERVED, sampling.for_tier("base")) in out
        # An unset param must not read as one pinned to zero.
        assert "top_p:" in out and "unset" in out
        assert manifest_copy.read_text(encoding="utf-8") == EMPTY_MANIFEST

    def test_yes_blesses_the_recomputed_fingerprints_under_the_right_tiers(
        self, tmp_path, sampling, promotion_env
    ):
        _, manifest_copy = promotion_env
        artifact = write_artifact(tmp_path, provenance(sampling))

        assert main(["promote", str(artifact), "--yes"]) == 0

        manifest = load_manifest(manifest_copy)
        assert manifest.blessed_for("base") == frozenset(
            {sampling_fingerprint(BASE_SERVED, sampling.for_tier("base"))}
        )
        assert manifest.blessed_for("strong") == frozenset(
            {sampling_fingerprint(STRONG_SERVED, sampling.for_tier("strong"))}
        )

    def test_promoting_preserves_fingerprints_blessed_earlier(
        self, tmp_path, sampling, promotion_env
    ):
        """A manifest accumulates: blessing is additive, never a replacement."""
        _, manifest_copy = promotion_env
        existing = "c" * 64
        manifest_copy.write_text(
            f'version = 2\n[tiers]\nbase = ["{existing}"]\nstrong = []\n', "utf-8"
        )
        artifact = write_artifact(tmp_path, provenance(sampling))

        assert main(["promote", str(artifact), "--yes"]) == 0

        blessed = load_manifest(manifest_copy).blessed_for("base")
        assert existing in blessed
        assert len(blessed) == 2

    def test_promoting_the_same_artifact_twice_adds_no_duplicate(
        self, tmp_path, sampling, promotion_env
    ):
        _, manifest_copy = promotion_env
        artifact = write_artifact(tmp_path, provenance(sampling))

        main(["promote", str(artifact), "--yes"])
        main(["promote", str(artifact), "--yes"])

        assert len(load_manifest(manifest_copy).blessed_for("base")) == 1

    def test_an_ambiguous_tier_fails_the_command_and_names_the_flag(
        self, tmp_path, sampling, promotion_env, capsys
    ):
        _, manifest_copy = promotion_env
        executions = [
            node_run("extract", BASE_REQUESTED, BASE_SERVED, sampling),
            node_run("extract", BASE_REQUESTED, "openai/gpt-4.1-mini-b", sampling),
        ]
        artifact = write_artifact(tmp_path, provenance(sampling, executions))

        assert main(["promote", str(artifact), "--yes"]) == 1

        assert "--served base=" in capsys.readouterr().err
        assert manifest_copy.read_text(encoding="utf-8") == EMPTY_MANIFEST

    def test_an_artifact_from_another_sampling_schema_is_refused(
        self, tmp_path, sampling, promotion_env, capsys
    ):
        """Re-pinning across a schema change would bless params no run carried."""
        _, manifest_copy = promotion_env
        path = write_artifact(tmp_path, provenance(sampling))
        payload = json.loads(path.read_text())
        payload["provenance"]["sampling_config_version"] = sampling.version - 1
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert main(["promote", str(path), "--yes"]) == 1

        assert "schema" in capsys.readouterr().err
        assert manifest_copy.read_text(encoding="utf-8") == EMPTY_MANIFEST

    def test_a_refused_promotion_leaves_the_sampling_file_untouched(
        self, tmp_path, sampling, promotion_env
    ):
        sampling_copy, _ = promotion_env
        before = sampling_copy.read_text(encoding="utf-8")
        path = write_artifact(tmp_path, provenance(sampling))
        payload = json.loads(path.read_text())
        payload["artifact_version"] = 99
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert main(["promote", str(path), "--yes"]) == 1

        assert sampling_copy.read_text(encoding="utf-8") == before

    def test_the_sweeps_own_sampling_is_what_gets_re_pinned(
        self, tmp_path, promotion_env
    ):
        """The values promoted are the ones measured, not the file's current ones."""
        sampling_copy, _ = promotion_env
        # A param the file pins: promotion re-pins those, and refuses to pin one
        # the file deliberately leaves unset.
        measured = load_sampling(
            SAMPLING_PATH, env={"STRIDE_SAMPLING_BASE_MAX_OUTPUT_TOKENS": "12288"}
        )
        artifact = write_artifact(tmp_path, provenance(measured))

        assert main(["promote", str(artifact), "--yes"]) == 0

        assert "max_output_tokens = 12288" in sampling_copy.read_text(encoding="utf-8")
