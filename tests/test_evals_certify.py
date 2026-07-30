"""Sweep promotion: the write half of certification (the pure check moved).

Zero provider calls. ``promote`` re-pins ``sampling.toml`` in place and records
the fingerprints that pinning implies; the check those fingerprints are later
tested against now lives in ``tests/test_certification.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness import modes
from evals.harness.certify import promote, promotion_paths
from stride_service.certification import (
    MANIFEST_VERSION,
    BlessedManifest,
    CertificationError,
    certify,
    load_manifest,
)
from stride_service.deployment import SAMPLING_VAR, Deployment
from stride_service.graph import ENTRY_EXTRACT, TIER_NODE_BY_GRAPH_NODE
from stride_service.model_tiers import load_model_tiers
from stride_service.sampling import load_sampling, sampling_fingerprint

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLING_PATH = REPO_ROOT / "config" / "sampling.toml"
TIERS_PATH = REPO_ROOT / "config" / "model_tiers.toml"

_TIER_OF = load_model_tiers(TIERS_PATH, env={}).resolve_tier


def tier_of(graph_node: str) -> str:
    return _TIER_OF(TIER_NODE_BY_GRAPH_NODE[graph_node])


def _manifest(tiers: dict[str, set[str]]) -> BlessedManifest:
    return BlessedManifest(version=MANIFEST_VERSION, tiers=tiers)


def _blessed_from(observations: dict[str, frozenset[str]]) -> BlessedManifest:
    """Bless every fingerprint a variant produced, collapsed onto its tier."""
    tiers: dict[str, set[str]] = {}
    for node, prints in observations.items():
        tiers.setdefault(tier_of(node), set()).update(prints)
    return _manifest(tiers)


ALL_NODES = tuple(TIER_NODE_BY_GRAPH_NODE)


# --- sweep parametrization: each variant is fingerprinted, drift flagged ---


def _build_fingerprints(sampling, served="fake-model-001"):
    """The per-node fingerprints one sampling variant would produce.

    The served build is now supplied rather than read off the built graph: a
    fingerprint's model half is what *answered*, which a build cannot know
    (#7 decision 2). Building the pipeline is still what resolves each node's
    tier sampling, and a string model keeps it credential-free.
    """
    pipeline = modes.build_eval_pipeline(
        ENTRY_EXTRACT,
        resolve_model=lambda _tier_node: served,
        sampling=sampling,
    )
    return {
        node: frozenset({sampling_fingerprint(served, node_sampling)})
        for node, node_sampling in pipeline.node_sampling.items()
    }


def test_a_pro_override_reprints_only_pro_nodes():
    default = load_sampling(SAMPLING_PATH)
    overridden = load_sampling(
        SAMPLING_PATH, env={"STRIDE_SAMPLING_STRONG_TEMPERATURE": "0.9"}
    )

    base = _build_fingerprints(default)
    swept = _build_fingerprints(overridden)

    # Base nodes are untouched; every strong node's generation identity moved.
    assert base["extract"] == swept["extract"]
    assert base["critic"] != swept["critic"]
    assert base["analyst_spoofing"] != swept["analyst_spoofing"]


def test_certify_flags_an_override_drifted_run():
    default = load_sampling(SAMPLING_PATH)
    overridden = load_sampling(
        SAMPLING_PATH, env={"STRIDE_SAMPLING_STRONG_TEMPERATURE": "0.9"}
    )
    manifest = _blessed_from(_build_fingerprints(default))

    assert certify(
        _build_fingerprints(default), manifest, tier_of, ALL_NODES
    ).certified
    drifted = certify(_build_fingerprints(overridden), manifest, tier_of, ALL_NODES)
    assert not drifted.certified
    assert {n.node for n in drifted.uncertified} == {
        "critic",
        "recritic",
        "analyst_spoofing",
        "analyst_tampering",
        "analyst_repudiation",
        "analyst_information_disclosure",
        "analyst_denial_of_service",
        "analyst_elevation_of_privilege",
    }


def test_certify_flags_a_served_build_drifted_run():
    default = load_sampling(SAMPLING_PATH)
    manifest = _blessed_from(_build_fingerprints(default, "build-001"))

    # Same sampling, a different served build — every tier's hash moves.
    drifted = certify(
        _build_fingerprints(default, "build-002"), manifest, tier_of, ALL_NODES
    )
    assert not drifted.certified
    assert len(drifted.uncertified) == len(_build_fingerprints(default))


# --- promotion writes both files, single-sourced --------------------------


def _promote_setup(tmp_path):
    sampling_copy = tmp_path / "sampling.toml"
    sampling_copy.write_text(SAMPLING_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    manifest_copy = tmp_path / "blessed.toml"
    return sampling_copy, manifest_copy


def test_promote_reprints_values_in_place_and_writes_the_manifest(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)
    hot_base = winner.for_tier("base").model_copy(update={"temperature": 0.2})
    winner = winner.model_copy(update={"tiers": {**winner.tiers, "base": hot_base}})

    manifest = promote(
        winner,
        {"base": "build-001", "strong": "build-001"},
        sampling_path=sampling_copy,
        manifest_path=manifest_copy,
    )

    rewritten = sampling_copy.read_text(encoding="utf-8")
    assert "temperature = 0.2" in rewritten
    # Comments survive the in-place edit — the file's whole value is its record.
    assert "pinned greedy decoding" in rewritten
    assert "# top_p" in rewritten  # unset lines untouched
    # The manifest reloads and its fingerprints are single-sourced from `winner`.
    reloaded = load_manifest(manifest_copy)
    assert reloaded.blessed_for("base") == manifest.blessed_for("base")
    assert reloaded.blessed_for("base")
    assert reloaded.blessed_for("strong")


def test_promoting_one_tier_leaves_the_other_unblessed(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)

    manifest = promote(
        winner,
        {"base": "build-001"},
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
            {"extract": "build-001"},  # a node name, not a tier
            sampling_path=sampling_copy,
            manifest_path=manifest_copy,
        )


def test_promote_accumulates_blessed_builds(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)

    promote(
        winner, {"base": "build-001"},
        sampling_path=sampling_copy, manifest_path=manifest_copy,
    )
    manifest = promote(
        winner, {"base": "build-002"},
        sampling_path=sampling_copy, manifest_path=manifest_copy,
    )

    # A tier accumulates several blessed served-builds (ticket 03 §4).
    assert len(manifest.blessed_for("base")) == 2


def test_promote_refuses_to_pin_a_previously_unset_param(tmp_path):
    sampling_copy, manifest_copy = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)
    tuned = winner.for_tier("strong").model_copy(update={"top_p": 0.9})
    winner = winner.model_copy(update={"tiers": {**winner.tiers, "strong": tuned}})

    with pytest.raises(CertificationError, match="tiers.strong.top_p"):
        promote(
            winner, {"strong": "build-001"},
            sampling_path=sampling_copy, manifest_path=manifest_copy,
        )
    # A rejected promotion leaves neither file touched.
    assert not manifest_copy.exists()
    assert sampling_copy.read_text() == SAMPLING_PATH.read_text()


def test_the_manifest_lives_with_the_service_config_not_under_evals():
    # It moved to config/ when the service became what certifies: evals/ does
    # not ship, so a manifest under it was unreachable from the production image.
    paths = promotion_paths()
    assert paths.blessed_fingerprints == REPO_ROOT / "config" / "blessed-fingerprints.toml"


def test_a_promotion_re_pins_the_file_the_sweep_actually_measured(tmp_path):
    """A redirected deployment promotes into its own config, not the repo's."""
    redirected = tmp_path / "sampling.toml"
    redirected.write_text(SAMPLING_PATH.read_text(), encoding="utf-8")

    paths = promotion_paths(
        Deployment.from_env(env={SAMPLING_VAR: str(redirected)})
    )

    assert paths.sampling == redirected
