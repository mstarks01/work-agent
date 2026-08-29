"""Skill composition: the static text an LLM node is given, per package.

A skill is subject-matter expertise a node is given. Under **Framework
Packages** every piece of it belongs to one package and is composed from that
package's own text root: a lane's skill, the lane-boundary digest its critic
dedupes against, the critic text saying what that framework's verdict states
assert, and the severity rubric — which exists exactly when the package's record
grades harm.

**One loader per package.** A :class:`~analysis_service.markdown_loader.
MarkdownLoader` rooted at ``frameworks/<name>/`` is what every function here
reads, so a deployment that redirects ``ANALYSIS_FRAMEWORKS_DIR`` redirects the
whole of a package's text and none of another's.

Domain packs compose separately (:func:`compose_domain_skills`) from the one
shared ``domains/`` root, because they arrive at a different time and belong to
nobody in particular. Which packs a job earns is a fact about *that job's*
System Model (:mod:`analysis_service.domains`), which #162 ruled one extraction
fills for every framework, so their key is neutral by construction and the
graph is built once at startup — pack text cannot sit in the instruction the way
a lane skill does. It rides in the job-varying block instead — see
:func:`~analysis_service.graph.prepare_analysis` — which is also what keeps the
cacheable prefix intact: everything before the first templated placeholder is
identical across jobs, and the packs sit after it.

**A shared pack may not name any package's lane.** A pack states a technology's
facts, its failure modes and the questions to ask; a sentence assigning those to
a STRIDE category is a sentence that is false in another framework's prompt, and
asking a model to disregard it is worse than not sending it. The lint over
``domains/*.md`` derives its word list from the registered packages' own
``lanes`` members rather than from a hand-maintained list.

Loading itself lives in :mod:`analysis_service.markdown_loader`, shared with
prompt loading. The fixed section headings are checked by the package gate
(:func:`~analysis_service.frameworks.validate_package`) because the code reads
them; the token caps stay CI lints, because a cap is a drift alarm rather
than a thing the service reads. They live in :mod:`analysis_service.token_caps`.
"""

from __future__ import annotations

from collections.abc import Sequence

from analysis_service.frameworks import (
    CRITIC_DOC,
    LANE_SECTION_HEADINGS,
    SEVERITY_RUBRIC_DOC,
    FrameworkPackage,
)
from analysis_service.markdown_loader import (
    MarkdownLoader,
    estimate_tokens,
    extract_section,
    split_sections,
)

__all__ = [
    "LANE_SECTION_HEADINGS",
    "compose_critic_skills",
    "compose_domain_skills",
    "compose_lane_skills",
    "estimate_tokens",
    "extract_section",
    "lane_boundary_digest",
    "lane_skill_doc",
    "split_sections",
]


def lane_skill_doc(lane: str) -> str:
    """The loader name of one lane's skill, under a package's own root."""
    return f"lanes/{lane}/skill"


def lane_exemplars_doc(lane: str) -> str:
    """The loader name of one lane's worked drafts, under a package's own root."""
    return f"lanes/{lane}/exemplars"


def _lane_title(lane: str) -> str:
    return lane.replace("-", " ").title()


def lane_boundary_digest(loader: MarkdownLoader, package: FrameworkPackage) -> str:
    """One package's lane digest: its lanes' ``## Scope`` sections, verbatim.

    Assembled mechanically in the package's own declared lane order, so a
    framework's critic dedupes against the same lane definitions its own lane
    agents used — and against no other framework's, which is what makes a
    cross-framework merge unavailable rather than merely discouraged.
    """
    parts = [f"# {package.name.upper()} Lane Boundaries"]
    for lane in package.lanes:
        scope = extract_section(loader.load(lane_skill_doc(lane)), "Scope")
        parts.append(f"## {_lane_title(lane)}\n\n{scope}")
    return "\n\n".join(parts) + "\n"


def compose_lane_skills(
    loader: MarkdownLoader, package: FrameworkPackage, lane: str
) -> str:
    """One lane agent's static skill text: the lane skill, then the rubric.

    Both halves are the same for every job in this lane, so the whole of it
    caches. The rubric is present exactly when this package's record carries a
    ``severity`` field: a package that grades nothing composes a lane skill
    alone, and the gate has already refused a package that ships a rubric
    nothing would read.
    """
    parts = [loader.load(lane_skill_doc(lane))]
    if package.carries_severity():
        parts.append(loader.load(SEVERITY_RUBRIC_DOC))
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_critic_skills(loader: MarkdownLoader, package: FrameworkPackage) -> str:
    """One package's critic skill text: rubric, critic text, lane digest.

    No threat catalogs, mitigations, or domain packs — verdicts anchor to
    System Model facts, not generative material.

    The package's own ``critic.md`` is what makes the three verdict states mean
    this framework's question rather than another's. The service keeps the
    states, the field rules and the review seam; the package says what
    ``confirmed`` asserts.
    """
    parts = []
    if package.carries_severity():
        parts.append(loader.load(SEVERITY_RUBRIC_DOC))
    parts.append(loader.load(CRITIC_DOC))
    parts.append(lane_boundary_digest(loader, package))
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_domain_skills(loader: MarkdownLoader, packs: Sequence[str]) -> str:
    """The selected domain packs' text, in selection order, or ``""`` for none.

    ``loader`` is rooted at the shared ``domains/`` root rather than at any
    package's, because a pack is selected from the **Valid System Model**'s own
    technology fields and belongs to no framework.

    The empty string is what a job earning no pack renders, and it is
    deliberately empty rather than a "no packs selected" note: the prompt
    reads the block as optional reference material, and a sentence saying
    there is none is a sentence about nothing.
    """
    if not packs:
        return ""
    return "\n\n".join(loader.load(pack).strip() for pack in packs) + "\n"
