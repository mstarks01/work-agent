"""Prompt composition for the four LLM node kinds.

Prompt content lives in ``prompts/`` as Markdown (ticket 019) and loads
through the same :class:`~stride_service.markdown_loader.MarkdownLoader` the
skills use — a skill is *what to know*, a prompt is *what to do with this
job's input* (ticket 013). Composition here is concatenation only: the
``{category}``, ``{system_model}``, ``{boundary_crossings}``,
``{draft_threats}``, ``{input_text}``, ``{previous_model}`` and
``{validation_issues}`` placeholders stay untouched for ADK state templating
to fill at run time.

Order is stable-first, mirroring
:func:`~stride_service.skills.compose_analyst_skills`: the one shared
``analyst.md`` body precedes the per-category exemplar file, so the six
analysts share the longest possible cacheable prefix. The token caps here are
enforced by ``tests/test_prompt_lints.py``.
"""

from __future__ import annotations

from stride_service.markdown_loader import MarkdownLoader
from stride_service.report import StrideCategory

# The four fixed H2 sections of an agent prompt, in order (ticket 013). The
# lints enforce these exact strings.
PROMPT_SECTION_HEADINGS: tuple[str, ...] = ("Role", "Input", "Procedure", "Output")

# The four prompt bodies, by node kind.
ANALYST_PROMPT_NAME = "analyst"
CRITIC_PROMPT_NAME = "critic"
EXTRACT_PROMPT_NAME = "extract"
REPAIR_PROMPT_NAME = "repair"
PROMPT_BODY_NAMES: tuple[str, ...] = (
    EXTRACT_PROMPT_NAME,
    REPAIR_PROMPT_NAME,
    ANALYST_PROMPT_NAME,
    CRITIC_PROMPT_NAME,
)

EXEMPLARS_PREFIX = "exemplars/"

# Token caps per prompt file (ticket 019), checked in CI by the lint tests.
ANALYST_PROMPT_TOKEN_CAP = 2000
EXEMPLAR_TOKEN_CAP = 1500
CRITIC_PROMPT_TOKEN_CAP = 1500
EXTRACT_PROMPT_TOKEN_CAP = 1500
REPAIR_PROMPT_TOKEN_CAP = 800


def exemplar_name(category: StrideCategory) -> str:
    """The loader name of one category's exemplar file."""
    return f"{EXEMPLARS_PREFIX}{category}"


def compose_analyst_prompt(loader: MarkdownLoader, category: StrideCategory) -> str:
    """One analyst's prompt: the shared body, then that category's exemplars.

    ``{category}`` is left in place — one templated body serves all six
    analysts rather than six near-identical copies.
    """
    parts = [loader.load(ANALYST_PROMPT_NAME), loader.load(exemplar_name(category))]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_critic_prompt(loader: MarkdownLoader) -> str:
    """The critic's prompt: the five judgement steps over all six analysts' drafts.

    No exemplars — the critic rules on drafts it is given rather than
    producing new ones, and the mechanical checks it must not re-perform run
    in :mod:`stride_service.critic`.
    """
    return loader.load(CRITIC_PROMPT_NAME).strip() + "\n"


def compose_extract_prompt(loader: MarkdownLoader) -> str:
    """The extraction prompt: semi-structured input text to a System Model."""
    return loader.load(EXTRACT_PROMPT_NAME).strip() + "\n"


def compose_repair_prompt(loader: MarkdownLoader) -> str:
    """The one-shot repair prompt: validator issues plus the original input."""
    return loader.load(REPAIR_PROMPT_NAME).strip() + "\n"
