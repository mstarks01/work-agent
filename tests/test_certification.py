"""Certification: the pure check, now the service's rather than the harness's.

Zero provider calls — the whole point is a gate the credential-free suite
exercises. Fingerprints are scripted; the live gate *run* stays out of scope.
"""

from __future__ import annotations

import pytest

from stride_service.certification import (
    MANIFEST_VERSION,
    BlessedManifest,
    CertificationError,
    CertificationGate,
    CertifyResult,
    certify,
    load_manifest,
    report_fingerprints,
)
from stride_service.model_tiers import TierName
from stride_service.report import NodeRun, Report
from tests.factories import sample_report

FP_A = "a" * 64
FP_B = "b" * 64
FP_C = "c" * 64

# extract/repair run on base; the category agents, critic and recritic on strong.
_TIERS: dict[str, TierName] = {
    "extract": "base",
    "repair": "base",
    "critic": "strong",
    "recritic": "strong",
    "analyze_spoofing": "strong",
}
ALL_NODES = tuple(_TIERS)


def tier_of(node: str) -> TierName:
    return _TIERS[node]


def manifest(tiers: dict[TierName, set[str]]) -> BlessedManifest:
    return BlessedManifest(
        version=MANIFEST_VERSION,
        tiers={tier: frozenset(prints) for tier, prints in tiers.items()},
    )


def observed(**nodes: set[str]) -> dict[str, frozenset[str]]:
    return {node: frozenset(prints) for node, prints in nodes.items()}


class TestTierKeying:
    """Blessing is per tier, because the hash is."""

    def test_two_nodes_on_one_tier_certify_off_one_blessed_set(self):
        # critic and recritic present a byte-identical hash by construction.
        # Per-node keying would call it blessed under one key and unblessed
        # under the other, reporting the first revise path uncertified on a
        # technicality.
        result = certify(
            observed(critic={FP_A}, recritic={FP_A}),
            manifest({"strong": {FP_A}}),
            tier_of,
            ALL_NODES,
        )
        assert result.certified

    def test_a_tier_with_no_blessed_set_fails_closed(self):
        # The honest pre-baseline state the shipped manifest ships in.
        result = certify(observed(extract={FP_A}), manifest({}), tier_of, ALL_NODES)
        assert not result.certified
        assert result.uncertified[0].node == "extract"

    def test_a_tier_accumulates_several_blessed_builds(self):
        blessed = manifest({"base": {FP_A, FP_B}})
        assert certify(observed(extract={FP_A}), blessed, tier_of, ALL_NODES).certified
        assert certify(observed(extract={FP_B}), blessed, tier_of, ALL_NODES).certified
        assert not certify(
            observed(extract={FP_C}), blessed, tier_of, ALL_NODES
        ).certified


class TestPerExecutionSets:
    """A node maps to every hash it presented, not one."""

    def test_a_node_that_ran_twice_certifies_only_if_both_are_blessed(self):
        # A build that moved partway through a sweep gives one node two hashes;
        # that is the drift signal, not a defect.
        blessed = manifest({"strong": {FP_A}})
        assert certify(observed(critic={FP_A}), blessed, tier_of, ALL_NODES).certified
        drifted = certify(observed(critic={FP_A, FP_B}), blessed, tier_of, ALL_NODES)
        assert not drifted.certified
        assert [n.fingerprint for n in drifted.uncertified] == [FP_B]

    def test_an_empty_observation_set_is_illegal(self):
        # Absence is the sole encoding of "never ran" — never one of two
        # drifting synonyms.
        with pytest.raises(CertificationError, match="empty fingerprint set"):
            certify({"critic": frozenset()}, manifest({}), tier_of, ALL_NODES)


class TestTheThirdState:
    """Unexercised is a separate field, naming tiers."""

    def test_an_unexercised_tier_is_named_without_touching_certified(self):
        # Folding it into the boolean would mark an ordinary run untrusted,
        # which is how a gate teaches people to bypass it.
        result = certify(
            observed(extract={FP_A}), manifest({"base": {FP_A}}), tier_of, ALL_NODES
        )
        assert result.certified
        assert result.unexercised == ("strong",)
        assert not result.complete

    def test_a_fully_exercised_run_is_complete(self):
        result = certify(
            observed(extract={FP_A}, critic={FP_B}),
            manifest({"base": {FP_A}, "strong": {FP_B}}),
            tier_of,
            ALL_NODES,
        )
        assert result.certified
        assert result.unexercised == ()
        assert result.complete

    def test_the_expectation_comes_from_the_graph_not_the_manifest(self):
        # A manifest-derived expectation would be empty exactly on day one, and
        # would conflate a stale manifest with an unexercised one.
        result = certify(
            observed(extract={FP_A}), manifest({}), tier_of, ["extract", "critic"]
        )
        assert result.unexercised == ("strong",)


class TestReportFingerprints:
    def _report(self, nodes: list[NodeRun]) -> Report:
        return sample_report().model_copy(update={"nodes": nodes})

    def test_covers_llm_nodes_only(self):
        report = self._report(
            [
                NodeRun(
                    node="extract", model="m", sampling_fingerprint=FP_A, duration_ms=1
                ),
                NodeRun(node="validate", model=None, duration_ms=0),
            ]
        )
        assert report_fingerprints(report) == {"extract": frozenset({FP_A})}

    def test_a_node_appearing_twice_contributes_both_hashes(self):
        report = self._report(
            [
                NodeRun(
                    node="critic", model="m", sampling_fingerprint=FP_A, duration_ms=1
                ),
                NodeRun(
                    node="critic", model="m2", sampling_fingerprint=FP_B, duration_ms=1
                ),
            ]
        )
        assert report_fingerprints(report) == {"critic": frozenset({FP_A, FP_B})}


class TestManifestLoading:
    def test_the_shipped_manifest_loads_and_ships_empty(self, tmp_path):
        from pathlib import Path

        shipped = Path(__file__).parents[1] / "config" / "blessed-fingerprints.toml"
        loaded = load_manifest(shipped)
        assert loaded.version == MANIFEST_VERSION
        assert all(not prints for prints in loaded.tiers.values())
        assert loaded.blessed_for("base") == frozenset()

    def test_a_version_1_node_keyed_manifest_fails_closed(self, tmp_path):
        bad = tmp_path / "m.toml"
        bad.write_text("version = 1\n[nodes]\nextract = []\n", encoding="utf-8")
        with pytest.raises(CertificationError, match="unsupported version 1"):
            load_manifest(bad)

    def test_an_unknown_tier_key_is_rejected(self, tmp_path):
        bad = tmp_path / "m.toml"
        bad.write_text("version = 2\n[tiers]\nturbo = []\n", encoding="utf-8")
        with pytest.raises(CertificationError):
            load_manifest(bad)

    def test_a_non_hex_fingerprint_is_rejected(self, tmp_path):
        bad = tmp_path / "m.toml"
        bad.write_text('version = 2\n[tiers]\nbase = ["nope"]\n', encoding="utf-8")
        with pytest.raises(CertificationError):
            load_manifest(bad)

    def test_a_stray_top_level_key_is_rejected(self, tmp_path):
        bad = tmp_path / "m.toml"
        bad.write_text("version = 2\nbogus = 1\n[tiers]\n", encoding="utf-8")
        with pytest.raises(CertificationError):
            load_manifest(bad)

    def test_invalid_toml_is_rejected(self, tmp_path):
        bad = tmp_path / "m.toml"
        bad.write_text("version = = 2\n", encoding="utf-8")
        with pytest.raises(CertificationError, match="invalid TOML"):
            load_manifest(bad)

    def test_a_missing_file_is_rejected(self, tmp_path):
        # Unset-means-disabled was rejected: an absent file silently switching
        # off a provenance check is the failure this exists to prevent.
        with pytest.raises(CertificationError, match="cannot be read"):
            load_manifest(tmp_path / "does-not-exist.toml")


class TestGatePolicy:
    """The two withholding rules deliberately differ."""

    def gate(self, require_certified: bool) -> CertificationGate:
        return CertificationGate(
            manifest=manifest({"base": {FP_A}, "strong": {FP_B}}),
            tier_of=tier_of,
            require_certified=require_certified,
        )

    def test_uncertified_is_annotated_but_served_by_default(self):
        # The manifest ships empty; on by default this would fail every run on
        # day one and train people to switch it off.
        result = CertifyResult(certified=False)
        assert not self.gate(require_certified=False).withholds(result)

    def test_uncertified_withholds_when_the_knob_is_on(self):
        result = CertifyResult(certified=False)
        assert self.gate(require_certified=True).withholds(result)

    def test_unexercised_withholds_unconditionally(self):
        # An assertion, not a measurement: unreachable on any run producing a
        # report, so its cost is zero and one knob would make it inert.
        result = CertifyResult(certified=True, unexercised=("strong",))
        assert self.gate(require_certified=False).withholds(result)

    def test_a_clean_verdict_is_served(self):
        assert not self.gate(require_certified=True).withholds(
            CertifyResult(certified=True)
        )
