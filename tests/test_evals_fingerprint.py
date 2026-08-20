"""The fingerprint is the recogniser a vote hangs on, so it is pinned hard.

Three properties, each of which a vote ledger depends on and none of which is
visible from reading a hash: the value is stable across orderings and rewordings,
it is *not* stable across a real difference, and a version bump re-keys by
recomputation rather than by losing the components.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from evals.harness.fingerprint import (
    DEFAULT_VERSION,
    SUPPORTED_VERSIONS,
    VERSION_FOR,
    Components,
    FingerprintError,
    components_for,
    fingerprint,
    version_for,
    version_of,
)
from evals.harness.verbs import VerbError
from stride_service.frameworks import PACKAGES

FLOWS = {
    "flow:shopper-to-storefront-api:place-order": (
        "entity:shopper",
        "process:storefront-api",
    ),
}


def test_element_order_does_not_change_the_value():
    """``affected_element_ids`` is a list whose order no rule reads."""
    one = Components("stride", "spoofing", ("process:a", "store:b"), verb="read")
    other = Components("stride", "spoofing", ("store:b", "process:a"), verb="read")
    assert fingerprint(one) == fingerprint(other)


def test_a_flow_and_its_endpoints_fingerprint_alike():
    """The corpus carries one finding cited both ways; both must recognise."""
    as_flow = components_for(
        "stride",
        "spoofing",
        ["flow:shopper-to-storefront-api:place-order"],
        FLOWS,
        verb="replay",
    )
    as_endpoints = components_for(
        "stride",
        "spoofing",
        ["entity:shopper", "process:storefront-api"],
        FLOWS,
        verb="replay",
    )
    assert fingerprint(as_flow) == fingerprint(as_endpoints)


def test_a_trust_boundary_citation_is_dropped():
    """A zone is the context a claim sits in, not the thing it is about."""
    with_zone = components_for(
        "stride", "spoofing", ["process:a", "boundary:dmz"], FLOWS, verb="read"
    )
    without = components_for("stride", "spoofing", ["process:a"], FLOWS, verb="read")
    assert fingerprint(with_zone) == fingerprint(without)


def test_a_different_lane_target_or_framework_is_a_different_finding():
    base = Components("stride", "spoofing", ("process:a",), verb="read")
    assert fingerprint(base) != fingerprint(
        Components("stride", "tampering", ("process:a",), verb="read")
    )
    assert fingerprint(base) != fingerprint(
        Components("stride", "spoofing", ("process:b",), verb="read")
    )
    assert fingerprint(base) != fingerprint(
        Components("asvs", "spoofing", ("process:a",), verb="read")
    )
    assert fingerprint(base) != fingerprint(
        Components("stride", "spoofing", ("process:a",), verb="alter")
    ), "the verb is what version 2 adds; two actions are two findings"


def test_the_version_table_covers_every_package():
    """A table nobody compares to its registry fails as quietly as an ``if``."""
    assert set(VERSION_FOR) == set(PACKAGES)
    assert all(version in SUPPORTED_VERSIONS for version in VERSION_FOR.values())


def test_an_undeclared_framework_raises_rather_than_defaulting():
    """A package quietly keyed under the weaker rule is a ledger nobody can read."""
    with pytest.raises(FingerprintError, match="no fingerprint version"):
        version_for("nothing-declares-this")


def test_the_declared_versions_follow_from_what_a_claim_carries():
    """STRIDE composes an identity; ASVS's claims already carry one."""
    assert version_for("stride") == 2
    assert version_for("asvs") == 1


def test_version_one_ignores_the_verb_and_version_two_reads_it():
    """The whole reason a version rides in the value: two rules, two values."""
    read = Components("stride", "information-disclosure", ("store:a",), verb="read")
    alter = Components("stride", "information-disclosure", ("store:a",), verb="alter")

    assert fingerprint(read, version=1) == fingerprint(alter, version=1)
    assert fingerprint(read, version=2) != fingerprint(alter, version=2)


def test_the_version_is_readable_off_the_value():
    """A ledger holding two versions cannot silently compare across them."""
    value = fingerprint(Components("stride", "spoofing", ("process:a",)), version=1)
    assert value.startswith("v1:")
    assert version_of(value) == 1
    with pytest.raises(FingerprintError, match="is not a fingerprint"):
        version_of("deadbeef")


def test_version_two_without_a_verb_fails_closed():
    """Never a hash over a silent empty string, which would collide widely."""
    with pytest.raises(FingerprintError, match="carries none"):
        fingerprint(Components("stride", "spoofing", ("process:a",)), version=2)


def test_an_unknown_version_raises_rather_than_falling_back():
    with pytest.raises(FingerprintError, match="not one this build computes"):
        fingerprint(Components("stride", "spoofing", ("process:a",)), version=99)
    assert DEFAULT_VERSION in SUPPORTED_VERSIONS


def test_an_unknown_verb_fails_where_the_claim_is_built():
    with pytest.raises(VerbError):
        components_for("stride", "spoofing", ["process:a"], FLOWS, verb="exfiltrate")


def test_components_round_trip_so_a_version_bump_is_a_recompute():
    """The property the ledger rests on: re-keying needs no re-vote."""
    original = Components("stride", "spoofing", ("process:a", "store:b"), verb="read")
    restored = Components.from_json(original.to_json())

    assert restored == original
    assert fingerprint(restored, version=2) == fingerprint(original, version=2)


def test_malformed_components_are_refused_by_name():
    with pytest.raises(FingerprintError, match="malformed components"):
        Components.from_json({"framework": "stride", "lane": "spoofing"})
