"""Prompt composition for the four LLM node kinds.

Prompt content lives in ``prompts/`` as Markdown and loads through the same
:class:`~stride_service.markdown_loader.MarkdownLoader` the skills use — a
skill is *what to know*, a prompt is *what to do with this job's input*.
Composition here is concatenation only: the ``{category}``, ``{system_model}``,
``{boundary_crossings}``, ``{evidence_catalog}``, ``{candidates}``,
``{domain_skills}``, ``{draft_threats}``, ``{input_text}``,
``{previous_model}`` and ``{validation_issues}`` placeholders stay untouched
for ADK state templating to fill at run time.

Order is stable-first, mirroring
:func:`~stride_service.skills.compose_analyze_skills`: the one shared
``analyze.md`` body precedes the per-category exemplar file, so the six
category agents share the longest possible cacheable prefix. The token caps
here are enforced by ``tests/test_prompt_lints.py``.
"""

from __future__ import annotations

from stride_service.markdown_loader import MarkdownLoader
from stride_service.report import StrideCategory

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

EXEMPLARS_PREFIX = "exemplars/"

# Token caps per prompt file, checked in CI by the lint tests.
#
# Raised from 2000 with the finding-attribution cutover, to
# ``EXTRACT_PROMPT_TOKEN_CAP`` exactly: the two had differed precisely *because*
# ``analyze.md`` did not read submitter text, and once it carried the same
# ``## Input`` responsibility ``extract.md`` does — fenced submitter sources and
# the data-not-instruction paragraph alike — equalizing them followed from the
# change rather than conceding to it.
#
# Raised again, off that parity, when the static/variable split was tightened
# for caching. Parity was the wrong rule and the reorganization is what exposed
# it: ``analyze.md`` carries everything ``extract.md`` does **plus** the shared
# exemplar system — a fixed reference model, its rendered source, and its
# element and flow tables — which is ~520 tokens with no analogue in any other
# prompt. Two files doing different amounts of work do not belong on one
# number, so the equality is dropped rather than nudged.
#
# A cap is a budget, not an entitlement: sized at the full allowance it would
# sit above the file and catch no drift at all, so it is sized to leave ~90
# tokens — room for a normal edit without a CI fight, and little enough that a
# second exemplar system still has to be argued for.
#
# THREE DERIVED BLOCKS NOW SHARE THIS BUDGET, and they arrived separately, so
# the number is stated once here rather than nudged by each:
#
# * the **evidence catalog**, which an agent selects references out of rather
#   than constructing grounds — an exemplar system without one would
#   demonstrate IDs arriving from nowhere, the exact habit a closed set exists
#   to break;
# * **candidates**, the leads each lane's rules fire on, plus the paragraph
#   fixing their standing and the Procedure step that works them;
# * **domain packs**, whose block is a name and a sentence here because the
#   pack text itself rides in the skills, not in this file.
#
# ~180 tokens for the candidate half and ~130 for the catalog, on a body that
# was 2239 before either. A fourth derived block should be argued for against
# what an agent can actually hold in mind, not against this number.
ANALYZE_PROMPT_TOKEN_CAP = 2950
EXEMPLAR_TOKEN_CAP = 1500
CRITIC_PROMPT_TOKEN_CAP = 1500
# The re-ask has to be able to name and fix every fault `review_issues` can
# report, so this cap moves when that set does — it grew with the three verdict
# shape rules, which are re-askable rather than fatal and so need a procedure
# step. 1100 leaves ~100 tokens; a fault class costs roughly a third of that,
# which is the right amount of friction for adding one.
RECRITIC_PROMPT_TOKEN_CAP = 1100
# Raised from 1500 with the sources cutover (#53, #56). The original was sized
# against the *category agent's* 6-8K envelope, but extract loads no skills, so this
# file is the whole instruction — around 5% of a full-budget call. Buying room
# for the seven reading rules and their worked examples is cheaper than
# deleting the only worked examples the prompt has.
EXTRACT_PROMPT_TOKEN_CAP = 2200
REPAIR_PROMPT_TOKEN_CAP = 800


def exemplar_name(category: StrideCategory) -> str:
    """The loader name of one category's exemplar file."""
    return f"{EXEMPLARS_PREFIX}{category}"


def compose_analyze_prompt(loader: MarkdownLoader, category: StrideCategory) -> str:
    """One category agent's prompt: the shared body, then that category's exemplars.

    ``{category}`` is left in place — one templated body serves all six agents
    rather than six near-identical copies.
    """
    parts = [loader.load(ANALYZE_PROMPT_NAME), loader.load(exemplar_name(category))]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_critic_prompt(loader: MarkdownLoader) -> str:
    """The critic's prompt: the five judgement steps over all six agents' drafts.

    No exemplars — the critic rules on drafts it is given rather than
    producing new ones, and the mechanical checks it must not re-perform run
    in :mod:`stride_service.critic`.
    """
    return loader.load(CRITIC_PROMPT_NAME).strip() + "\n"


def compose_recritic_prompt(loader: MarkdownLoader) -> str:
    """The critic re-ask prompt: a bounded reconciliation of the critic's output.

    No exemplars, like the critic — it re-rules the drafts it was given
    against the mechanical problems in its previous output, and the checks it
    must satisfy run in :mod:`stride_service.critic`.
    """
    return loader.load(RECRITIC_PROMPT_NAME).strip() + "\n"


def compose_extract_prompt(loader: MarkdownLoader) -> str:
    """The extraction prompt: semi-structured input text to a System Model."""
    return loader.load(EXTRACT_PROMPT_NAME).strip() + "\n"


def compose_repair_prompt(loader: MarkdownLoader) -> str:
    """The one-shot repair prompt: validator issues plus the original input."""
    return loader.load(REPAIR_PROMPT_NAME).strip() + "\n"
