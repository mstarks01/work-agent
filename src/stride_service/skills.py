"""Skill loading and composition for the STRIDE analysis nodes.

Implements the skills-as-SME design from wayfinder ticket 006: a
:class:`SkillLoader` reading a directory of Markdown files, mechanical
assembly of the critic's category-boundary digest from the ``## Scope``
section of the six category skills, and composition of a node's skill text in
the stable-first order category -> shared rubric -> selected domain packs.

Skill files are trusted repo content baked into the image, but loading still
fails closed: a missing skill, a heading that deviates from the fixed set, or
a name escaping the skills root raises instead of degrading silently — a
skill that silently drops out of an analyst's context is a recall loss no one
would notice. The fixed section headings and token caps here are enforced by
the CI lint tests over ``skills/**/*.md``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import get_args

from stride_service.report import StrideCategory

STRIDE_CATEGORIES: tuple[StrideCategory, ...] = get_args(StrideCategory)

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


class SkillNotFoundError(LookupError):
    """No skill file exists for the requested name."""


class SkillFormatError(ValueError):
    """A skill file deviates from the fixed section structure."""


def estimate_tokens(text: str) -> int:
    """Coarse token estimate (words x 4/3), the convention the caps assume."""
    return math.ceil(len(text.split()) * 4 / 3)


def split_sections(text: str) -> dict[str, str]:
    """Split a skill file into its H2 sections, preserving order.

    Headings are taken verbatim (everything after ``## ``) so the lints can
    enforce exact strings. Duplicate or empty headings raise
    :class:`SkillFormatError`.
    """
    sections: dict[str, str] = {}
    heading: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(body).strip()
            heading = line[3:]
            if not heading.strip():
                raise SkillFormatError("empty H2 heading")
            if heading in sections:
                raise SkillFormatError(f"duplicate section '## {heading}'")
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        sections[heading] = "\n".join(body).strip()
    return sections


def extract_section(text: str, heading: str) -> str:
    """The body of one named H2 section; missing or empty is a format error."""
    sections = split_sections(text)
    if heading not in sections:
        raise SkillFormatError(f"missing section '## {heading}'")
    if not sections[heading]:
        raise SkillFormatError(f"section '## {heading}' is empty")
    return sections[heading]


class SkillLoader:
    """Loads skills from a directory of Markdown files.

    Names are root-relative POSIX paths without the ``.md`` suffix, e.g.
    ``"stride/spoofing"`` or ``"shared/severity_rubric"``. This is the
    canonical directory-in, named-items-out interface for the service (ticket
    011: no external PromptLoader was available to mirror); prompt loading
    follows the same shape.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise FileNotFoundError(f"skills root is not a directory: {root}")

    @property
    def root(self) -> Path:
        return self._root

    def names(self) -> list[str]:
        """All loadable skill names, sorted."""
        return sorted(
            path.relative_to(self._root).with_suffix("").as_posix()
            for path in self._root.rglob("*.md")
        )

    def load(self, name: str) -> str:
        path = (self._root / f"{name}.md").resolve()
        # A name resolving outside the root (traversal) is treated the same
        # as absent — deny, don't reveal what lies outside.
        if not path.is_relative_to(self._root) or not path.is_file():
            raise SkillNotFoundError(f"no skill named {name!r} under {self._root}")
        return path.read_text(encoding="utf-8")


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
