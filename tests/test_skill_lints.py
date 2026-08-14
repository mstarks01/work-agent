"""CI lints over the real Markdown trees the graph composes instructions from.

Two roots, because the cutover split one. A lane skill and the severity rubric
are **STRIDE's**, under ``frameworks/stride/``, since a lane is a package's own
partition of its subject and the rubric grades that package's own record. A
domain pack is **shared**, under ``domains/``, since it describes a technology
rather than a framework and every carried framework's lanes may earn it. That
split is ADR 0011's rule — a document's home follows its retrieval key — and
these lints are where it is enforced against the tree rather than argued about.

The package gate (:func:`~stride_service.frameworks.validate_package`) already
refuses a package whose lanes are missing files or whose ``## Scope`` headings
have drifted. What is here is what the gate does not reach: the token caps, the
full heading set, and the shared root's own contract.
"""

from pathlib import Path

import pytest

from stride_service.domains import DETECTORS
from stride_service.frameworks import LANE_SECTION_HEADINGS
from stride_service.frameworks.stride import STRIDE
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES
from stride_service.markdown_loader import MarkdownLoader
from stride_service.skills import (
    DOMAIN_PACK_TOKEN_CAP,
    LANE_SKILL_TOKEN_CAP,
    SEVERITY_RUBRIC_TOKEN_CAP,
    estimate_tokens,
    lane_boundary_digest,
    lane_skill_doc,
    split_sections,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PROJECT_ROOT / "frameworks" / "stride"
DOMAINS_DIR = PROJECT_ROOT / "domains"

# A domain pack's three fixed H2 sections, in order. Fewer than a lane skill's
# five because a pack does not define a lane or a rating — it says when it
# applies, what to ask, and what it may not be used for.
PACK_SECTION_HEADINGS = ("When this applies", "What to look for", "Guardrails")

package_loader = MarkdownLoader(PACKAGE_DIR)
domain_loader = MarkdownLoader(DOMAINS_DIR)
domain_packs = sorted(domain_loader.names())


def test_lane_directories_match_the_packages_declared_lanes():
    """The tree and the declaration are one list, in one order."""
    dirs = sorted(path.name for path in (PACKAGE_DIR / "lanes").iterdir())
    assert dirs == sorted(STRIDE.lanes) == sorted(STRIDE_CATEGORIES)


@pytest.mark.parametrize("lane", STRIDE_CATEGORIES)
def test_lane_skill_has_exact_fixed_headings_in_order(lane):
    sections = split_sections(package_loader.load(lane_skill_doc(lane)))
    assert list(sections) == list(LANE_SECTION_HEADINGS)


@pytest.mark.parametrize("lane", STRIDE_CATEGORIES)
def test_lane_skill_sections_are_nonempty(lane):
    sections = split_sections(package_loader.load(lane_skill_doc(lane)))
    empty = [heading for heading, body in sections.items() if not body]
    assert not empty


@pytest.mark.parametrize("lane", STRIDE_CATEGORIES)
def test_lane_skill_within_token_cap(lane):
    tokens = estimate_tokens(package_loader.load(lane_skill_doc(lane)))
    assert tokens <= LANE_SKILL_TOKEN_CAP


def test_severity_rubric_within_token_cap():
    tokens = estimate_tokens(package_loader.load("severity_rubric"))
    assert tokens <= SEVERITY_RUBRIC_TOKEN_CAP


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_within_token_cap(pack):
    assert estimate_tokens(domain_loader.load(pack)) <= DOMAIN_PACK_TOKEN_CAP


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_has_the_three_fixed_headings_in_order(pack):
    assert list(split_sections(domain_loader.load(pack))) == list(PACK_SECTION_HEADINGS)


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_says_it_is_not_evidence(pack):
    """The one line a pack cannot be shipped without.

    A pack is generative security knowledge that arrives in the same request as
    the submitter's own words, and the failure it could cause — a finding
    grounded in what the pack said rather than in what the system is — is the
    one this service's whole provenance model exists to prevent. Wording is the
    author's; carrying the claim is not optional.
    """
    guardrails = split_sections(domain_loader.load(pack))["Guardrails"]
    assert "not evidence" in guardrails.lower()


def test_every_shipped_pack_can_be_selected():
    """A pack no detector names is a file nothing can ever load."""
    assert set(domain_packs) == set(DETECTORS)


def test_the_shared_domains_root_carries_nothing_but_packs():
    """No framework's text leaked back into the root every framework reads.

    The package's own root is covered by the gate, which refuses unread
    Markdown under it. This is the other direction, and it is the one the
    cutover could plausibly get wrong: ``domains/`` was carved out of the old
    ``skills/`` tree, and a lane skill left behind here would be loadable by
    every framework rather than by the one that wrote it.
    """
    entries = [path for path in DOMAINS_DIR.iterdir() if not path.name.startswith(".")]
    assert all(path.suffix == ".md" for path in entries)


# The critic's digest runs ~1-1.5K tokens and the shipped skills come in just
# under 1.6K. Guard against runaway Scope growth at 2K.
DIGEST_TOKEN_BUDGET = 2000


def test_boundary_digest_assembles_within_budget():
    digest = lane_boundary_digest(package_loader, STRIDE)
    assert estimate_tokens(digest) <= DIGEST_TOKEN_BUDGET


# There is no mechanically-pre-filtered element view: every lane agent receives
# the whole System Model, and ``## Applicability`` scopes only where a claim may
# be *filed*. A skill that claims its input was filtered is telling the model
# something false about what it is looking at.
@pytest.mark.parametrize("lane", STRIDE_CATEGORIES)
def test_applicability_does_not_claim_a_filtered_element_view(lane):
    sections = split_sections(package_loader.load(lane_skill_doc(lane)))
    assert "pre-filter" not in sections["Applicability"].lower()
