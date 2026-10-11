"""Prompt composition for the five LLM node kinds.

Prompt content lives in ``prompts/`` as Markdown, and loads through the same
:class:`~analysis_service.markdown_loader.MarkdownLoader` the skills use. A
skill is what to know, and a prompt is what to do with this job's input.

Composition here is concatenation only. The ``{lane}``, ``{system_model}``,
``{boundary_crossings}``, ``{evidence_catalog}``, ``{candidates}``,
``{domain_skills}``, ``{drafts}``, ``{input_text}``, ``{previous_model}`` and
``{validation_issues}`` placeholders stay untouched, for ADK state templating to
fill at run time.

The five bodies are the service's, and they are framework-neutral. A prompt says
what to do with this job's input, and every registered framework's lane agent
does the same thing with it: read the model, work the leads, cite from the
catalog, and emit an object holding ``claims``. What differs is what to look
for, and that is a skill — the lane skill, the exemplars, the critic text — which
is package text under ``frameworks/<name>/`` that
:mod:`analysis_service.skills` composes. Two frameworks reading two copies of
``analyze.md`` would be two places for the output contract to drift.

Two blocks are the exception, and both are package text. The output contract,
``output.md``, says what one claim is and which fields carry it, which a record
that grades nothing cannot share with one that does. The exemplars,
``lanes/<lane>/exemplars.md``, are worked drafts in that record's own shape.
:func:`compose_analyze_prompt` takes the package's loader for exactly those two.

Order is stable-first: the one shared ``analyze.md`` body, then the package's
output contract, then the per-lane exemplar file. A framework's lane agents
therefore share the longest possible cacheable prefix.
``tests/test_prompt_lints.py`` enforces the token caps in
:mod:`analysis_service.token_caps` over this text.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from analysis_service.assertions import REGISTRY, Predicate, referent_type
from analysis_service.claims import FrameworkName
from analysis_service.compact import COMPACT_FORMAT, FULL_FORMAT
from analysis_service.frameworks import LANE_CLOSING_DOC, OUTPUT_DOC
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.skills import lane_exemplars_doc

# The four fixed H2 sections of an agent prompt, in order. The lints enforce
# these exact strings.
PROMPT_SECTION_HEADINGS: tuple[str, ...] = ("Role", "Input", "Procedure", "Output")

# The prompt bodies, by node kind.
ANALYZE_PROMPT_NAME = "analyze"
CRITIC_PROMPT_NAME = "critic"
RECRITIC_PROMPT_NAME = "recritic"
EXTRACT_PROMPT_NAME = "extract"
#: The compact transport's delta, appended after ``extract.md``. Not a prompt
#: body: it carries no Role, Input or Procedure of its own, because the whole
#: point is that both extraction routes read one body and differ only in what
#: they are asked to write. See :mod:`analysis_service.compact`.
EXTRACT_COMPACT_PROMPT_NAME = "extract-compact"
REPAIR_PROMPT_NAME = "repair"
ASSERT_PROMPT_NAME = "assert"
PROMPT_BODY_NAMES: tuple[str, ...] = (
    EXTRACT_PROMPT_NAME,
    REPAIR_PROMPT_NAME,
    ASSERT_PROMPT_NAME,
    ANALYZE_PROMPT_NAME,
    CRITIC_PROMPT_NAME,
    RECRITIC_PROMPT_NAME,
)

#: The parts of ``prompts/`` that are appended to a body rather than loaded as
#: one. A delta answers the same lints a body does for size and for content; it
#: answers none of the ones about section structure, which it deliberately has
#: none of.
PROMPT_DELTA_NAMES: tuple[str, ...] = (EXTRACT_COMPACT_PROMPT_NAME,)

#: What each extraction transport appends to ``extract.md``, in order. A table
#: rather than a branch, and the twin of
#: :data:`~analysis_service.graph.EXTRACTION_SCHEMAS`: a transport is a schema
#: and the text that describes it, and a missing key raises here rather than
#: quietly composing the prompt for the other route.
EXTRACTION_DELTAS: dict[str, tuple[str, ...]] = {
    FULL_FORMAT: (),
    COMPACT_FORMAT: (EXTRACT_COMPACT_PROMPT_NAME,),
}

# The token caps over these bodies are drift alarms rather than a budget, and
# they live in one table with every other one: ``analysis_service.token_caps``.
# ADR 0016 says why they are one table.


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


def lane_closing(
    package_loader: MarkdownLoader, framework: FrameworkName
) -> str | None:
    """The text a lane of ``framework`` reads last, or ``None`` where it has none."""
    doc = LANE_CLOSING_DOC[framework]
    return package_loader.load(doc).strip() if doc else None


def compose_critic_prompt(loader: MarkdownLoader) -> str:
    """The critic's prompt: the judgement steps over one framework's drafts.

    No exemplars — the critic rules on drafts it is given rather than
    producing new ones, and the mechanical checks it must not re-perform run
    in :mod:`analysis_service.critic`.

    What this framework's verdicts *assert* is not here: that is the package's
    own ``critic.md``, composed into the node's skills by
    :func:`~analysis_service.skills.compose_critic_skills`.

    The question kinds follow the body, rendered from their table for the
    reason :func:`compose_assert_prompt` gives.
    """
    parts = [loader.load(CRITIC_PROMPT_NAME), render_question_kinds()]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def render_question_kinds() -> str:
    """The question kinds as the list the critic reads, one kind to a line.

    The provider schema lists them too, but a provider may treat a schema as a
    hint and never show the model its descriptions (``QA-2026-09-26-03-E10``),
    so the instruction carries the list whatever the route does with it. A
    kind answered in parts lists each part's ID, which a reference names in
    ``facets`` where the argument rests on some parts only (ADR 0073).
    """
    rows = ["## The question kinds", ""]
    for name, kind in QUESTION_KINDS.items():
        parts = "; ".join(f"`{facet.id}` ({facet.question})" for facet in kind.facets)
        rows.append(
            f"- `{name}`: {kind.covers}" + (f". Its parts: {parts}" if parts else "")
        )
    return "\n".join(rows)


def compose_recritic_prompt(loader: MarkdownLoader) -> str:
    """The critic re-ask prompt: a bounded reconciliation of the critic's output.

    No exemplars, like the critic — it re-rules the drafts it was given
    against the mechanical problems in its previous output, and the checks it
    must satisfy run in :mod:`analysis_service.critic`.

    The question kinds follow the body as they follow the critic's, because a
    repaired ``related_unknowns`` entry may have to name one.
    """
    parts = [loader.load(RECRITIC_PROMPT_NAME), render_question_kinds()]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_extract_prompt(
    loader: MarkdownLoader, extraction_format: str = FULL_FORMAT
) -> str:
    """The extraction prompt: semi-structured input text to a System Model.

    One body for both transports. ``extract.md`` says what to read and what to
    write down; the compact route appends ``extract-compact.md``, which says
    only how the answer is spelled on the wire. A second full prompt would be a
    second copy of the reading rules, and the two would answer the transcription
    question differently the first time one of them was edited.

    The delta goes last for the reason the package parts do: the body is the
    longer text and the one both routes share, so the cacheable prefix is as
    long as it can be either way.
    """
    if extraction_format not in EXTRACTION_DELTAS:
        raise ValueError(f"unknown extraction format: {extraction_format!r}")
    parts = [
        loader.load(name)
        for name in (EXTRACT_PROMPT_NAME, *EXTRACTION_DELTAS[extraction_format])
    ]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def compose_repair_prompt(loader: MarkdownLoader) -> str:
    """The one-shot repair prompt: validator issues plus the original input."""
    return loader.load(REPAIR_PROMPT_NAME).strip() + "\n"


def compose_assert_prompt(loader: MarkdownLoader) -> str:
    """The assertion prompt: the body, then the predicate registry rendered.

    **The table is rendered from the registry, never written in the prompt.**
    A predicate's meaning, the subjects it takes and the shape of its value are
    facts :data:`~analysis_service.assertions.REGISTRY` already holds, and the
    gate reads them from there. A second copy in prose would be a second reader
    of one rule, and the prompt's copy is the one nothing checks.
    """
    parts = [loader.load(ASSERT_PROMPT_NAME), render_predicates("catalog")]
    return "\n\n".join(part.strip() for part in parts) + "\n"


#: Which composer is rendering the predicate table. A closed set, so a stage
#: nobody has written reads as a type error here rather than as a wrong sentence
#: in a shipped prompt.
Stage = Literal["catalog"]

#: How a ``reference`` predicate's value is spelled, per stage that renders the
#: table. No resolver takes a display name, so each stage states its spelling:
#: the catalog stage writes the subject's identity, which
#: :func:`~analysis_service.assertions.resolve_catalog` looks up among the
#: subjects the catalog declares.
#:
#: Keyed by the composer that renders it, so a stage added tomorrow raises here
#: rather than inheriting whichever sentence happened to be first. **No default
#: anywhere**, for the same reason: a composer that says nothing about its stage
#: inherits one spelling in silence, which is the failure this table answers
#: rather than a shorter way to call it.
REFERENCE_FORMS: Mapping[Stage, str] = MappingProxyType(
    {"catalog": "the ID of a {referent}"}
)


def render_predicates(stage: Stage) -> str:
    """The predicate registry as the table a model reads.

    One row per predicate, in registry order: what it means, which subjects it
    takes, and what its value may be. A ``term`` predicate lists its own words
    beside the two every predicate admits; a ``reference`` one names what it
    points at, in the spelling ``stage``'s own resolver reads
    (:data:`REFERENCE_FORMS`); free text says so. ``stage`` is required, because
    the spelling differs at every one of them and no composer may inherit
    another's.

    A ``stated_only`` predicate says so in the same row. That is a registry
    field the gate reads — it refuses an ``inferred`` basis on a predicate
    whose value *identifies* rather than describes — and a row that did not
    carry it cost the model the whole fact with no way to have known.
    """
    rows = [
        "## The predicates",
        "",
        (
            "Every predicate also admits `absent` — the source says the thing is"
            " not there — and `unknown`, which takes a `reason`."
        ),
        "",
        "| Predicate | Subjects | Value | Means |",
        "| --- | --- | --- | --- |",
    ]
    for name, predicate in REGISTRY.items():
        subjects = ", ".join(sorted(predicate.subjects))
        meaning = predicate.meaning
        if predicate.stated_only:
            meaning += "; only where a source states it, never inferred"
        rows.append(
            f"| `{name}` | {subjects} | {_value_form(predicate, stage)} | {meaning} |"
        )
    scoped = [
        f"`{name}` needs a scope naming its " + " and ".join(predicate.requires)
        for name, predicate in REGISTRY.items()
        if predicate.requires
    ]
    if scoped:
        rows += ["", "Scope requirements: " + "; ".join(scoped) + "."]
    return "\n".join(rows)


def _value_form(predicate: Predicate, stage: Stage) -> str:
    """How one predicate's value is written, for the rendered table."""
    if predicate.value == "term":
        return ", ".join(f"`{term}`" for term in sorted(predicate.terms))
    if predicate.value == "reference":
        return REFERENCE_FORMS[stage].format(referent=referent_type(predicate))
    return "what the source says, in a few words"
