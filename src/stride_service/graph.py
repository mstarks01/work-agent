"""The ADK Workflow graph: nodes wired to prompts, skills, and models.

The topology::

    START -> extract -> validate -+-valid--> prepare -> 6 category agents -> join
                                  |             ^                             |
                                  +-invalid-> repair                          v
                                                 |                          merge
                                          revalidate -+-invalid-> reject |
                                                      |                  v
                                                      +-valid-> prepare  critic
                                                                          |
                          assemble <--accept-- router <------------------+
                             ^  ^                 |
                             |  |          revise v
                             |  +--accept-- rereview <- recritic
                             |                 |
                             |          revise v
                             +---(none)  critic_failed (raises)

Six of those nodes are structural rather than analytical:

* ``revalidate`` is the second run of the *same* validate function after the
  one repair pass. Two nodes instead of a back-edge because the repair budget
  is exactly one: the graph cannot loop, so it cannot spend a second pass, and
  "one repair then reject" is visible in the topology rather than enforced by a
  counter.
* ``reject`` is where the second failure lands — a terminal node that parks the
  validator's issues in state for the runner to return as a rejection.
* ``merge`` runs :func:`stride_service.critic.join_drafts` behind ADK's
  ``JoinNode``, which is a pure barrier with no user code of its own.
* ``router`` and ``rereview`` are the critic's ``validate``/``revalidate``: one
  ``route_review`` function run twice, keeping the mechanical check outside
  ``assemble`` so a malformed critic output can be re-asked before assembly.
  Clean output accepts to ``assemble``; a malformed one revises — to
  ``recritic`` the first time, to ``critic_failed`` the second.
* ``recritic`` is the one bounded critic re-ask, the ``repair`` of the review
  half. A structural pass, not a counted one — the graph cannot loop back for a
  third.
* ``critic_failed`` is where a still-malformed re-ask lands: it *raises* rather
  than parking issues, because a critic that will not return its own drafts
  whole is our defect (a ``failed`` job), not the input's (a ``rejected`` one,
  which carries ``ValidationIssue``s).

Every LLM node binds its model through the caller's ``resolve_model`` (the
canonical names in :data:`stride_service.model_tiers.LLM_NODES`), its skills
through :mod:`stride_service.skills`, and its prompt through
:mod:`stride_service.prompts`. Graph node names must be Python identifiers,
so ``analyze/information-disclosure`` in the tier config is
``analyze_information_disclosure`` here; :data:`TIER_NODE_BY_GRAPH_NODE` is
the only place that correspondence lives. Both halves now spell the six
category agents the same way — the tier keys moved with
``config/model_tiers.toml`` v4 and the graph names with report schema 2.0,
which is the bump these names earned: a consumer keying on ``analyst_spoofing``
in ``nodes[].node`` does not error, it matches nothing, silently.

The bookends are deliberately deterministic, because mechanical work belongs in
code: category agents cannot receive a malformed view, the report cannot cite an
element the model does not contain, and a quote a finding rests on is matched
against the submitter's own bytes rather than taken on trust. Every check in
this module fails closed — a raising FunctionNode aborts the workflow, which
the runner turns into a failed job.

One of those checks is for **silence** rather than for malformed content. An
LLM node that emits no text writes no ``output_key``, so the absence arrives
where the next node reads state, not as anything that raised. ``validate`` and
``merge`` name it (:class:`SilentNodeError`) instead of reading it as an empty
value, which is what keeps a truncated category agent from deleting a STRIDE
category from a report that still finishes green.

Security: the submitted text is untrusted and reaches the extraction prompt
**and now the six category agents' prompts** inside a fenced block that names it
as data (OWASP LLM01) — the agents read it so they can quote it, which is the
whole of finding-level attribution and is why ``analyze.md`` carries the same
data-not-instruction paragraph ``extract.md`` does. Everything a model emits is
untrusted output validated before use (LLM05) — by ``output_schema`` at the node
boundary, then by the System Model gate and the critic seams here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import START, FunctionNode, JoinNode, Workflow
from google.genai import types

from stride_service.critic import assemble_threats, join_drafts, review_issues
from stride_service.markdown_loader import MarkdownLoader
from stride_service.prompts import (
    compose_analyze_prompt,
    compose_critic_prompt,
    compose_extract_prompt,
    compose_recritic_prompt,
    compose_repair_prompt,
)
from stride_service.report import (
    STRIDE_CATEGORIES,
    DraftThreat,
    DraftThreats,
    StrideCategory,
    Summary,
    Threat,
    ThreatRuling,
    ThreatRulings,
    UnverifiedGround,
    build_summary,
)
from stride_service.resilience import ResilienceConfig
from stride_service.retry import TRUNCATION_REMEDY
from stride_service.sampling import (
    SamplingResolver,
    TierSampling,
)
from stride_service.skills import compose_analyze_skills, compose_critic_skills
from stride_service.sources import fence_for
from stride_service.system_model import BoundaryCrossing, SystemModel
from stride_service.validation import ValidationIssue, parse_and_validate

if TYPE_CHECKING:
    # Type-only, so composing a graph costs no provider-library import and the
    # binding <-> graph reference stays one-directional at run time.
    from stride_service.binding import NodeBinding

# Resolves one canonical LLM node name (as in ``LLM_NODES``) to the model it
# runs on. A ``BaseLlm`` instance is accepted so tests can drive the whole
# graph without a Vertex endpoint.
ModelResolver = Callable[[str], str | BaseLlm]

# --- Node names -------------------------------------------------------------

EXTRACT_NODE = "extract"
VALIDATE_NODE = "validate"
REPAIR_NODE = "repair"
REVALIDATE_NODE = "revalidate"
REJECT_NODE = "reject"
PREPARE_NODE = "prepare"
JOIN_NODE = "join"
MERGE_NODE = "merge"
CRITIC_NODE = "critic"
ROUTER_NODE = "router"
RECRITIC_NODE = "recritic"
REREVIEW_NODE = "rereview"
CRITIC_FAILED_NODE = "critic_failed"
ASSEMBLE_NODE = "assemble"


def analyze_node_name(category: StrideCategory) -> str:
    """This category agent's graph node name (an identifier, unlike the ID)."""
    return f"analyze_{category.replace('-', '_')}"


ANALYZE_GRAPH_NODES: tuple[str, ...] = tuple(
    analyze_node_name(category) for category in STRIDE_CATEGORIES
)

# Graph node name -> the canonical LLM node name the tier config keys on.
TIER_NODE_BY_GRAPH_NODE: dict[str, str] = {
    EXTRACT_NODE: "extract",
    REPAIR_NODE: "repair",
    CRITIC_NODE: "critic",
    RECRITIC_NODE: "recritic",
    **{
        analyze_node_name(category): f"analyze/{category}"
        for category in STRIDE_CATEGORIES
    },
}

# --- Routes -----------------------------------------------------------------

Entry = Literal["extract", "prepare", "extract-only"]

ENTRY_EXTRACT: Entry = "extract"
ENTRY_PREPARE: Entry = "prepare"
"""The analysis eval mode's entry: start at ``prepare`` over a blessed model
seeded in state, so a recall miss cannot be blamed on an element ``extract``
never produced."""

ENTRY_EXTRACT_ONLY: Entry = "extract-only"
"""The extraction eval mode: run ``extract`` and stop, leaving its emission at
:data:`STATE_EXTRACTED_MODEL` for the caller to put through the same
:func:`~stride_service.validation.parse_and_validate` gate ``validate`` uses.
Spending six category agents and a critic to score an extraction would be six kinds
of noise on one number."""

ROUTE_VALID = "valid"
ROUTE_INVALID = "invalid"
ROUTE_ACCEPT = "accept"
ROUTE_REVISE = "revise"
"""The critic re-ask route. ``route_review`` takes it when the critic's output
fails the mechanical check: from ``router`` it reaches the bounded ``recritic``
re-ask, and from ``rereview`` — the second look after that re-ask — it reaches
``critic_failed``, since a repeated failure is ours to own, not the input's to
be rejected for."""

# --- State keys -------------------------------------------------------------
#
# The six keys the prompt files template against carry *rendered* text, since
# ADK substitutes ``str(value)`` into an instruction. The structured values
# the FunctionNodes pass between themselves live under their own keys.
#
# Two key families, and the invariant that keeps them honest:
# *structured* keys are the code's view (Pydantic round-trips), *rendered*
# keys are the model's view (:func:`render` output). Both copies of an
# artifact are kept on purpose — reading back exactly the bytes a model saw is
# what makes a failed job debuggable — so the rule that stops them drifting
# is: **a rendered key is written once by the FunctionNode that derives it,
# and never read by Python.** No node mutates an artifact after rendering it.
# A future node that re-renders or edits one of these in place breaks the
# report's traceability without failing any test.

STATE_INPUT_TEXT = "input_text"
# The job's sources as ``label -> text``, for the two checks that take data
# from outside the model: the gate rule that a ``source_excerpt``'s citation
# names a source the job actually carried, and the fan-in's check that a
# finding's quote is really in the source it names. Written by the executor
# beside the rendered input, never by a node.
#
# The structured counterpart of :data:`STATE_INPUT_TEXT`, which holds the same
# bytes *rendered* — one key per view, under this module's rule that a rendered
# key is written once and never read back by Python.
STATE_SOURCE_TEXTS = "source_texts"
STATE_SYSTEM_MODEL = "system_model"
STATE_BOUNDARY_CROSSINGS = "boundary_crossings"
STATE_DRAFT_THREATS = "draft_threats"
STATE_PREVIOUS_MODEL = "previous_model"
STATE_VALIDATION_ISSUES = "validation_issues"
STATE_PREVIOUS_REVIEW = "previous_review"
STATE_CRITIC_ISSUES = "critic_issues"
STATE_DRAFT_ROSTER = "draft_roster"
STATE_UNRECONCILED_DRAFTS = "unreconciled_drafts"

STATE_EXTRACTED_MODEL = "extracted_model"
STATE_VALID_MODEL = "valid_model"
STATE_MERGED_DRAFTS = "merged_drafts"
STATE_UNVERIFIED_GROUNDS = "unverified_grounds"
STATE_REVIEWED_THREATS = "reviewed_threats"
STATE_ANALYSIS = "analysis"
STATE_REJECTION = "rejection"


def analyze_state_key(category: StrideCategory) -> str:
    """Where one category agent parks its drafts for the merge node."""
    return f"drafts_{category.replace('-', '_')}"


class SilentNodeError(RuntimeError):
    """An LLM node finished without emitting anything for the next node to read.

    Not a malformed emission — *no* emission. ADK writes a node's ``output_key``
    only from a final event carrying a non-thought text part, so a completion
    that comes back with no text writes no key at all, raises nothing, and
    leaves the hole to be discovered downstream. The usual cause is truncation:
    the response was cut off at ``max_output_tokens``, which reasoning tokens
    are spent against as well.

    That shape is a **vendor behaviour, not the shape of truncation**. Anthropic
    and Vertex return no text; OpenAI returns the fragment it had written, which
    never reaches here because it writes its key like any other answer.
    :class:`~stride_service.retry.TruncatedCompletionError` catches that one
    upstream, off ``finish_reason``, before a validator sees a partial document
    and misreports it as malformed. This class remains the net for the silent
    half, where nothing but the absent key says anything happened at all.

    Absence is deliberately **not** read as emptiness. An agent that finds no
    threats in its lane emits ``{"threats": []}`` and its key is written; a
    truncated one writes nothing. The two look identical once a missing key is
    defaulted to an empty list, which is how a silently dropped STRIDE category
    used to reach a finished report.

    Ours to own rather than the input's, so it fails the job rather than
    rejecting it — the same split :func:`fail_review` makes, and the reason this
    is a ``RuntimeError`` beside :class:`~stride_service.pipeline.PipelineError`
    rather than a ``ValueError`` beside the input errors.
    """


# Every SilentNodeError says the same two things: nothing was written, and here
# is the knob. Kept in one place because the two raise sites are one bug.
#
# Only the first half is this module's. The knob is the same one
# :class:`~stride_service.retry.TruncatedCompletionError` names, because the two
# are one failure seen from either side of a vendor difference, so the remedy is
# imported rather than restated.
_TRUNCATION_HINT = (
    "The node completed without emitting any text, which is what a completion"
    " truncated at max_output_tokens looks like on a vendor that returns no"
    " partial output — reasoning tokens are spent against that cap too."
    f" {TRUNCATION_REMEDY}"
)


@dataclass(frozen=True)
class Analysis:
    """What the graph produces: a report minus the facts only the runner has.

    Job identity and per-node timings belong to whoever ran the graph, so the
    assemble node stops here and :mod:`stride_service.pipeline` stamps the
    rest onto a :class:`~stride_service.report.StrideReport`.
    """

    system_model: SystemModel
    boundary_crossings: list[BoundaryCrossing]
    threats: list[Threat]
    rejected_threats: list[Threat]
    # Service-owned, computed at the fan-in, and covering both arrays above —
    # a rejected threat keeps its grounds, so it keeps their marks too.
    unverified_grounds: list[UnverifiedGround]
    summary: Summary

    def to_state(self) -> dict[str, Any]:
        """The JSON-safe form parked in session state."""
        return {
            "system_model": self.system_model.model_dump(mode="json"),
            "boundary_crossings": [
                crossing.model_dump(mode="json") for crossing in self.boundary_crossings
            ],
            "threats": [threat.model_dump(mode="json") for threat in self.threats],
            "rejected_threats": [
                threat.model_dump(mode="json") for threat in self.rejected_threats
            ],
            "unverified_grounds": [
                mark.model_dump(mode="json") for mark in self.unverified_grounds
            ],
            "summary": self.summary.model_dump(mode="json"),
        }

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> Analysis:
        """Rebuild from session state, revalidating every part."""
        return cls(
            system_model=SystemModel.model_validate(data["system_model"]),
            boundary_crossings=[
                BoundaryCrossing.model_validate(crossing)
                for crossing in data["boundary_crossings"]
            ],
            threats=[Threat.model_validate(threat) for threat in data["threats"]],
            rejected_threats=[
                Threat.model_validate(threat) for threat in data["rejected_threats"]
            ],
            unverified_grounds=[
                UnverifiedGround.model_validate(mark)
                for mark in data["unverified_grounds"]
            ],
            summary=Summary.model_validate(data["summary"]),
        )


def render(value: Any) -> str:
    """The one way a structured value enters a prompt: pretty, stable JSON."""
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def render_fenced(value: Any) -> str:
    """A rendered value plus a fence it cannot close.

    ``render`` is ``json.dumps``, which escapes quotes, backslashes and
    newlines but **not** backticks. A System Model carries caller words by rule
    — ``source_excerpt`` verbatim, and ``notes`` quoting what a speaker said —
    so a value holding a fence would close a static one in the prompt and land
    the bytes after it in instruction position. That is the hole
    :func:`~stride_service.sources.render_sources` closes one node upstream,
    with the same technique and the same sizing rule: sized once over the whole
    rendered document, because this seam renders one JSON blob rather than N
    caller-controlled blocks.
    """
    body = render(value)
    fence = fence_for(body)
    return f"{fence}\n{body}\n{fence}"


# --- Deterministic node functions -------------------------------------------
#
# Plain functions, wrapped in FunctionNodes below. Parameters bind by name
# from session state; ``ctx`` is ADK's node context, whose ``state`` writes
# become the session's state delta.


def validate_extraction(
    ctx, extracted_model: dict | None = None, source_texts: dict | None = None
) -> Event:
    """Run the mechanical validity gate and route on the result.

    Element IDs are derived here rather than demanded of the model (ticket
    037), so ``repair``'s one pass is never spent reconciling an ID with its
    own name.

    On failure, parks the rejected model and the issues where the repair
    prompt's ``{previous_model}`` and ``{validation_issues}`` placeholders
    read them. What is parked is the *normalized* model whenever there is one,
    because that is the artifact the issues were computed against — handing
    repair the pre-normalization IDs would cite elements it cannot find.
    Both validate nodes run this same function — what differs is where their
    ``invalid`` edge points.

    ``extracted_model`` defaults so that a silent extraction is named rather
    than hitting ADK's parameter binding, which would report a missing argument
    to *this* function and read as a graph defect. It raises rather than routing
    to ``repair``: an absent model is not an invalid one, ``repair`` is a
    transcriber given issues to fix and there are none, and it sits on the same
    tier under the same cap — so the pass most likely to truncate would be
    followed by the pass that truncates identically, then a rejection carrying
    validation issues nobody computed.
    """
    if extracted_model is None:
        raise SilentNodeError(
            f"nothing was written to {STATE_EXTRACTED_MODEL!r}, so there is no"
            f" model to validate. {_TRUNCATION_HINT}"
        )
    model, issues = parse_and_validate(
        extracted_model, normalize_ids=True, source_labels=source_texts or ()
    )
    if issues or model is None:
        parked = extracted_model if model is None else model.model_dump(mode="json")
        # Fenced for the same reason as the agents' copy: this is the model
        # built from caller words, handed straight back to a model.
        ctx.state[STATE_PREVIOUS_MODEL] = render_fenced(parked)
        ctx.state[STATE_VALIDATION_ISSUES] = render(
            [issue.model_dump(mode="json") for issue in issues]
        )
        return Event(route=ROUTE_INVALID, output={"issue_count": len(issues)})

    ctx.state[STATE_VALID_MODEL] = model.model_dump(mode="json")
    return Event(route=ROUTE_VALID, output={"issue_count": 0})


def reject_model(validation_issues: str, ctx) -> dict[str, Any]:
    """Terminal node: the repaired model failed too, so the job is rejected.

    Nothing is auto-repaired and nothing is analyzed on a model that never
    passed the gate — the user gets the validator's issues instead.
    """
    ctx.state[STATE_REJECTION] = validation_issues
    return {"rejected": True}


# Element fields stripped from the model rendered to the six category agents
# and the critic. They survive on STATE_VALID_MODEL, so the report still
# carries them.
_ELEMENT_SOURCE_FIELDS = ("source_excerpt", "source_label", "source_speaker")


def _without_source_fields(valid_model: dict) -> dict:
    """The model as a reasoning view: every element's own quote removed.

    Two reasons, and the second is the one that matters. Once the full source
    text is in the same request as ``{input_text}``, an element's excerpt is a
    lossy duplicate of bytes already there. And leaving it in preserves exactly
    the failure finding-level attribution was written against — an agent
    reaching for the nearest excerpt instead of the span that actually
    triggered its finding. Removing the shortcut is stronger than wording
    against it, and it makes the two quotes structurally independent: an agent
    cannot align its quote to an element's excerpt because it can no longer see
    it. That disagreement is legitimate and ungoverned; the two spans are
    chosen by different readers from different views.

    Nothing is stranded. A quote ground must name its ``source_label``, and the
    label rides *inside* each fence by construction, so every label a job
    carries is visible in ``{input_text}``.

    ``notes`` is deliberately untouched: the prompt binds it as context for the
    needs-info question, and it is also what lets the critic recognise a quote
    lifted out of a note — a quote the ladder cannot tell from a legitimate one,
    because it *is* verbatim submitter text.

    The honest cost: the bytes an agent saw are no longer the bytes the report
    carries.
    """
    stripped = dict(valid_model)
    for collection, entries in stripped.items():
        if collection == "assumptions" or not isinstance(entries, list):
            continue
        stripped[collection] = [
            {
                field: value
                for field, value in element.items()
                if field not in _ELEMENT_SOURCE_FIELDS
            }
            for element in entries
        ]
    return stripped


# Draft fields stripped from the set rendered to the critic and its re-ask.
# They survive on STATE_MERGED_DRAFTS, so assemble_threats still carries them
# into the report exactly as the agent wrote them.
_DRAFT_UNRULED_FIELDS = frozenset({"mitigations"})


def _ruling_view(drafts: Sequence[DraftThreat]) -> list[dict]:
    """The drafts as the critic reads them: no mitigations, no empty branches.

    The critic's five steps read ``description`` (evidence), ``category``
    (lane), ``affected_element_ids`` (duplicate), ``severity`` (calibration)
    and ``grounds`` (confidence). ``mitigations`` is read by none of them, and
    the prompt already says so — it is held beside the ruling and copied into
    the report untouched. A :class:`~stride_service.report.Mitigation` is a
    200-character summary plus 2000 characters of detail, and a draft carries a
    list of them, so this is the largest block in the longest prompt the graph
    sends that no judgement is spent on. Same argument as
    :func:`_without_source_fields`, one node further down.

    ``exclude_defaults`` is what drops the empty branches of a
    :class:`~stride_service.report.Ground`. That model is one flat object
    rather than a discriminated union — a deliberate choice, for provider
    schema-compiler reasons it documents itself — so four of its six fields are
    the empty string on any given ground, and rendering them spends a line each
    on a field whose own validator forbids it carrying anything.

    THE HAZARD THIS BUYS, stated rather than left to be discovered: a field
    added to ``DraftThreat`` with a default now disappears from the critic's
    view whenever it holds that default, silently and with nothing downstream
    able to see it. Every field the five steps read is required today and so
    cannot be dropped; ``test_ruling_view_keeps_every_field_the_critic_rules_on``
    is what holds that true for the next field.
    """
    return [
        draft.model_dump(
            mode="json", exclude=_DRAFT_UNRULED_FIELDS, exclude_defaults=True
        )
        for draft in drafts
    ]


def prepare_analysis(valid_model: dict, ctx) -> dict[str, Any]:
    """Derive boundary crossings and render the category agents' shared view.

    Crossings are computed here rather than extracted, so no agent can be handed
    a crossing that contradicts the zones in the model it is reading.

    The rendered view is the *stripped* model — see
    :func:`_without_source_fields`. The critic templates against this same key,
    so it is stripped there too, and neither reads a submitter's words except
    the ones a finding chose to quote.
    """
    model = SystemModel.model_validate(valid_model)
    crossings = model.boundary_crossings()
    ctx.state[STATE_SYSTEM_MODEL] = render_fenced(_without_source_fields(valid_model))
    ctx.state[STATE_BOUNDARY_CROSSINGS] = render(
        [crossing.model_dump(mode="json") for crossing in crossings]
    )
    return {"element_count": len(model.elements()), "crossing_count": len(crossings)}


def _threats_of(payload: object) -> list[Any]:
    """The list inside an LLM node's emission, or empty if the node never ran.

    The category agent and review nodes emit ``{"threats": [...]}`` rather than a bare
    array, because a bare ``list[...]`` schema is one ADK cannot convert into a
    response format — it sends none and the node generates unconstrained. The
    wrapper is the schema's shape, not the domain's, so it is unwrapped here at
    the boundary and nothing downstream carries it.

    A missing key is a node that produced nothing, which is the same absence the
    bare-list read treated as empty; a *malformed* payload is not this
    function's to catch, since every element is validated by the caller.

    ``None`` reaches here from a node that ran and emitted no text at all — see
    :func:`route_review`. It is not a dict, so it reads as the empty list, which
    is what makes "the critic returned nothing" the maximally malformed output
    rather than a special case.
    """
    if not isinstance(payload, dict):
        return []
    threats = payload.get("threats")
    return threats if isinstance(threats, list) else []


def merge_drafts(
    valid_model: dict, ctx, source_texts: dict | None = None
) -> dict[str, Any]:
    """Merge the six category agents' drafts into the single list the critic sees.

    The mechanical half of the fan-in: :func:`join_drafts` fails closed if a
    draft cites an element the model does not contain, if two agents reused a
    threat ID, if a grounds reference does not resolve, or if no ground on a
    threat verifies at all — so the critic spends judgement on evidence, lanes
    and whether a verbatim quote actually supports what it was filed under.

    It also returns what it could *not* verify: quote grounds absent from the
    source they name are marked rather than dropped, and the marks are parked
    for :func:`assemble_report` to carry into the report. ``source_texts``
    defaults to ``None`` so the in-process engine, which drives a hand-authored
    model with no job behind it, is not failed on a citation that is not wrong.

    An agent that emitted **nothing** fails the job here, before any of that.
    Six lanes are what makes the output a STRIDE model rather than a list of
    findings, so a lane that never ran is not a smaller report — it is a
    different method, and one whose absence nothing downstream can see: the
    critic rules what it is given, ``build_summary`` counts what exists, and
    ``by_category`` omits a lane with no threats rather than carrying a zero. A
    truncated agent would delete a sixth of the analysis and finish green.

    A lane that ran and found nothing is a different thing and stays legal — it
    emits ``{"threats": []}``, its key is written, and ``_threats_of`` reads it
    as the empty list it is. That distinction is the whole check: the absent
    key, not the empty list.

    Two state keys, two shapes, on purpose. ``STATE_MERGED_DRAFTS`` holds the
    drafts whole, because that is what ``assemble_threats`` merges rulings onto
    to build the report. ``STATE_DRAFT_THREATS`` is the *prompt* view, narrowed
    by :func:`_ruling_view` to the fields a verdict is actually reached from.
    """
    silent = [
        category
        for category in STRIDE_CATEGORIES
        if ctx.state.get(analyze_state_key(category)) is None
    ]
    if silent:
        keys = ", ".join(repr(analyze_state_key(c)) for c in silent)
        raise SilentNodeError(
            f"{len(silent)} of {len(STRIDE_CATEGORIES)} category agents wrote"
            f" nothing ({keys}), so those STRIDE categories were never"
            f" analyzed. {_TRUNCATION_HINT}"
        )
    model = SystemModel.model_validate(valid_model)
    drafts_by_category = {
        category: [
            DraftThreat.model_validate(draft)
            for draft in _threats_of(ctx.state.get(analyze_state_key(category)))
        ]
        for category in STRIDE_CATEGORIES
    }
    merged, unverified = join_drafts(drafts_by_category, model, source_texts or {})
    ctx.state[STATE_MERGED_DRAFTS] = [draft.model_dump(mode="json") for draft in merged]
    ctx.state[STATE_UNVERIFIED_GROUNDS] = [
        mark.model_dump(mode="json") for mark in unverified
    ]
    ctx.state[STATE_DRAFT_THREATS] = render(_ruling_view(merged))
    return {"draft_count": len(merged), "unverified_count": len(unverified)}


def route_review(
    valid_model: dict,
    merged_drafts: list,
    ctx,
    reviewed_threats: dict | None = None,
) -> Event:
    """Run the mechanical check on the critic's output and route on the result.

    The check runs here rather than in :func:`assemble_report` because
    re-asking a malformed critic means deciding *before* assembly whether it is
    malformed. Clean output routes to ``accept``; a critic that dropped,
    invented or duplicated a ruling, or hung a ``needs-info`` verdict on an
    element the model does not contain, routes to ``revise``, with the failing
    rulings and the problem list parked where the re-ask prompt reads them.

    The re-ask sees the drafts as a **roster of IDs plus the few it must
    read**, not as the whole set again. Its job is structural — cover exactly
    the drafted IDs, once each, with unknowns that resolve — and an ID carries
    the whole of that claim. The two drafts a structural fix cannot be made
    without reading are named by
    :attr:`~stride_service.critic.ReviewProblems.implicated`, which is computed
    beside the messages so the prompt and the check cannot disagree about which
    threats are in trouble.

    Both review nodes run this same function — what differs is where their
    ``revise`` edge points (``recritic`` for the first look, ``critic_failed``
    for the second), exactly as the two validate nodes share one function.

    ``reviewed_threats`` defaults because **an LLM node that emits no text does
    not write its output key at all**, and ADK binds a FunctionNode's parameters
    from state: without the default, that node's silence surfaces here as a
    parameter-binding ``ValueError`` naming this function, which reads as a
    graph defect rather than as what it is. A model returns nothing when its
    completion is truncated at ``max_output_tokens`` — reasoning tokens are
    spent against that same cap, so the critic, which rules on every draft in
    one pass, is the node that hits it first. Absent output is output that
    dropped every draft, so it takes the ``revise`` edge the graph already has:
    one bounded re-ask, then ``critic_failed`` raises the ``CriticOutputError``
    that names what did not reconcile.
    """
    model = SystemModel.model_validate(valid_model)
    drafts = [DraftThreat.model_validate(draft) for draft in merged_drafts]
    ruled = _threats_of(reviewed_threats)
    rulings = [ThreatRuling.model_validate(ruling) for ruling in ruled]
    problems = review_issues(drafts, rulings, model)
    if problems:
        ctx.state[STATE_PREVIOUS_REVIEW] = render(ruled)
        ctx.state[STATE_CRITIC_ISSUES] = render(problems.messages)
        # The roster is the covering set the re-ask must reproduce, and an ID is
        # the whole of that claim — the re-ask reproduces rulings, not drafts.
        # Only the drafts it cannot fix without reading travel in full.
        ctx.state[STATE_DRAFT_ROSTER] = render([draft.id for draft in drafts])
        ctx.state[STATE_UNRECONCILED_DRAFTS] = render(
            _ruling_view([draft for draft in drafts if draft.id in problems.implicated])
        )
        return Event(route=ROUTE_REVISE, output={"issue_count": len(problems.messages)})
    return Event(route=ROUTE_ACCEPT, output={"reviewed_count": len(rulings)})


def fail_review(
    valid_model: dict, merged_drafts: list, reviewed_threats: dict | None = None
) -> dict:
    """Terminal node: the critic re-ask still did not reconcile, so the job fails.

    Reached only on the ``revise`` edge out of ``rereview``, which means the
    check found problems on the second look. Raising propagates out of the
    runner as a failed job — not a *rejected* one: rejection means the input
    failed the validity gate and carries ``ValidationIssue``s, whereas a critic
    that will not return its own drafts whole is our defect and has none.

    ``reviewed_threats`` defaults for the reason :func:`route_review` gives, and
    most of all here: this is the node a silent critic *reaches*, so without the
    default the run would die on parameter binding at the one place built to
    report why it died.
    """
    model = SystemModel.model_validate(valid_model)
    drafts = [DraftThreat.model_validate(draft) for draft in merged_drafts]
    rulings = [
        ThreatRuling.model_validate(ruling) for ruling in _threats_of(reviewed_threats)
    ]
    # review_issues is non-empty here by construction; assemble_threats raises
    # the CriticOutputError naming exactly what still does not reconcile.
    assemble_threats(drafts, rulings, model)
    raise AssertionError("fail_review reached on reconciled critic output")


def assemble_report(
    valid_model: dict,
    merged_drafts: list,
    ctx,
    reviewed_threats: dict | None = None,
    unverified_grounds: list | None = None,
) -> dict[str, Any]:
    """Build the report body deterministically from the critic's rulings.

    Reached only on the ``accept`` edge, so the mechanical check has already
    passed in :func:`route_review`. :func:`assemble_threats` re-runs it and
    fails closed regardless — nothing reaches the report on output that did
    not survive the gate — then splits the ruled threats into the actionable
    and rejected arrays by verdict rather than any model's say-so.

    ``reviewed_threats`` defaults to keep the three readers of that key spelled
    the same way. Nothing can route here without it — an absent ruling drops
    every draft, and dropped drafts are ``revise`` — so the default is
    unreachable rather than a fallback this node relies on.

    ``unverified_grounds`` defaults because ``merge`` writes an empty list when
    nothing failed and when the job carried no sources to check against, and an
    empty list is indistinguishable from the absent key ADK would bind here.
    Both mean the same thing: no quote was found wanting.
    """
    model = SystemModel.model_validate(valid_model)
    drafts = [DraftThreat.model_validate(draft) for draft in merged_drafts]
    rulings = [
        ThreatRuling.model_validate(ruling) for ruling in _threats_of(reviewed_threats)
    ]
    threats, rejected = assemble_threats(drafts, rulings, model)
    analysis = Analysis(
        system_model=model,
        boundary_crossings=model.boundary_crossings(),
        threats=threats,
        rejected_threats=rejected,
        unverified_grounds=[
            UnverifiedGround.model_validate(mark) for mark in unverified_grounds or []
        ],
        summary=build_summary(threats, rejected, model),
    )
    ctx.state[STATE_ANALYSIS] = analysis.to_state()
    return {"threat_count": len(threats), "rejected_count": len(rejected)}


# --- Assembly ---------------------------------------------------------------


@dataclass(frozen=True)
class Pipeline:
    """The built graph plus the static provenance every run stamps.

    ``node_models`` is the *configured* route each LLM node was bound to
    (``vertex_ai/gemini-2.5-pro``) — what the run asked for, which the report
    records as ``requested_model``. Deterministic nodes carry none.

    ``tier_sampling`` is the resolved per-tier clear block the report records
    once per tier; ``node_sampling`` is the same values keyed by graph node, so
    a fingerprint can be computed for a node without re-walking the tier map.

    There is deliberately **no** ``node_fingerprints`` here. A fingerprint's
    model half is the *served* build, which is only known once a node has
    actually run and answered, so it is computed per node *execution* in
    :mod:`stride_service.pipeline` rather than once at build time.
    """

    workflow: Workflow
    node_models: dict[str, str]
    tier_sampling: dict[str, TierSampling]
    node_sampling: dict[str, TierSampling]


def _generate_content_config(
    sampling: TierSampling, resilience: ResilienceConfig | None
) -> types.GenerateContentConfig:
    """One node's tier sampling and the per-request timeout, composed together.

    ``sampling`` is the node's *own* tier's decoding params:
    ``resolve_sampling`` hands each node its :class:`TierSampling`, so nodes on
    different tiers never share one graph-wide constant. The timeout rides on
    ``http_options``, owned by ``config/resilience.toml`` and never sourced
    from sampling. ``resilience`` is optional only so the offline stand-ins,
    whose fakes never read a deadline, can build the graph without a config.
    """
    config = sampling.to_generate_content_config()
    if resilience is not None:
        config.http_options = resilience.to_http_options()
    return config


def _llm_node(
    *,
    name: str,
    instruction: str,
    output_schema: Any,
    output_key: str,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
    resilience: ResilienceConfig | None,
) -> LlmAgent:
    """One LLM node: its model, its full instruction, its emitted schema.

    ``include_contents='none'`` is set explicitly: a node sees its instruction
    and the state templated into it, never the transcript of the nodes before
    it. Model *and* sampling are resolved off the one canonical node name
    (:data:`TIER_NODE_BY_GRAPH_NODE`), so each node runs on its own tier's
    decoding params from the config shared with the eval suite — no node on
    library defaults, none on another tier's sampling. The request deadline
    comes from the resilience config.
    """
    tier_node = TIER_NODE_BY_GRAPH_NODE[name]
    return LlmAgent(
        name=name,
        model=resolve_model(tier_node),
        instruction=instruction,
        output_schema=output_schema,
        output_key=output_key,
        include_contents="none",
        generate_content_config=_generate_content_config(
            resolve_sampling(tier_node), resilience
        ),
    )


def _extract_node(
    prompt_loader: MarkdownLoader,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
    resilience: ResilienceConfig | None,
) -> LlmAgent:
    """The extraction node, shared by the production graph and eval mode 1."""
    return _llm_node(
        name=EXTRACT_NODE,
        instruction=compose_extract_prompt(prompt_loader),
        output_schema=SystemModel,
        output_key=STATE_EXTRACTED_MODEL,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
        resilience=resilience,
    )


def _instruction(skills: str, prompt: str) -> str:
    """Skill text then prompt text: what to know, then what to do with it.

    Stable-first order, so the six category agents and every job for one category
    share the longest possible cacheable prefix (tickets 006 and 013).
    """
    return f"{skills.strip()}\n\n{prompt.strip()}\n"


def analyze_instruction(
    skill_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    category: StrideCategory,
    domain_packs: Sequence[str] = (),
) -> str:
    """One category agent's full instruction, with ``{category}`` already filled in.

    ``{category}`` is the one placeholder resolved here rather than by ADK:
    the six category agents run in parallel against a single session state,
    which cannot hold six different values for one key. The job-varying
    placeholders stay for ADK to template.
    """
    skills = compose_analyze_skills(skill_loader, category, tuple(domain_packs))
    prompt = compose_analyze_prompt(prompt_loader, category)
    return _instruction(skills, prompt.replace("{category}", category))


def recritic_instruction(
    skill_loader: MarkdownLoader, prompt_loader: MarkdownLoader
) -> str:
    """The critic re-ask instruction: the critic's own skills, the re-ask prompt.

    Reuses :func:`compose_critic_skills` byte-for-byte, so the re-ask reads
    the same severity rubric and lane definitions the critic did — it may have
    to re-rule a draft it dropped — and shares that cacheable prefix with the
    critic across jobs.
    """
    return _instruction(
        compose_critic_skills(skill_loader), compose_recritic_prompt(prompt_loader)
    )


def build_pipeline(
    *,
    skill_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    binding: NodeBinding,
    domain_packs: Sequence[str] = (),
    entry: Entry = ENTRY_EXTRACT,
    name: str = "stride_pipeline",
) -> Pipeline:
    """Wire the whole graph: prompts, skills, and models onto the topology.

    ``binding`` is everything an LLM node runs on — which model, which tier's
    decoding params, and the per-request deadline — as one value, because two
    of its fields are views of the same
    :class:`~stride_service.sampling.SamplingConfig` and sourcing them from
    different ones would leave every node running on params the report does not
    attest to. See :class:`~stride_service.binding.NodeBinding`.

    ``entry`` selects where the graph starts. ``"extract"`` is production and
    the end-to-end eval mode. ``"prepare"`` is the **analysis** eval mode: a
    blessed System Model is seeded at :data:`STATE_VALID_MODEL` and the
    extraction half is left out entirely, so threat numbers are attributable to
    the category agents and critic rather than to an element ``extract`` never
    produced. It is a parameter here, not a second topology in the eval tree,
    because two definitions of the same graph drift.
    """
    if entry not in (ENTRY_EXTRACT, ENTRY_PREPARE, ENTRY_EXTRACT_ONLY):
        raise ValueError(f"unknown graph entry point: {entry!r}")

    resolve_model = binding.resolve_model
    resolve_sampling = binding.resolve_sampling
    resilience = binding.resilience

    if entry == ENTRY_EXTRACT_ONLY:
        extract = _extract_node(
            prompt_loader, resolve_model, resolve_sampling, resilience
        )
        return Pipeline(
            workflow=Workflow(name=name, edges=[(START, extract)]),
            node_models={extract.name: _model_name(extract.model)},
            tier_sampling=dict(binding.tier_sampling),
            node_sampling=_node_sampling([extract], resolve_sampling),
        )

    critic = _llm_node(
        name=CRITIC_NODE,
        instruction=_instruction(
            compose_critic_skills(skill_loader), compose_critic_prompt(prompt_loader)
        ),
        output_schema=ThreatRulings,
        output_key=STATE_REVIEWED_THREATS,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
        resilience=resilience,
    )
    recritic = _llm_node(
        name=RECRITIC_NODE,
        instruction=recritic_instruction(skill_loader, prompt_loader),
        output_schema=ThreatRulings,
        output_key=STATE_REVIEWED_THREATS,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
        resilience=resilience,
    )
    agents = [
        _llm_node(
            name=analyze_node_name(category),
            instruction=analyze_instruction(
                skill_loader, prompt_loader, category, domain_packs
            ),
            output_schema=DraftThreats,
            output_key=analyze_state_key(category),
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
            resilience=resilience,
        )
        for category in STRIDE_CATEGORIES
    ]

    prepare = FunctionNode(func=prepare_analysis, name=PREPARE_NODE)
    join = JoinNode(name=JOIN_NODE)
    merge = FunctionNode(func=merge_drafts, name=MERGE_NODE)
    router = FunctionNode(func=route_review, name=ROUTER_NODE)
    rereview = FunctionNode(func=route_review, name=REREVIEW_NODE)
    critic_failed = FunctionNode(func=fail_review, name=CRITIC_FAILED_NODE)
    assemble = FunctionNode(func=assemble_report, name=ASSEMBLE_NODE)

    extraction_nodes: list[LlmAgent] = []
    if entry == ENTRY_EXTRACT:
        extract = _extract_node(
            prompt_loader, resolve_model, resolve_sampling, resilience
        )
        repair = _llm_node(
            name=REPAIR_NODE,
            instruction=compose_repair_prompt(prompt_loader),
            output_schema=SystemModel,
            output_key=STATE_EXTRACTED_MODEL,
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
            resilience=resilience,
        )
        validate = FunctionNode(func=validate_extraction, name=VALIDATE_NODE)
        revalidate = FunctionNode(func=validate_extraction, name=REVALIDATE_NODE)
        reject = FunctionNode(func=reject_model, name=REJECT_NODE)
        extraction_nodes = [extract, repair]
        head_edges = [
            (START, extract, validate),
            (validate, {ROUTE_VALID: prepare, ROUTE_INVALID: repair}),
            (repair, revalidate),
            (revalidate, {ROUTE_VALID: prepare, ROUTE_INVALID: reject}),
        ]
    else:
        head_edges = [(START, prepare)]

    workflow = Workflow(
        name=name,
        edges=[
            *head_edges,
            (prepare, tuple(agents)),
            *((agent, join) for agent in agents),
            (join, merge, critic, router),
            (router, {ROUTE_ACCEPT: assemble, ROUTE_REVISE: recritic}),
            (recritic, rereview),
            (rereview, {ROUTE_ACCEPT: assemble, ROUTE_REVISE: critic_failed}),
        ],
    )
    llm_nodes = [*extraction_nodes, *agents, critic, recritic]
    return Pipeline(
        workflow=workflow,
        node_models={node.name: _model_name(node.model) for node in llm_nodes},
        tier_sampling=dict(binding.tier_sampling),
        node_sampling=_node_sampling(llm_nodes, resolve_sampling),
    )


def _model_name(model: str | BaseLlm) -> str:
    """The model string to record, however the node was bound to it."""
    return model if isinstance(model, str) else model.model


def _node_sampling(
    llm_nodes: Sequence[LlmAgent], resolve_sampling: SamplingResolver
) -> dict[str, TierSampling]:
    """Each LLM node's resolved tier sampling, keyed by graph node name.

    The same ``resolve_sampling`` the graph binds onto the nodes themselves, so
    the params a node runs on and the params its fingerprint attests to can
    never describe different generations. The served model — the fingerprint's
    other half — is not known until the node answers, so the hash itself is
    computed per execution rather than here.
    """
    return {
        node.name: resolve_sampling(TIER_NODE_BY_GRAPH_NODE[node.name])
        for node in llm_nodes
    }


def rejection_issues(rendered: str) -> list[ValidationIssue]:
    """Parse the rejection the graph parked in state back into issues."""
    return [ValidationIssue.model_validate(issue) for issue in json.loads(rendered)]
