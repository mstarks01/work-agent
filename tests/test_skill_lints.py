"""CI lints over the real ``skills/**/*.md`` tree.

Enforce the ticket-006 contract the runtime depends on: the six category
files exist under the exact ``StrideCategory`` names with the five fixed H2
headings in order (digest extraction targets these strings), and every file
respects its token cap.
"""

from pathlib import Path

import pytest

from stride_service.markdown_loader import MarkdownLoader
from stride_service.skills import (
    CATEGORY_SKILL_TOKEN_CAP,
    DOMAIN_PACK_TOKEN_CAP,
    SEVERITY_RUBRIC_TOKEN_CAP,
    SKILL_SECTION_HEADINGS,
    STRIDE_CATEGORIES,
    category_boundary_digest,
    estimate_tokens,
    split_sections,
)

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

loader = MarkdownLoader(SKILLS_DIR)
domain_packs = sorted(
    name for name in loader.names() if name.startswith("domains/")
)


def test_category_files_match_stride_categories_exactly():
    stems = sorted(path.stem for path in (SKILLS_DIR / "stride").glob("*.md"))
    assert stems == sorted(STRIDE_CATEGORIES)


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_category_file_has_exact_fixed_headings_in_order(category):
    sections = split_sections(loader.load(f"stride/{category}"))
    assert list(sections) == list(SKILL_SECTION_HEADINGS)


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_category_file_sections_are_nonempty(category):
    sections = split_sections(loader.load(f"stride/{category}"))
    empty = [heading for heading, body in sections.items() if not body]
    assert not empty


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_category_file_within_token_cap(category):
    tokens = estimate_tokens(loader.load(f"stride/{category}"))
    assert tokens <= CATEGORY_SKILL_TOKEN_CAP


def test_severity_rubric_within_token_cap():
    tokens = estimate_tokens(loader.load("shared/severity_rubric"))
    assert tokens <= SEVERITY_RUBRIC_TOKEN_CAP


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_within_token_cap(pack):
    assert estimate_tokens(loader.load(pack)) <= DOMAIN_PACK_TOKEN_CAP


def test_no_stray_skill_files():
    known_prefixes = ("stride/", "shared/", "domains/")
    stray = [
        name for name in loader.names() if not name.startswith(known_prefixes)
    ]
    assert not stray


# The critic's digest runs ~1-1.5K tokens and the shipped skills come in just
# under 1.6K. Guard against runaway Scope growth at 2K.
DIGEST_TOKEN_BUDGET = 2000


def test_boundary_digest_assembles_within_budget():
    digest = category_boundary_digest(loader)
    assert estimate_tokens(digest) <= DIGEST_TOKEN_BUDGET


# There is no mechanically-pre-filtered element view: every category agent receives
# whole System Model, and ``## Applicability`` scopes only where a threat may be
# *filed*. A skill that claims its input was filtered is telling the model
# something false about what it is looking at.
@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_applicability_does_not_claim_a_filtered_element_view(category):
    applicability = split_sections(loader.load(f"stride/{category}"))["Applicability"]
    assert "pre-filter" not in applicability.lower()
