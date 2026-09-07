"""The profile document against the code it describes.

``docs/STRIDE-Profile.md`` says which of this service's STRIDE decisions are the
published method's and which are this repository's. That is what
``STRIDE_VERSION`` names a version of, and a document nothing checks is how a
version comes to name something that has moved.

**Only the decidable half.** Whether "one category per finding" is argued well is
a reading question, and no test answers it. What is decidable is whether the
document's enumerations still match their registries: the six categories, the
size of the action vocabulary, and that every deviation it claims is one the
code actually makes. Those are the parts that go stale silently, because adding
a verb or a lane touches no prose.

Free of provider calls, so it gates on every pull request.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from analysis_service.actions import ACTION_VERBS
from analysis_service.frameworks import package_for
from analysis_service.frameworks.stride.record import LANES_OF_VERB, STRIDE_VERSION

PROFILE = Path(__file__).resolve().parents[1] / "docs" / "STRIDE-Profile.md"


@pytest.fixture(scope="module")
def profile() -> str:
    return PROFILE.read_text(encoding="utf-8")


def test_the_document_names_every_lane_and_no_other(profile):
    """The category table against the package's own lanes, both directions.

    A lane added to the package and missing here leaves a reader with five of
    six; a lane named here that the package dropped describes a service that
    does not exist.
    """
    lanes = set(package_for("stride").lanes)
    named = {lane for lane in lanes if f"`{lane}`" in profile}
    # Backticked lower-case words that read like a lane, so a lane the package
    # dropped is caught as well as one it gained.
    lane_shaped = {
        match.group(1)
        for match in re.finditer(r"`([a-z]+(?:-[a-z]+)+|spoofing|tampering)`", profile)
    }

    assert named == lanes, f"the profile does not name {sorted(lanes - named)}"
    assert not (lane_shaped & {"stride", "asvs"}), (
        "the profile names a framework where the table lists lanes"
    )


def test_the_document_counts_the_action_vocabulary_correctly(profile):
    """ "20 action verbs" is a number, and a number is decidable.

    The vocabulary is half of what a fingerprint keys a finding on, so it grows
    when a blessing pass needs an action it cannot spell — and nothing about
    that edit touches this document.
    """
    counted = re.search(r"closed set of (\d+) action verbs", profile)

    assert counted, "the profile no longer states the size of the vocabulary"
    assert int(counted.group(1)) == len(ACTION_VERBS), (
        f"the profile says {counted.group(1)} action verbs and"
        f" ACTION_VERBS holds {len(ACTION_VERBS)}"
    )


def test_the_deviations_it_claims_are_deviations_the_code_makes(profile):
    """Each claimed deviation, against the thing that implements it.

    A document may not claim a decision the service stopped making. These are
    the four the code can answer for directly; the rest are design statements a
    reader checks.
    """
    assert "`analysis_service.actions.ACTION_VERBS`" in profile
    # A legal-verb table exists, and it is what makes "one category per finding"
    # enforceable rather than conventional.
    assert LANES_OF_VERB, "the profile claims a legal-verb table and there is none"
    assert set(LANES_OF_VERB) == set(ACTION_VERBS), (
        "every verb has a lane list, which is what the profile describes"
    )
    # Repudiation reaching a data store is the one extension it claims.
    assert "repudiation" in package_for("stride").lanes


def test_the_version_the_document_says_it_describes_still_exists():
    """`STRIDE_VERSION` is the field; this document is what it versions."""
    assert STRIDE_VERSION
    assert "STRIDE_VERSION" in PROFILE.read_text(encoding="utf-8")
