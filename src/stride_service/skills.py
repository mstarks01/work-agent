"""Skill composition for the STRIDE analysis nodes.

Implements the skills-as-SME design from wayfinder ticket 006: mechanical
assembly of the critic's category-boundary digest from the ``## Scope``
section of the six category skills, and composition of a node's skill text in
the stable-first order category -> shared rubric -> selected domain packs.

Loading itself lives in :mod:`stride_service.markdown_loader`, shared with
prompt loading (ticket 020). ``SkillLoader`` and the ``Skill*Error`` names
are aliases of it, kept because the skills tree, its lints and ticket 016
were written against them. The fixed section headings and token caps here are
enforced by the CI lint tests over ``skills/**/*.md``.
"""

from __future__ import annotations

from stride_service.markdown_loader import (
    MarkdownFormatError,
    MarkdownLoader,
    MarkdownNotFoundError,
    estimate_tokens,
    extract_section,
    split_sections,
)
from stride_service.report import STRIDE_CATEGORIES, StrideCategory

# Skill-flavored aliases of the one loader implementation.
SkillLoader = MarkdownLoader
SkillNotFoundError = MarkdownNotFoundError
SkillFormatError = MarkdownFormatError

__all__ = [
    "CATEGORY_SKILL_TOKEN_CAP",
    "DOMAIN_PACK_TOKEN_CAP",
    "SEVERITY_RUBRIC_NAME",
    "SEVERITY_RUBRIC_TOKEN_CAP",
    "SKILL_SECTION_HEADINGS",
    "STRIDE_CATEGORIES",
    "SkillFormatError",
    "SkillLoader",
    "SkillNotFoundError",
    "category_boundary_digest",
    "compose_analyst_skills",
    "compose_critic_skills",
    "estimate_tokens",
    "extract_section",
    "split_sections",
]

# The five fixed H2 sections of a category skill, in order. The lints enforce
# these exact strings; digest extraction depends on "Scope" being first.
SKILL_SECTION_HEADINGS: tuple[str, ...] = (
    "Scope",
    "Applicability",
    "Threat Patterns",
    "Guardrails",
    "Mitigations",
)

# Token caps per skill kind (ticket 006), checked in CI by the lint tests.
CATEGORY_SKILL_TOKEN_CAP = 3000
SEVERITY_RUBRIC_TOKEN_CAP = 1000
DOMAIN_PACK_TOKEN_CAP = 2000

SEVERITY_RUBRIC_NAME = "shared/severity_rubric"


def _category_title(category: StrideCategory) -> str:
    return category.replace("-", " ").title()


def category_boundary_digest(loader: SkillLoader) -> str:
    """The critic's lane digest: the six ``## Scope`` sections, verbatim.

    Assembled mechanically in canonical STRIDE order so the critic dedupes
    against the same lane definitions the analysts used.
    """
    parts = ["# STRIDE Category Boundaries"]
    for category in STRIDE_CATEGORIES:
        scope = extract_section(loader.load(f"stride/{category}"), "Scope")
        parts.append(f"## {_category_title(category)}\n\n{scope}")
    return "\n\n".join(parts) + "\n"


def compose_analyst_skills(
    loader: SkillLoader,
    category: StrideCategory,
    domain_packs: tuple[str, ...] = (),
) -> str:
    """One analyst's skill text: category skill, shared rubric, domain packs.

    Stable-first order (ticket 006) keeps the instruction prefix identical
    across jobs for the same category + pack selection, so it caches.
    """
    parts = [loader.load(f"stride/{category}"), loader.load(SEVERITY_RUBRIC_NAME)]
    parts.extend(loader.load(f"domains/{pack}") for pack in domain_packs)
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_critic_skills(loader: SkillLoader) -> str:
    """The critic's skill text: shared rubric plus the category-boundary digest.

    No threat catalogs, mitigations, or domain packs — verdicts anchor to
    System Model facts, not generative material.
    """
    parts = [loader.load(SEVERITY_RUBRIC_NAME), category_boundary_digest(loader)]
    return "\n\n".join(part.strip() for part in parts) + "\n"
