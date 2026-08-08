"""Skill composition for the STRIDE analysis nodes.

A skill is subject-matter expertise a node is given: mechanical assembly of the
critic's category-boundary digest from the ``## Scope`` section of the six
category skills, and composition of a node's skill text as the category skill
followed by the shared rubric.

Domain packs compose separately (:func:`compose_domain_skills`) because they
arrive at a different time. Which packs a job earns is a fact about *that job's*
System Model (:mod:`stride_service.domains`), and the graph is built once at
startup, so pack text cannot sit in the instruction the way a category skill
does. It rides in the job-varying block instead — see
:func:`~stride_service.graph.prepare_analysis` — which is also what keeps the
cacheable prefix intact: everything before the first templated placeholder is
identical across jobs, and the packs sit after it.

Loading itself lives in :mod:`stride_service.markdown_loader`, shared with
prompt loading. The fixed section headings and token caps here are enforced by
the CI lint tests over ``skills/**/*.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

from stride_service.markdown_loader import (
    MarkdownLoader,
    estimate_tokens,
    extract_section,
    split_sections,
)
from stride_service.report import STRIDE_CATEGORIES, StrideCategory

__all__ = [
    "CATEGORY_SKILL_TOKEN_CAP",
    "DOMAIN_PACK_TOKEN_CAP",
    "SEVERITY_RUBRIC_NAME",
    "SEVERITY_RUBRIC_TOKEN_CAP",
    "SKILL_SECTION_HEADINGS",
    "STRIDE_CATEGORIES",
    "category_boundary_digest",
    "compose_analyze_skills",
    "compose_critic_skills",
    "compose_domain_skills",
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

# Token caps per skill kind, checked in CI by the lint tests.
CATEGORY_SKILL_TOKEN_CAP = 3000
SEVERITY_RUBRIC_TOKEN_CAP = 1000
DOMAIN_PACK_TOKEN_CAP = 2000

SEVERITY_RUBRIC_NAME = "shared/severity_rubric"


def _category_title(category: StrideCategory) -> str:
    return category.replace("-", " ").title()


def category_boundary_digest(loader: MarkdownLoader) -> str:
    """The critic's lane digest: the six ``## Scope`` sections, verbatim.

    Assembled mechanically in canonical STRIDE order so the critic dedupes
    against the same lane definitions the category agents used.
    """
    parts = ["# STRIDE Category Boundaries"]
    for category in STRIDE_CATEGORIES:
        scope = extract_section(loader.load(f"stride/{category}"), "Scope")
        parts.append(f"## {_category_title(category)}\n\n{scope}")
    return "\n\n".join(parts) + "\n"


def compose_analyze_skills(loader: MarkdownLoader, category: StrideCategory) -> str:
    """One category agent's static skill text: category skill, shared rubric.

    Both halves are the same for every job in this category, so the whole of
    it caches.
    """
    parts = [loader.load(f"stride/{category}"), loader.load(SEVERITY_RUBRIC_NAME)]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_domain_skills(loader: MarkdownLoader, packs: Sequence[str]) -> str:
    """The selected domain packs' text, in selection order, or ``""`` for none.

    The empty string is what a job earning no pack renders, and it is
    deliberately empty rather than a "no packs selected" note: the prompt
    reads the block as optional reference material, and a sentence saying
    there is none is a sentence about nothing.
    """
    if not packs:
        return ""
    return "\n\n".join(loader.load(f"domains/{pack}").strip() for pack in packs) + "\n"


def compose_critic_skills(loader: MarkdownLoader) -> str:
    """The critic's skill text: shared rubric plus the category-boundary digest.

    No threat catalogs, mitigations, or domain packs — verdicts anchor to
    System Model facts, not generative material.
    """
    parts = [loader.load(SEVERITY_RUBRIC_NAME), category_boundary_digest(loader)]
    return "\n\n".join(part.strip() for part in parts) + "\n"
