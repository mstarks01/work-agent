"""Prompt composition for the five LLM node kinds.

Prompt content lives in ``prompts/`` as Markdown and loads through the same
:class:`~stride_service.markdown_loader.MarkdownLoader` the skills use — a
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
:mod:`stride_service.skills`. Two frameworks reading two copies of ``analyze.md``
would be two places for the output contract to drift.

The one exception is the **exemplars**, which are worked drafts in one
framework's own record shape and so live under its package root
(``lanes/<lane>/exemplars.md``). :func:`compose_analyze_prompt` takes the
package's loader for exactly that block.

Order is stable-first: the one shared ``analyze.md`` body precedes the per-lane
exemplar file, so a framework's lane agents share the longest possible cacheable
prefix. The token caps here are enforced by ``tests/test_prompt_lints.py``.
"""

from __future__ import annotations

from stride_service.markdown_loader import MarkdownLoader
from stride_service.skills import lane_exemplars_doc

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
# third exemplar system has to be argued for rather than added.
#
# THE SECOND EXEMPLAR SYSTEM IS WHAT THE LAST RAISE BOUGHT: ~500 tokens for a
# fleet-telemetry platform beside the payments one, event-driven and
# multi-tenant where the first is synchronous request/response, worked by six
# of the eighteen exemplars. The cost is real and lands on every lane agent on
# every job. What it buys is that the exemplars stop teaching one architecture
# alongside the method — an agent shown only payments has no way to tell which
# parts of the reasoning were the domain, and the far-domain corpus cases are
# where that shows up (docs/adr/0006-two-exemplar-systems.md).
#
# TWO is the argued number, not a step toward six. Cost here is linear in
# systems and the diversity it buys is not: the first contrasting system breaks
# a monoculture, the fifth mostly restates it, and per-technology depth has a
# carrier that costs nothing on jobs that do not earn it — the domain packs in
# ``domains/``, selected per job. System B is deliberately smaller than
# system A (four elements and three flows against six and five), because it
# exists to contrast rather than to be a second full-fidelity model.
#
# THREE DERIVED BLOCKS ALSO SHARE THIS BUDGET, and they arrived separately, so
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
# was 2239 before either. A further derived block should be argued for against
# what an agent can actually hold in mind, not against this number.
#
# THE FOURTH AND FIFTH BLOCKS ARE THE RETRIEVED CORPUS, and here is the
# argument the line above asks for. A package's ``notes/`` and ``cases/``
# are selected per lane by the rules that actually fired
# (:mod:`stride_service.knowledge`), so they cost ~57 tokens of *static* text
# here — one clause in the Input section and two sentences fixing their
# standing — while the material itself rides in the job-varying block and is
# capped where it is retrieved, at two notes and one case per lane.
#
# The standing sentences are what earn their place rather than the pointer: a
# retrieved note reads exactly like the System Model until something says it is
# not a fact about this system, and a case ending in a rejection reads like an
# instruction to reject until something says it is somebody else's reasoning.
# Both are the failure this prompt spends most of its length preventing, so the
# blocks arrive with the same treatment candidates got.
#
# 3550 -> 3850 IS THE EVIDENCE CATALOG BECOMING A TABLE (#138). A live sweep put
# 2 of 12 jobs on the floor because agents composed well-formed evidence
# references to facts the catalog did not hold — correct grammar, plausible
# element IDs, absent from the set — and a bad reference fails its whole job.
# Rendered as a JSON array of ID strings the catalog read as a specimen of the
# format; the two exemplar catalogs move to the table shape agents now receive,
# which is most of the raise, and the rest is the paragraph in `## Input`
# telling them to select rather than compose and what an *absent* row means.
#
# This is the most expensive block-shape change in the file and the easiest to
# justify: every other line here improves a finding, and this one is the
# difference between a job returning and a job dying. Cheaper phrasings were
# tried first — the row gloss deliberately does not repeat the element ID
# standing in the left column beside it.
#
# 3850 -> 3900 is the scope line: ~44 tokens saying what the denominators are
# and, at greater length, that they are not a quota. The numbers themselves are
# job-varying and cost nothing here. The disclaimer is most of the spend and is
# the part that had to be written — an agent handed "17 elements" with no
# framing has been given a target, and inflating a lane's draft count is a worse
# failure than the undercounting the line exists to fix.
#
# 3900 -> 4100 IS THE CATALOG LEARNING TO SAY "STATED ABSENT" (#171). Roughly
# 160 tokens, and about 60 of them are the two rows exemplar system A now
# carries for a flow the input says is unauthenticated and unencrypted — rows
# the catalog previously could not offer at all, so the exemplar grounded that
# flow on a quote instead. The rest is the distinction those rows make citable:
# the sub-step separating an attribute the input left open from one it ruled
# out, and the sentence saying to cite the row rather than re-quote the
# sentence behind it.
#
# It buys back more runtime tokens than it spends in static ones: a quote is a
# span of submitter prose in every draft that carries it, while a row is an ID,
# and the workaround this replaces put the quote on every finding resting on a
# stated absence. What it fixes is not a token count, though — 18 of 300 corpus
# candidates fire on a control the input states is absent, and none of them had
# a citable fact before this.
ANALYZE_PROMPT_TOKEN_CAP = 4100
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


def compose_analyze_prompt(
    prompt_loader: MarkdownLoader, package_loader: MarkdownLoader, lane: str
) -> str:
    """One lane agent's prompt: the shared body, then that lane's own exemplars.

    Two loaders because the two halves have two owners. ``analyze.md`` is the
    service's, rooted at ``prompts/``, and one templated body serves every lane
    of every registered framework rather than N near-identical copies.
    ``lanes/<lane>/exemplars.md`` is the *package's*, rooted at
    ``frameworks/<name>/``, because a worked draft is written in that framework's
    own record shape and would be a lie in another's.

    ``{lane}`` is left in place for ADK to template.
    """
    parts = [
        prompt_loader.load(ANALYZE_PROMPT_NAME),
        package_loader.load(lane_exemplars_doc(lane)),
    ]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_critic_prompt(loader: MarkdownLoader) -> str:
    """The critic's prompt: the judgement steps over one framework's drafts.

    No exemplars — the critic rules on drafts it is given rather than
    producing new ones, and the mechanical checks it must not re-perform run
    in :mod:`stride_service.critic`.

    What this framework's verdicts *assert* is not here: that is the package's
    own ``critic.md``, composed into the node's skills by
    :func:`~stride_service.skills.compose_critic_skills`.
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
