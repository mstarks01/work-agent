"""The offline eval gate (ticket 08): certify(), the manifest, and promotion.

Zero Vertex calls — the whole point of ticket 03 §4 is a gate the credential-
free suite exercises. Fingerprints are scripted or built from the shipped graph
without running it; the live gate *run* stays out of scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness import modes
from evals.harness.certify import (
    DEFAULT_MANIFEST_PATH,
    BlessedManifest,
    CertificationError,
    certify,
    load_manifest,
    promote,
    report_fingerprints,
)
from stride_service.graph import ENTRY_EXTRACT
from stride_service.model_tiers import load_model_tiers
from stride_service.report import NodeRun, StrideReport
from stride_service.sampling import load_sampling
from tests.factories import sample_report

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLING_PATH = REPO_ROOT / "config" / "sampling.toml"
TIERS_PATH = REPO_ROOT / "config" / "model_tiers.toml"

FP_A = "a" * 64
FP_B = "b" * 64
FP_C = "c" * 64


def _manifest(nodes: dict[str, set[str]]) -> BlessedManifest:
    return BlessedManifest(version=1, nodes=nodes)


# --- certify() ------------------------------------------------------------


def test_exact_match_is_certified():
    manifest = _manifest({"extract": {FP_A}, "critic": {FP_B}})

    result = certify({"extract": FP_A, "critic": FP_B}, manifest)

    assert result.certified
    assert result.uncertified == ()


def test_a_mismatched_node_is_uncertified_and_listed():
    manifest = _manifest({"extract": {FP_A}, "critic": {FP_B}})

    result = certify({"extract": FP_A, "critic": FP_C}, manifest)

    assert not result.certified
    assert [n.node for n in result.uncertified] == ["critic"]
    assert result.uncertified[0].fingerprint == FP_C


def test_a_node_absent_from_the_manifest_fails_closed():
    # An empty / missing blessed set certifies nothing — the honest pre-baseline
    # state the shipped manifest ships in.
    result = certify({"extract": FP_A}, _manifest({}))

    assert not result.certified
    assert result.uncertified[0].node == "extract"


def test_multiple_blessed_builds_per_node_all_certify():
    manifest = _manifest({"extract": {FP_A, FP_B}})

    assert certify({"extract": FP_A}, manifest).certified
    assert certify({"extract": FP_B}, manifest).certified
    assert not certify({"extract": FP_C}, manifest).certified


# --- manifest loading fails closed ---------------------------------------


def test_shipped_manifest_loads_and_ships_empty():
    manifest = load_manifest()

    assert manifest.version == 1
    # Every blessed set is empty until a live sweep promotes one.
    assert all(not prints for prints in manifest.nodes.values())
    assert manifest.blessed_for("extract") == frozenset()


def test_load_rejects_a_bad_version(tmp_path):
    bad = tmp_path / "m.toml"
    bad.write_text("version = 2\n[nodes]\n", encoding="utf-8")

    with pytest.raises(CertificationError, match="unsupported version 2"):
        load_manifest(bad)


def test_load_rejects_a_non_hex_fingerprint(tmp_path):
    bad = tmp_path / "m.toml"
    bad.write_text('version = 1\n[nodes]\nextract = ["nope"]\n', encoding="utf-8")

    with pytest.raises(CertificationError):
        load_manifest(bad)


def test_load_rejects_a_stray_top_level_key(tmp_path):
    bad = tmp_path / "m.toml"
    bad.write_text("version = 1\nbogus = 1\n[nodes]\n", encoding="utf-8")

    with pytest.raises(CertificationError):
        load_manifest(bad)


def test_load_rejects_invalid_toml(tmp_path):
    bad = tmp_path / "m.toml"
    bad.write_text("version = = 1\n", encoding="utf-8")

    with pytest.raises(CertificationError, match="invalid TOML"):
        load_manifest(bad)


def test_load_rejects_a_missing_file(tmp_path):
    with pytest.raises(CertificationError, match="cannot be read"):
        load_manifest(tmp_path / "does-not-exist.toml")


# --- report_fingerprints skips deterministic nodes ------------------------


def _report_with_nodes(nodes: list[NodeRun]) -> StrideReport:
    return sample_report().model_copy(update={"nodes": nodes})


def test_report_fingerprints_covers_llm_nodes_only():
    report = _report_with_nodes(
        [
            NodeRun(node="extract", model="gemini", sampling_fingerprint=FP_A, duration_ms=1),
            NodeRun(node="validate", model=None, duration_ms=0),
        ]
    )

    assert report_fingerprints(report) == {"extract": FP_A}


# --- sweep parametrization: each variant is fingerprinted, drift flagged ---


def _build_fingerprints(sampling, served="fake-model-001"):
    """The shipped graph's per-node fingerprints for one sampling variant.

    Building — not running — the pipeline is enough: a node's fingerprint is a
    property of its served model and resolved tier sampling, both fixed at build
    (ticket 07). A string model keeps it credential-free.
    """
    pipeline = modes.build_eval_pipeline(
        ENTRY_EXTRACT,
        resolve_model=lambda _tier_node: served,
        sampling=sampling,
    )
    return pipeline.node_fingerprints


def test_a_pro_override_reprints_only_pro_nodes():
    default = load_sampling(SAMPLING_PATH)
    overridden = load_sampling(
        SAMPLING_PATH, env={"STRIDE_SAMPLING_STRONG_TEMPERATURE": "0.9"}
    )

    base = _build_fingerprints(default)
    swept = _build_fingerprints(overridden)

    # Flash nodes are untouched; every pro node's generation identity moved.
    assert base["extract"] == swept["extract"]
    assert base["critic"] != swept["critic"]
    assert base["analyst_spoofing"] != swept["analyst_spoofing"]


def test_certify_flags_an_override_drifted_run():
    default = load_sampling(SAMPLING_PATH)
    overridden = load_sampling(
        SAMPLING_PATH, env={"STRIDE_SAMPLING_STRONG_TEMPERATURE": "0.9"}
    )
    manifest = _manifest(
        {node: {fp} for node, fp in _build_fingerprints(default).items()}
    )

    assert certify(_build_fingerprints(default), manifest).certified
    drifted = certify(_build_fingerprints(overridden), manifest)
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
    manifest = _manifest(
        {node: {fp} for node, fp in _build_fingerprints(default, "build-001").items()}
    )

    # Same sampling, a different served build — every node's hash moves.
    drifted = certify(_build_fingerprints(default, "build-002"), manifest)
    assert not drifted.certified
    assert len(drifted.uncertified) == len(_build_fingerprints(default))


# --- promotion writes both files, single-sourced --------------------------


def _promote_setup(tmp_path):
    sampling_copy = tmp_path / "sampling.toml"
    sampling_copy.write_text(SAMPLING_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    manifest_copy = tmp_path / "blessed.toml"
    resolve_tier = load_model_tiers(TIERS_PATH).resolve_tier
    return sampling_copy, manifest_copy, resolve_tier


def test_promote_reprints_values_in_place_and_writes_the_manifest(tmp_path):
    sampling_copy, manifest_copy, resolve_tier = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)
    hot_base = winner.for_tier("base").model_copy(update={"temperature": 0.2})
    winner = winner.model_copy(update={"tiers": {**winner.tiers, "base": hot_base}})

    manifest = promote(
        winner,
        {"extract": "build-001", "critic": "build-001"},
        resolve_tier,
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
    assert reloaded.blessed_for("extract") == manifest.blessed_for("extract")
    assert certify(_build_fingerprints(winner, "build-001"), reloaded).certified is False
    # extract (base) is blessed; only the two named nodes were promoted.
    assert reloaded.blessed_for("extract")
    assert reloaded.blessed_for("critic")
    assert reloaded.blessed_for("recritic") == frozenset()


def test_promote_accumulates_blessed_builds(tmp_path):
    sampling_copy, manifest_copy, resolve_tier = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)

    promote(
        winner, {"extract": "build-001"}, resolve_tier,
        sampling_path=sampling_copy, manifest_path=manifest_copy,
    )
    manifest = promote(
        winner, {"extract": "build-002"}, resolve_tier,
        sampling_path=sampling_copy, manifest_path=manifest_copy,
    )

    # A node accumulates several blessed served-builds (ticket 03 §4).
    assert len(manifest.blessed_for("extract")) == 2


def test_promote_refuses_to_pin_a_previously_unset_param(tmp_path):
    sampling_copy, manifest_copy, resolve_tier = _promote_setup(tmp_path)
    winner = load_sampling(SAMPLING_PATH)
    tuned = winner.for_tier("strong").model_copy(update={"top_p": 0.9})
    winner = winner.model_copy(update={"tiers": {**winner.tiers, "strong": tuned}})

    with pytest.raises(CertificationError, match="tiers.strong.top_p"):
        promote(
            winner, {"critic": "build-001"}, resolve_tier,
            sampling_path=sampling_copy, manifest_path=manifest_copy,
        )
    # A rejected promotion leaves neither file touched.
    assert not manifest_copy.exists()
    assert sampling_copy.read_text() == SAMPLING_PATH.read_text()


def test_default_manifest_path_points_at_the_shipped_file():
    assert DEFAULT_MANIFEST_PATH == REPO_ROOT / "evals" / "blessed-fingerprints.toml"
