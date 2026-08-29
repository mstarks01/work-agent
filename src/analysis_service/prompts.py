"""Prompt composition for the five LLM node kinds.

Prompt content lives in ``prompts/`` as Markdown and loads through the same
:class:`~analysis_service.markdown_loader.MarkdownLoader` the skills use — a
skill is *what to know*, a prompt is *what to do with this job's input*.
Composition here is concatenation only: the ``{lane}``, ``{system_model}``,
``{boundary_crossings}``, ``{evidence_catalog}``, ``{candidates}``,
``{domain_skills}``, ``{drafts}``, ``{input_text}``, ``{previous_model}`` and
``{validation_issues}`` placeholders stay untouched for ADK state templating to
fill at run time.

**The five bodies are the service's and are framework-neutral.** A prompt says
what to do with this job's input, and every registered framework's lane agent
does the same thing with it: read the model, work the leads, cite from the
catalog, emit an object holding ``claims``. What differs is *what to look for*,
and that is a skill — the lane skill, the exemplars, the critic text — which is
package text under ``frameworks/<name>/`` and is composed by
:mod:`analysis_service.skills`. Two frameworks reading two copies of ``analyze.md``
would be two places for the output contract to drift.

Two blocks are the exception, and both are package text. The **output contract**
(``output.md``) says what one claim is and which fields carry it, which a record
that grades nothing cannot share with one that does. The **exemplars**
(``lanes/<lane>/exemplars.md``) are worked drafts in that record's own shape.
:func:`compose_analyze_prompt` takes the package's loader for exactly those two.

Order is stable-first: the one shared ``analyze.md`` body, then the package's
output contract, then the per-lane exemplar file, so a framework's lane agents
share the longest possible cacheable prefix. ``tests/test_prompt_lints.py``
enforces the token caps in :mod:`analysis_service.token_caps` over this text.
"""

from __future__ import annotations

from analysis_service.frameworks import OUTPUT_DOC
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.skills import lane_exemplars_doc

# The four fixed H2 sections of an agent prompt, in order. The lints enforce
# these exact strings.
PROMPT_SECTION_HEADINGS: tuple[str, ...] = ("Role", "Input", "Procedure", "Output")

# The prompt bodies, by node kind.
ANALYZE_PROMPT_NAME = "analyze"
CRITIC_PROMPT_NAME = "critic"
RECRITIC_PROMPT_NAME = "recritic"
EXTRACT_PROMPT_NAME = "extract"
REPAIR_PROMPT_NAME = "repair"
PROMPT_BODY_NAMES: tuple[str, ...] = (
    EXTRACT_PROMPT_NAME,
    REPAIR_PROMPT_NAME,
    ANALYZE_PROMPT_NAME,
    CRITIC_PROMPT_NAME,
    RECRITIC_PROMPT_NAME,
)

# The token caps over these bodies are drift alarms rather than a budget, and
# they live in one table with every other one: ``analysis_service.token_caps``.
# ADR 0016 says why they stopped being an argued number per file.


def compose_analyze_prompt(
    prompt_loader: MarkdownLoader, package_loader: MarkdownLoader, lane: str
) -> str:
    """One lane agent's prompt: the shared body, the package's output contract,
    then that lane's own exemplars.

    Two loaders because the parts have two owners. ``analyze.md`` is the
    service's, rooted at ``prompts/``, and one templated body serves every lane
    of every registered framework rather than N near-identical copies. The other
    two are the *package's*, rooted at ``frameworks/<name>/``: ``output.md``
    says what one claim is and which fields carry it, and
    ``lanes/<lane>/exemplars.md`` works drafts in that framework's own record
    shape. Both would be a lie in another framework's prompt.

    **Stable-first, so the cacheable prefix is as long as it can be.** The shared
    body is identical across every framework, the output contract across every
    lane of one framework, and only the exemplars are per lane.

    ``{lane}`` is left in place for ADK to template.
    """
    parts = [
        prompt_loader.load(ANALYZE_PROMPT_NAME),
        package_loader.load(OUTPUT_DOC),
        package_loader.load(lane_exemplars_doc(lane)),
    ]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_critic_prompt(loader: MarkdownLoader) -> str:
    """The critic's prompt: the judgement steps over one framework's drafts.

    No exemplars — the critic rules on drafts it is given rather than
    producing new ones, and the mechanical checks it must not re-perform run
    in :mod:`analysis_service.critic`.

    What this framework's verdicts *assert* is not here: that is the package's
    own ``critic.md``, composed into the node's skills by
    :func:`~analysis_service.skills.compose_critic_skills`.
    """
    return loader.load(CRITIC_PROMPT_NAME).strip() + "\n"


def compose_recritic_prompt(loader: MarkdownLoader) -> str:
    """The critic re-ask prompt: a bounded reconciliation of the critic's output.

    No exemplars, like the critic — it re-rules the drafts it was given
    against the mechanical problems in its previous output, and the checks it
    must satisfy run in :mod:`analysis_service.critic`.
    """
    return loader.load(RECRITIC_PROMPT_NAME).strip() + "\n"


def compose_extract_prompt(loader: MarkdownLoader) -> str:
    """The extraction prompt: semi-structured input text to a System Model."""
    return loader.load(EXTRACT_PROMPT_NAME).strip() + "\n"


def compose_repair_prompt(loader: MarkdownLoader) -> str:
    """The one-shot repair prompt: validator issues plus the original input."""
    return loader.load(REPAIR_PROMPT_NAME).strip() + "\n"
