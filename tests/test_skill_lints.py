"""CI lints over the real ``skills/**/*.md`` tree.

Enforce the ticket-006 contract the runtime depends on: the six category
files exist under the exact ``StrideCategory`` names with the five fixed H2
headings in order (digest extraction targets these strings), and every file
respects its token cap.
"""

from pathlib import Path

import pytest

from stride_service.domains import DETECTORS
from stride_service.frameworks.stride.record import (
    STRIDE_CATEGORIES,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.skills import (
    DOMAIN_PACK_TOKEN_CAP,
    LANE_SECTION_HEADINGS,
    LANE_SKILL_TOKEN_CAP,
    SEVERITY_RUBRIC_TOKEN_CAP,
    estimate_tokens,
    lane_boundary_digest,
    split_sections,
)

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

# A domain pack's three fixed H2 sections, in order. Fewer than a category
# skill's five because a pack does not define a lane or a rating — it says when
# it applies, what to ask, and what it may not be used for.
PACK_SECTION_HEADINGS = ("When this applies", "What to look for", "Guardrails")

loader = MarkdownLoader(SKILLS_DIR)
domain_packs = sorted(name for name in loader.names() if name.startswith("domains/"))


def test_category_files_match_stride_categories_exactly():
    stems = sorted(path.stem for path in (SKILLS_DIR / "stride").glob("*.md"))
    assert stems == sorted(STRIDE_CATEGORIES)


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_category_file_has_exact_fixed_headings_in_order(category):
    sections = split_sections(loader.load(f"stride/{category}"))
    assert list(sections) == list(LANE_SECTION_HEADINGS)


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_category_file_sections_are_nonempty(category):
    sections = split_sections(loader.load(f"stride/{category}"))
    empty = [heading for heading, body in sections.items() if not body]
    assert not empty


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_category_file_within_token_cap(category):
    tokens = estimate_tokens(loader.load(f"stride/{category}"))
    assert tokens <= LANE_SKILL_TOKEN_CAP


def test_severity_rubric_within_token_cap():
    tokens = estimate_tokens(loader.load("shared/severity_rubric"))
    assert tokens <= SEVERITY_RUBRIC_TOKEN_CAP


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_within_token_cap(pack):
    assert estimate_tokens(loader.load(pack)) <= DOMAIN_PACK_TOKEN_CAP


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_has_the_three_fixed_headings_in_order(pack):
    assert list(split_sections(loader.load(pack))) == list(PACK_SECTION_HEADINGS)


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_says_it_is_not_evidence(pack):
    """The one line a pack cannot be shipped without.

    A pack is generative security knowledge that arrives in the same request as
    the submitter's own words, and the failure it could cause — a finding
    grounded in what the pack said rather than in what the system is — is the
    one this service's whole provenance model exists to prevent. Wording is the
    author's; carrying the claim is not optional.
    """
    guardrails = split_sections(loader.load(pack))["Guardrails"]
    assert "not evidence" in guardrails.lower()


def test_every_shipped_pack_can_be_selected():
    """A pack no detector names is a file nothing can ever load."""
    assert {name.removeprefix("domains/") for name in domain_packs} == set(DETECTORS)


def test_no_stray_skill_files():
    known_prefixes = ("stride/", "shared/", "domains/")
    stray = [name for name in loader.names() if not name.startswith(known_prefixes)]
    assert not stray


# The critic's digest runs ~1-1.5K tokens and the shipped skills come in just
# under 1.6K. Guard against runaway Scope growth at 2K.
DIGEST_TOKEN_BUDGET = 2000


def test_boundary_digest_assembles_within_budget():
    digest = lane_boundary_digest(loader)
    assert estimate_tokens(digest) <= DIGEST_TOKEN_BUDGET


# There is no mechanically-pre-filtered element view: every category agent receives
# whole System Model, and ``## Applicability`` scopes only where a threat may be
# *filed*. A skill that claims its input was filtered is telling the model
# something false about what it is looking at.
@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_applicability_does_not_claim_a_filtered_element_view(category):
    applicability = split_sections(loader.load(f"stride/{category}"))["Applicability"]
    assert "pre-filter" not in applicability.lower()
