"""Sweep promotion: the write half of certification (the pure check moved).

Zero provider calls. ``promote`` re-pins ``sampling.toml`` in place and records
the fingerprints that pinning implies; the check those fingerprints are later
tested against now lives in ``tests/test_certification.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.certification import (
    MANIFEST_VERSION,
    BlessedManifest,
    CertificationError,
    certify,
    load_manifest,
)
from analysis_service.deployment import SAMPLING_VAR, Deployment
from analysis_service.graph import ENTRY_EXTRACT, tier_node_by_graph_node
from analysis_service.identity import build_identity
from analysis_service.model_tiers import TierName
from analysis_service.sampling import load_sampling
from evals.harness import modes
from evals.harness.certify import TierIdentityKey, promote, promotion_paths
from tests.factories import (
    DEFAULT_FRAMEWORKS,
    SAMPLE_INSTRUCTIONS,
    TEST_TIER_ENV,
    repo_tiers,
    sample_fingerprint,
)

TIER_NODE_BY_GRAPH_NODE = tier_node_by_graph_node(DEFAULT_FRAMEWORKS)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLING_PATH = REPO_ROOT / "config" / "sampling.toml"
_TIER_OF = repo_tiers().resolve_tier


def tier_of(graph_node: str) -> TierName:
    return _TIER_OF(TIER_NODE_BY_GRAPH_NODE[graph_node])


def _manifest(tiers: dict[TierName, set[str]]) -> BlessedManifest:
    return BlessedManifest(
        version=MANIFEST_VERSION,
        tiers={tier: frozenset(prints) for tier, prints in tiers.items()},
    )


def _blessed_from(observations: dict[str, frozenset[str]]) -> BlessedManifest:
    """Bless every fingerprint a variant produced, collapsed onto its tier."""
    tiers: dict[TierName, set[str]] = {}
    for node, prints in observations.items():
        tiers.setdefault(tier_of(node), set()).update(prints)
    return _manifest(tiers)


ALL_NODES = tuple(TIER_NODE_BY_GRAPH_NODE)


def keys(**served: str) -> dict[str, tuple[TierIdentityKey, ...]]:
    """A tier -> identity-key map for a promotion, one key per tier.

    Promotion blesses a whole identity, so a test naming only a served build
    supplies the rest. The requested route echoes the served one, which is what
    an offline stand-in produces.
    """
    return {
        tier: (
            TierIdentityKey(
                requested=build,
                served=build,
                instruction_sha256=SAMPLE_INSTRUCTIONS,
            ),
        )
        for tier, build in served.items()
    }


# --- sweep parametrization: each variant is fingerprinted, drift flagged ---


def _build_fingerprints(sampling, served="vertex_ai/fake-model-001"):
    """The per-node fingerprints one sampling variant would produce.

    The served build is supplied rather than read off the built graph: a
    fingerprint's model half is what *answered*, which a build cannot know.
    Building the pipeline is what resolves each node's tier sampling, and a
    string model keeps it credential-free.
    """
    pipeline = modes.build_eval_pipeline(
        ENTRY_EXTRACT,
        resolve_model=lambda _tier_node: served,
        sampling=sampling,
    )
    return {
        node: frozenset({sample_fingerprint(served, node_sampling)})
        for node, node_sampling in pipeline.node_sampling.items()
    }


def test_a_pro_override_reprints_only_pro_nodes():
    default = load_sampling(SAMPLING_PATH)
    overridden = load_sampling(
        SAMPLING_PATH, env={"ANALYSIS_SAMPLING_STRONG_TEMPERATURE": "0.9"}
    )

    base = _build_fingerprints(default)
    swept = _build_fingerprints(overridden)

    # Base nodes are untouched; every strong node's execution identity moved.
    assert base["extract"] == swept["extract"]
    assert base["critic_stride"] != swept["critic_stride"]
    assert base["analyze_stride_spoofing"] != swept["analyze_stride_spoofing"]


def test_certify_flags_an_override_drifted_run():
    default = load_sampling(SAMPLING_PATH)
    overridden = load_sampling(
        SAMPLING_PATH, env={"ANALYSIS_SAMPLING_STRONG_TEMPERATURE": "0.9"}
    )
    manifest = _blessed_from(_build_fingerprints(default))

    assert certify(_build_fingerprints(default), manifest, tier_of, ALL_NODES).certified
    drifted = certify(_build_fingerprints(overridden), manifest, tier_of, ALL_NODES)
    assert not drifted.certified
    # Every strong node, which is now per framework: one critic, one re-ask
    # and one lane agent per lane, all named for the package they belong to.
    assert {n.node for n in drifted.uncertified} == {
        "critic_stride",
        "recritic_stride",
        "analyze_stride_spoofing",
        "analyze_stride_tampering",
        "analyze_stride_repudiation",
        "analyze_stride_information_disclosure",
        "analyze_stride_denial_of_service",
        "analyze_stride_elevation_of_privilege",
    }


def test_certify_flags_a_served_build_drifted_run():
    default = load_sampling(SAMPLING_PATH)
    manifest = _blessed_from(_build_fingerprints(default, "vertex_ai/build-001"))

    # Same sampling, a different served build — every tier's hash moves.
    drifted = certify(
        _build_fingerprints(default, "vertex_ai/build-002"),
        manifest,
        tier_of,
        ALL_NODES,
    )
    assert not drifted.certified
    assert len(drifted.uncertified) == len(_build_fingerprints(default))


# --- promotion writes both files, single-sourced --------------------------


def _promote_setup(tmp_path):
    sampling_copy = tmp_path / "sampling.toml"
    sampling_copy.write_text(
        SAMPLING_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    manifest_copy = tmp_path / "blessed.toml"
    return sampling_copy, manifest_copy


def test_promote_reprints_values_in_place_and_writes_the_manifest(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)
    # A param the file pins, since those are the only ones promotion may
    # rewrite: an unset one raises rather than being quietly uncommented.
    roomier = winner.for_tier("base").model_copy(update={"max_output_tokens": 12288})
    winner = winner.model_copy(update={"tiers": {**winner.tiers, "base": roomier}})

    manifest = promote(
        winner,
        keys(base="vertex_ai/build-001", strong="vertex_ai/build-001"),
        build=build_identity(),
        sampling_path=sampling_copy,
        manifest_path=manifest_copy,
    )

    rewritten = sampling_copy.read_text(encoding="utf-8")
    assert "max_output_tokens = 12288" in rewritten
    # The rewritten line keeps its own comment — the file's whole value is its
    # record, and the edited line is where losing one would matter most.
    assert "pinned: silence means a vendor-derived cap" in rewritten
    assert "# top_p" in rewritten  # unset lines untouched
    # The manifest reloads and its fingerprints are single-sourced from `winner`.
    reloaded = load_manifest(manifest_copy)
    assert reloaded.blessed_for("base") == manifest.blessed_for("base")
    assert reloaded.blessed_for("base")
    assert reloaded.blessed_for("strong")


def test_promoting_a_param_the_file_leaves_unset_raises(tmp_path):
    """The workflow a temperature sweep now lands on.

    ``temperature`` is unset in the shipped file, so a sweep that measured one
    is asking to *pin* a param rather than to re-pin it. That owes a rationale
    in the file, which a sweep cannot write, so promotion stops and says so
    instead of uncommenting the line.
    """
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)
    hot = winner.for_tier("strong").model_copy(update={"temperature": 0.7})
    winner = winner.model_copy(update={"tiers": {**winner.tiers, "strong": hot}})
    before = sampling_copy.read_text(encoding="utf-8")

    with pytest.raises(CertificationError, match="tiers.strong.temperature"):
        promote(
            winner,
            keys(base="vertex_ai/build-001", strong="vertex_ai/build-001"),
            build=build_identity(),
            sampling_path=sampling_copy,
            manifest_path=manifest_copy,
        )

    # Nothing written on either path: a refused promotion is not a partial one.
    assert sampling_copy.read_text(encoding="utf-8") == before
    assert not manifest_copy.exists()


def test_promoting_one_tier_leaves_the_other_unblessed(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)

    manifest = promote(
        winner,
        keys(base="vertex_ai/build-001"),
        build=build_identity(),
        sampling_path=sampling_copy,
        manifest_path=manifest_copy,
    )

    assert manifest.blessed_for("base")
    assert manifest.blessed_for("strong") == frozenset()


def test_promote_rejects_an_unknown_tier(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    with pytest.raises(CertificationError, match="unknown tier"):
        promote(
            load_sampling(SAMPLING_PATH),
            keys(extract="vertex_ai/build-001"),  # a node name, not a tier
            build=build_identity(),
            sampling_path=sampling_copy,
            manifest_path=manifest_copy,
        )


def test_promote_accumulates_blessed_builds(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)

    promote(
        winner,
        keys(base="vertex_ai/build-001"),
        build=build_identity(),
        sampling_path=sampling_copy,
        manifest_path=manifest_copy,
    )
    manifest = promote(
        winner,
        keys(base="vertex_ai/build-002"),
        build=build_identity(),
        sampling_path=sampling_copy,
        manifest_path=manifest_copy,
    )

    # A tier accumulates several blessed served-builds.
    assert len(manifest.blessed_for("base")) == 2


def test_promote_refuses_to_pin_a_previously_unset_param(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)
    tuned = winner.for_tier("strong").model_copy(update={"top_p": 0.9})
    winner = winner.model_copy(update={"tiers": {**winner.tiers, "strong": tuned}})

    with pytest.raises(CertificationError, match="tiers.strong.top_p"):
        promote(
            winner,
            keys(strong="vertex_ai/build-001"),
            build=build_identity(),
            sampling_path=sampling_copy,
            manifest_path=manifest_copy,
        )
    # A rejected promotion leaves neither file touched.
    assert not manifest_copy.exists()
    assert sampling_copy.read_text() == SAMPLING_PATH.read_text()


def test_the_manifest_lives_with_the_service_config_not_under_evals():
    # It moved to config/ when the service became what certifies: evals/ does
    # not ship, so a manifest under it was unreachable from the production image.
    paths = promotion_paths()
    assert (
        paths.blessed_fingerprints == REPO_ROOT / "config" / "blessed-fingerprints.toml"
    )


def test_a_promotion_re_pins_the_file_the_sweep_actually_measured(tmp_path):
    """A redirected deployment promotes into its own config, not the repo's."""
    redirected = tmp_path / "sampling.toml"
    redirected.write_text(SAMPLING_PATH.read_text(), encoding="utf-8")

    paths = promotion_paths(
        Deployment.from_env(env=TEST_TIER_ENV | {SAMPLING_VAR: str(redirected)})
    )

    assert paths.sampling == redirected
