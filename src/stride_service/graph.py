"""The ADK Workflow graph: nodes wired to prompts, skills, and models.

Assembles the topology decided in ticket 004 out of the pieces the earlier
implementation tickets shipped::

    START -> extract -> validate -+-valid--> prepare -> 6 analysts -> join
                                  |             ^                       |
                                  +-invalid-> repair                    v
                                                 |                    merge
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

Six nodes carry names ticket 004's sketch does not spell out, and each is
structural rather than a new decision:

* ``revalidate`` is the second run of the *same* validate function after the
  one repair pass. Two nodes instead of a back-edge because the repair
  budget is exactly one: the graph cannot loop, so it cannot spend a second
  pass, and "one repair then reject" is visible in the topology rather than
  enforced by a counter.
* ``reject`` is where the second failure lands — a terminal node that parks
  the validator's issues in state for the runner to return as a rejection.
* ``merge`` runs :func:`stride_service.critic.join_drafts` behind ADK's
  ``JoinNode``, which is a pure barrier with no user code of its own.
* ``router`` and ``rereview`` are the critic's ``validate``/``revalidate``
  (ticket 038 decision 3): one ``route_review`` function run twice, moving the
  mechanical check *out* of ``assemble`` so a malformed critic output can be
  re-asked before assembly. Clean output accepts to ``assemble``; a malformed
  one revises — to ``recritic`` the first time, to ``critic_failed`` the
  second.
* ``recritic`` is the one bounded critic re-ask, the ``repair`` of the review
  half. A structural pass, not a counted one — the graph cannot loop back for
  a third.
* ``critic_failed`` is where a still-malformed re-ask lands: it *raises*
  rather than parking issues, because a critic that will not return its own
  drafts whole is our defect (a ``failed`` job), not the input's (a
  ``rejected`` one, which carries ``ValidationIssue``s).

Every LLM node binds its model through the caller's ``resolve_model`` (the
canonical names in :data:`stride_service.model_tiers.LLM_NODES`), its skills
through :mod:`stride_service.skills`, and its prompt through
:mod:`stride_service.prompts`. Graph node names must be Python identifiers,
so ``analyst/information-disclosure`` in the tier config is
``analyst_information_disclosure`` here; :data:`TIER_NODE_BY_GRAPH_NODE` is
the only place that correspondence lives.

The bookends are deliberately deterministic (ticket 004, and the standing
principle that mechanical work belongs in code): analysts cannot receive a
malformed view, and the report cannot cite an element the model does not
contain. Every check in this module fails closed — a raising FunctionNode
aborts the workflow, which the runner turns into a failed job.

Security: the submitted text is untrusted and reaches the extraction prompt
inside a fenced block that names it as data (OWASP LLM01); everything a
model emits is untrusted output validated before use (LLM05) — by
``output_schema`` at the node boundary, then by the System Model gate and
the critic seams here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import START, FunctionNode, JoinNode, Workflow
from google.genai import types

from stride_service.critic import assemble_threats, join_drafts, review_issues
from stride_service.markdown_loader import MarkdownLoader
from stride_service.prompts import (
    compose_analyst_prompt,
    compose_critic_prompt,
    compose_extract_prompt,
    compose_recritic_prompt,
    compose_repair_prompt,
)
from stride_service.resilience import ResilienceConfig
from stride_service.report import (
    STRIDE_CATEGORIES,
    DraftThreat,
    StrideCategory,
    Summary,
    Threat,
    build_summary,
)
from stride_service.sampling import (
    SamplingResolver,
    TierSampling,
    sampling_fingerprint,
)
from stride_service.skills import compose_analyst_skills, compose_critic_skills
from stride_service.system_model import BoundaryCrossing, SystemModel
from stride_service.validation import ValidationIssue, parse_and_validate

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


def analyst_node_name(category: StrideCategory) -> str:
    """This category analyst's graph node name (an identifier, unlike the ID)."""
    return f"analyst_{category.replace('-', '_')}"


ANALYST_GRAPH_NODES: tuple[str, ...] = tuple(
    analyst_node_name(category) for category in STRIDE_CATEGORIES
)

# Graph node name -> the canonical LLM node name the tier config keys on.
TIER_NODE_BY_GRAPH_NODE: dict[str, str] = {
    EXTRACT_NODE: "extract",
    REPAIR_NODE: "repair",
    CRITIC_NODE: "critic",
    RECRITIC_NODE: "recritic",
    **{
        analyst_node_name(category): f"analyst/{category}"
        for category in STRIDE_CATEGORIES
    },
}

# --- Routes -----------------------------------------------------------------

Entry = Literal["extract", "prepare", "extract-only"]

ENTRY_EXTRACT: Entry = "extract"
ENTRY_PREPARE: Entry = "prepare"
"""The analysis eval mode's entry (ticket 009 decision 1): start at
``prepare`` over a blessed model seeded in state, so a recall miss cannot be
blamed on an element ``extract`` never produced."""

ENTRY_EXTRACT_ONLY: Entry = "extract-only"
"""The extraction eval mode: run ``extract`` and stop, leaving its emission at
:data:`STATE_EXTRACTED_MODEL` for the caller to put through the same
:func:`~stride_service.validation.parse_and_validate` gate ``validate`` uses.
Spending six analysts and a critic to score an extraction would be six kinds
of noise on one number."""

ROUTE_VALID = "valid"
ROUTE_INVALID = "invalid"
ROUTE_ACCEPT = "accept"
ROUTE_REVISE = "revise"
"""Reserved by ticket 004, now the critic re-ask route (ticket 038 decision
3). ``route_review`` takes it when the critic's output fails the mechanical
check: from ``router`` it reaches the bounded ``recritic`` re-ask, and from
``rereview`` — the second look after that re-ask — it reaches
``critic_failed``, since a repeated failure is ours to own, not the input's to
be rejected for."""

# --- State keys -------------------------------------------------------------
#
# The six keys the prompt files template against carry *rendered* text, since
# ADK substitutes ``str(value)`` into an instruction. The structured values
# the FunctionNodes pass between themselves live under their own keys.
#
# Two key families, and the invariant that keeps them honest (ticket 010):
# *structured* keys are the code's view (Pydantic round-trips), *rendered*
# keys are the model's view (:func:`render` output). Both copies of an
# artifact are kept on purpose — reading back exactly the bytes a model saw is
# what makes a failed job debuggable — so the rule that stops them drifting
# is: **a rendered key is written once by the FunctionNode that derives it,
# and never read by Python.** No node mutates an artifact after rendering it.
# A future node that re-renders or edits one of these in place breaks the
# report's traceability without failing any test.

STATE_INPUT_TEXT = "input_text"
STATE_SYSTEM_MODEL = "system_model"
STATE_BOUNDARY_CROSSINGS = "boundary_crossings"
STATE_DRAFT_THREATS = "draft_threats"
STATE_PREVIOUS_MODEL = "previous_model"
STATE_VALIDATION_ISSUES = "validation_issues"
STATE_PREVIOUS_REVIEW = "previous_review"
STATE_CRITIC_ISSUES = "critic_issues"

STATE_EXTRACTED_MODEL = "extracted_model"
STATE_VALID_MODEL = "valid_model"
STATE_MERGED_DRAFTS = "merged_drafts"
STATE_REVIEWED_THREATS = "reviewed_threats"
STATE_ANALYSIS = "analysis"
STATE_REJECTION = "rejection"


def analyst_state_key(category: StrideCategory) -> str:
    """Where one analyst parks its drafts for the merge node."""
    return f"drafts_{category.replace('-', '_')}"


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
            summary=Summary.model_validate(data["summary"]),
        )


def render(value: Any) -> str:
    """The one way a structured value enters a prompt: pretty, stable JSON."""
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


# --- Deterministic node functions -------------------------------------------
#
# Plain functions, wrapped in FunctionNodes below. Parameters bind by name
# from session state; ``ctx`` is ADK's node context, whose ``state`` writes
# become the session's state delta.


def validate_extraction(extracted_model: dict, ctx) -> Event:
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
    """
    model, issues = parse_and_validate(extracted_model, normalize_ids=True)
    if issues or model is None:
        parked = extracted_model if model is None else model.model_dump(mode="json")
        ctx.state[STATE_PREVIOUS_MODEL] = render(parked)
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


def prepare_analysis(valid_model: dict, ctx) -> dict[str, Any]:
    """Derive boundary crossings and render the analysts' shared view.

    Crossings are computed here rather than extracted, so no analyst can be
    handed a crossing that contradicts the zones in the model it is reading.
    """
    model = SystemModel.model_validate(valid_model)
    crossings = model.boundary_crossings()
    ctx.state[STATE_SYSTEM_MODEL] = render(valid_model)
    ctx.state[STATE_BOUNDARY_CROSSINGS] = render(
        [crossing.model_dump(mode="json") for crossing in crossings]
    )
    return {"element_count": len(model.elements()), "crossing_count": len(crossings)}


def merge_drafts(valid_model: dict, ctx) -> dict[str, Any]:
    """Merge the six analysts' drafts into the single list the critic sees.

    The mechanical half of the fan-in: :func:`join_drafts` fails closed if a
    draft cites an element the model does not contain or two analysts reused
    a threat ID, so the critic spends judgement on evidence and lanes only.
    """
    model = SystemModel.model_validate(valid_model)
    drafts_by_category = {
        category: [
            DraftThreat.model_validate(draft)
            for draft in ctx.state.get(analyst_state_key(category), [])
        ]
        for category in STRIDE_CATEGORIES
    }
    merged = join_drafts(drafts_by_category, model)
    ctx.state[STATE_MERGED_DRAFTS] = [draft.model_dump(mode="json") for draft in merged]
    ctx.state[STATE_DRAFT_THREATS] = render(
        [draft.model_dump(mode="json") for draft in merged]
    )
    return {"draft_count": len(merged)}


def route_review(
    valid_model: dict, merged_drafts: list, reviewed_threats: list, ctx
) -> Event:
    """Run the mechanical check on the critic's output and route on the result.

    The check that used to live in :func:`assemble_report` moved here (ticket
    038 decision 3): to re-ask the critic when its output is malformed, the
    graph has to decide *before* assembly whether it is malformed. Clean
    output routes to ``accept``; a critic that dropped, invented, duplicated
    or mis-referenced a threat routes to ``revise``, with the failing ruling
    and the problem list parked where the re-ask prompt reads them.

    Both review nodes run this same function — what differs is where their
    ``revise`` edge points (``recritic`` for the first look, ``critic_failed``
    for the second), exactly as the two validate nodes share one function.
    """
    model = SystemModel.model_validate(valid_model)
    drafts = [DraftThreat.model_validate(draft) for draft in merged_drafts]
    reviewed = [Threat.model_validate(threat) for threat in reviewed_threats]
    issues = review_issues(drafts, reviewed, model)
    if issues:
        ctx.state[STATE_PREVIOUS_REVIEW] = render(reviewed_threats)
        ctx.state[STATE_CRITIC_ISSUES] = render(issues)
        return Event(route=ROUTE_REVISE, output={"issue_count": len(issues)})
    return Event(route=ROUTE_ACCEPT, output={"reviewed_count": len(reviewed)})


def fail_review(valid_model: dict, merged_drafts: list, reviewed_threats: list) -> dict:
    """Terminal node: the critic re-ask still did not reconcile, so the job fails.

    Reached only on the ``revise`` edge out of ``rereview``, which means the
    check found problems on the second look. Raising propagates out of the
    runner as a failed job — not a *rejected* one: rejection means the input
    failed the validity gate and carries ``ValidationIssue``s, whereas a
    critic that will not return its own drafts whole is our defect and has
    none (ticket 038 decision 3, holding the ticket-008 contract).
    """
    model = SystemModel.model_validate(valid_model)
    drafts = [DraftThreat.model_validate(draft) for draft in merged_drafts]
    reviewed = [Threat.model_validate(threat) for threat in reviewed_threats]
    # review_issues is non-empty here by construction; assemble_threats raises
    # the CriticOutputError naming exactly what still does not reconcile.
    assemble_threats(drafts, reviewed, model)
    raise AssertionError("fail_review reached on reconciled critic output")


def assemble_report(
    valid_model: dict, merged_drafts: list, reviewed_threats: list, ctx
) -> dict[str, Any]:
    """Build the report body deterministically from the critic's rulings.

    Reached only on the ``accept`` edge, so the mechanical check has already
    passed in :func:`route_review`. :func:`assemble_threats` re-runs it and
    fails closed regardless — nothing reaches the report on output that did
    not survive the gate — then splits the ruled threats into the actionable
    and rejected arrays by verdict rather than any model's say-so.
    """
    model = SystemModel.model_validate(valid_model)
    drafts = [DraftThreat.model_validate(draft) for draft in merged_drafts]
    reviewed = [Threat.model_validate(threat) for threat in reviewed_threats]
    threats, rejected = assemble_threats(drafts, reviewed, model)
    analysis = Analysis(
        system_model=model,
        boundary_crossings=model.boundary_crossings(),
        threats=threats,
        rejected_threats=rejected,
        summary=build_summary(threats, rejected, model),
    )
    ctx.state[STATE_ANALYSIS] = analysis.to_state()
    return {"threat_count": len(threats), "rejected_count": len(rejected)}


# --- Assembly ---------------------------------------------------------------


@dataclass(frozen=True)
class Pipeline:
    """The built graph plus the static provenance every run stamps.

    ``node_models`` is what the report's ``nodes`` array records: a run has to
    be able to say which model produced it, and deterministic nodes carry none.
    ``tier_sampling`` and ``node_fingerprints`` are the sampling provenance
    (ticket 07): the resolved per-tier clear block the report records once per
    tier, and each LLM node's ``sha256(served model, tier sampling)``
    generation-identity hash. All three are config-derived and fixed for the
    life of the pipeline, computed once here and copied into each report.
    """

    workflow: Workflow
    node_models: dict[str, str]
    tier_sampling: dict[str, TierSampling]
    node_fingerprints: dict[str, str]


def _generate_content_config(
    sampling: TierSampling, resilience: ResilienceConfig | None
) -> types.GenerateContentConfig:
    """One node's tier sampling and the per-request timeout, composed together.

    ``sampling`` is the node's *own* tier's decoding params (ticket 06):
    ``resolve_sampling`` hands each node its :class:`TierSampling`, so flash and
    pro nodes no longer share a graph-wide constant. The timeout rides on
    ``http_options`` (ticket 038 decision 4), which stays owned by
    ``config/resilience.toml`` — never sourced from sampling (ticket 03).
    ``resilience`` is optional only so the offline stand-ins, whose fakes never
    read a deadline, can build the graph without a config.
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

    ``include_contents='none'`` is set explicitly (ticket 002): a node sees
    its instruction and the state templated into it, never the transcript of
    the nodes before it. Model *and* sampling are resolved off the one
    canonical node name (:data:`TIER_NODE_BY_GRAPH_NODE`), so each node runs on
    its own tier's decoding params from the config shared with the eval suite
    (ticket 009 decision 15) — no node on library defaults, none on another
    tier's sampling. The request deadline comes from the resilience config
    (ticket 038).
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

    Stable-first order, so the six analysts and every job for one category
    share the longest possible cacheable prefix (tickets 006 and 013).
    """
    return f"{skills.strip()}\n\n{prompt.strip()}\n"


def analyst_instruction(
    skill_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    category: StrideCategory,
    domain_packs: Sequence[str] = (),
) -> str:
    """One analyst's full instruction, with ``{category}`` already filled in.

    ``{category}`` is the one placeholder resolved here rather than by ADK:
    the six analysts run in parallel against a single session state, which
    cannot hold six different values for one key. The job-varying
    placeholders stay for ADK to template.
    """
    skills = compose_analyst_skills(skill_loader, category, tuple(domain_packs))
    prompt = compose_analyst_prompt(prompt_loader, category)
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
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
    tier_sampling: Mapping[str, TierSampling],
    resilience: ResilienceConfig | None = None,
    domain_packs: Sequence[str] = (),
    entry: Entry = ENTRY_EXTRACT,
    name: str = "stride_pipeline",
) -> Pipeline:
    """Wire the whole graph: prompts, skills, and models onto the topology.

    ``resolve_model`` takes a canonical LLM node name and returns the model
    to bind — :meth:`ModelTierConfig.resolve_model` in production, so a node
    silently running on the wrong tier stays impossible. ``resolve_sampling``
    is its sibling (ticket 06): the same canonical name in, the node's tier
    decoding params out — :func:`~stride_service.sampling.make_resolve_sampling`
    in production, so each node gets its own tier's ``GenerateContentConfig``.
    ``tier_sampling`` is the resolved per-tier clear block (``SamplingConfig``'s
    ``tiers``) the report stamps once per tier for provenance (ticket 07); each
    LLM node's fingerprint is derived here from its served model and its tier's
    resolved sampling.

    ``resilience`` binds the per-request timeout onto every LLM node (ticket
    038). It is optional so the offline stand-ins can build the graph without
    a config; production always passes one, and the client-level retry is
    bound separately by the caller's ``resolve_model``.

    ``entry`` selects where the graph starts. ``"extract"`` is production and
    the end-to-end eval mode. ``"prepare"`` is the **analysis** eval mode of
    ticket 009 decision 1: a blessed System Model is seeded at
    :data:`STATE_VALID_MODEL` and the extraction half is left out entirely,
    so threat numbers are attributable to the analysts and critic rather than
    to an element ``extract`` never produced. It is a parameter here, not a
    second topology in the eval tree, because two definitions of the same
    graph drift.
    """
    if entry not in (ENTRY_EXTRACT, ENTRY_PREPARE, ENTRY_EXTRACT_ONLY):
        raise ValueError(f"unknown graph entry point: {entry!r}")

    if entry == ENTRY_EXTRACT_ONLY:
        extract = _extract_node(
            prompt_loader, resolve_model, resolve_sampling, resilience
        )
        return Pipeline(
            workflow=Workflow(name=name, edges=[(START, extract)]),
            node_models={extract.name: _model_name(extract.model)},
            tier_sampling=dict(tier_sampling),
            node_fingerprints=_node_fingerprints([extract], resolve_sampling),
        )

    critic = _llm_node(
        name=CRITIC_NODE,
        instruction=_instruction(
            compose_critic_skills(skill_loader), compose_critic_prompt(prompt_loader)
        ),
        output_schema=list[Threat],
        output_key=STATE_REVIEWED_THREATS,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
        resilience=resilience,
    )
    recritic = _llm_node(
        name=RECRITIC_NODE,
        instruction=recritic_instruction(skill_loader, prompt_loader),
        output_schema=list[Threat],
        output_key=STATE_REVIEWED_THREATS,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
        resilience=resilience,
    )
    analysts = [
        _llm_node(
            name=analyst_node_name(category),
            instruction=analyst_instruction(
                skill_loader, prompt_loader, category, domain_packs
            ),
            output_schema=list[DraftThreat],
            output_key=analyst_state_key(category),
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
            (prepare, tuple(analysts)),
            *((analyst, join) for analyst in analysts),
            (join, merge, critic, router),
            (router, {ROUTE_ACCEPT: assemble, ROUTE_REVISE: recritic}),
            (recritic, rereview),
            (rereview, {ROUTE_ACCEPT: assemble, ROUTE_REVISE: critic_failed}),
        ],
    )
    llm_nodes = [*extraction_nodes, *analysts, critic, recritic]
    return Pipeline(
        workflow=workflow,
        node_models={node.name: _model_name(node.model) for node in llm_nodes},
        tier_sampling=dict(tier_sampling),
        node_fingerprints=_node_fingerprints(llm_nodes, resolve_sampling),
    )


def _model_name(model: str | BaseLlm) -> str:
    """The model string to record, however the node was bound to it."""
    return model if isinstance(model, str) else model.model


def _node_fingerprints(
    llm_nodes: Sequence[LlmAgent], resolve_sampling: SamplingResolver
) -> dict[str, str]:
    """Each LLM node's ``sha256(served model, tier sampling)`` (ticket 07).

    Computed from the very ``node_models`` source and ``resolve_sampling`` the
    graph binds, so a node's recorded served model and its fingerprint can never
    describe different generations.
    """
    return {
        node.name: sampling_fingerprint(
            _model_name(node.model),
            resolve_sampling(TIER_NODE_BY_GRAPH_NODE[node.name]),
        )
        for node in llm_nodes
    }


def rejection_issues(rendered: str) -> list[ValidationIssue]:
    """Parse the rejection the graph parked in state back into issues."""
    return [ValidationIssue.model_validate(issue) for issue in json.loads(rendered)]
