"""The ADK Workflow graph: nodes wired to prompts, skills, and models.

The topology, for a job selecting frameworks F1..Fn::

    START -> extract -> validate -+-valid--> prepare --+--> F1 subgraph --+
                                  |             ^      |                  |
                                  +-invalid-> repair   +--> ... ----------+
                                                 |     |                  |
                                          revalidate   +--> Fn subgraph --+
                                              |                           |
                                    invalid-> reject                      v
                                                                       assemble

and one subgraph per framework, all of them converging on the one ``assemble``::

    prepare -> lane agents -> join_<F> -> merge_<F> -> critic_<F> -> router_<F>
                                                                        |
                                assemble <-------------------accept-----+
                                   ^  ^                                 |
                                   |  |                          revise v
                                   |  +--accept-- rereview_<F> <- recritic_<F>
                                   |                  |
                                   |           revise v
                                   +---(none)  critic_failed_<F> (raises)

**The shared half runs once and the framework half runs N times**, which is
#162's ruling drawn as a graph: one extraction, one validity gate, one prepared
view and one assembly, because one **Valid System Model** serves every framework
a job selects. Everything between fans out per framework, because a critic rules
its own framework's drafts against its own framework's question, and no node
between ``prepare`` and ``assemble`` ever sees two frameworks' claims together.

A graph is built for one selection. The node names, the state keys and the
instruction digest are all functions of it, so a graph carrying nodes a job did
not select would leave them unfired and their keys unwritten.

Six of the per-framework nodes are structural rather than analytical:

* ``revalidate`` is the second run of the *same* validate function after the
  one repair pass. Two nodes instead of a back-edge because the repair budget
  is exactly one: the graph cannot loop, so it cannot spend a second pass, and
  "one repair then reject" is visible in the topology rather than enforced by a
  counter.
* ``reject`` is where the second failure lands — a terminal node that parks the
  validator's issues in state for the runner to return as a rejection.
* ``join`` is ADK's ``JoinNode``, a pure barrier with no user code of its own;
  ``merge`` runs :func:`stride_service.critic.join_drafts` behind it.
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
:mod:`stride_service.prompts`. Graph node names must be Python identifiers, so
``analyze/stride`` in the tier config is ``analyze_stride_spoofing`` and its five
siblings here; :func:`tier_node_by_graph_node` is the only place that
correspondence lives. The tier config keys one ``analyze/<framework>`` where the
graph builds one node per lane, which is ``model_tiers.toml`` v5's own rule: a
lane is a framework's internal fact and all of them run the same judgement on the
same tier. The graph names carry the framework too, and that is the bump report
schema 3.0 earned — a consumer keying on ``analyze_spoofing`` in ``nodes[].node``
does not error, it matches nothing, silently.

The bookends are deliberately deterministic, because mechanical work belongs in
code: lane agents cannot receive a malformed view, the report cannot cite an
element the model does not contain, and a quote a finding rests on is matched
against the submitter's own bytes rather than taken on trust. Every check in
this module fails closed — a raising FunctionNode aborts the workflow, which
the runner turns into a failed job.

One of those checks is for **silence** rather than for malformed content. An
LLM node that emits no text writes no ``output_key``, so the absence arrives
where the next node reads state, not as anything that raised. ``validate`` and
each framework's ``merge`` name it (:class:`SilentNodeError`) instead of reading
it as an empty value, which is what keeps a truncated lane agent from deleting a
lane from a report that still finishes green.

Security: the submitted text is untrusted and reaches the extraction prompt
**and every lane agent's prompt** inside a fenced block that names it as data
(OWASP LLM01) — the agents read it so they can quote it, which is the whole of
finding-level attribution and is why ``analyze.md`` carries the same
data-not-instruction paragraph ``extract.md`` does. Everything a model emits is
untrusted output validated before use (LLM05) — by ``output_schema`` at the node
boundary, then by the System Model gate and the critic seams here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import START, FunctionNode, JoinNode, Workflow
from google.genai import types

from stride_service.candidates import generate_candidates
from stride_service.coverage import build_coverage, lane_scope
from stride_service.critic import (
    assemble_claims,
    join_drafts,
    review_issues,
)
from stride_service.domains import select_domain_packs
from stride_service.evidence import (
    evidence_catalog,
    render_catalog,
    resolve_proposals,
)
from stride_service.frameworks import (
    DISCLAIMER_DOC,
    SCHEMAS,
    FrameworkPackage,
    FrameworkSchemas,
    package_for,
    schemas_for,
)
from stride_service.knowledge import (
    MAX_CASES,
    MAX_NOTES,
    compose_cases,
    compose_notes,
    select_documents,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import TierName
from stride_service.prompts import (
    compose_analyze_prompt,
    compose_critic_prompt,
    compose_extract_prompt,
    compose_recritic_prompt,
    compose_repair_prompt,
)
from stride_service.report import (
    AnalysisContext,
    AnalysisMarks,
    Claim,
    FrameworkAnalysis,
    FrameworkName,
    InputRef,
    Job,
    LaneCoverage,
    NodeRun,
    Report,
    Ruling,
    SharedElementName,
)
from stride_service.resilience import ResilienceConfig
from stride_service.retry import TRUNCATION_REMEDY
from stride_service.sampling import (
    SamplingResolver,
    TierSampling,
)
from stride_service.skills import (
    compose_critic_skills,
    compose_domain_skills,
    compose_lane_skills,
)
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

logger = logging.getLogger(__name__)

# --- Node names -------------------------------------------------------------

# The shared half of the topology: one extraction, one validity gate, one
# preparation, one assembly. #162 ruled that one **Valid System Model** serves
# every framework a job selects, so nothing here is per-framework.
EXTRACT_NODE = "extract"
VALIDATE_NODE = "validate"
REPAIR_NODE = "repair"
REVALIDATE_NODE = "revalidate"
REJECT_NODE = "reject"
PREPARE_NODE = "prepare"
ASSEMBLE_NODE = "assemble"

# The per-framework half. Each of these is a *role*, and a graph carries one
# node per role per selected framework, named ``<role>_<framework>``; the lane
# agents are named ``analyze_<framework>_<lane>``. See :class:`Lane` and
# :class:`FrameworkNodes`, which are the only places those names are spelled.
JOIN_ROLE = "join"
MERGE_ROLE = "merge"
CRITIC_ROLE = "critic"
ROUTER_ROLE = "router"
RECRITIC_ROLE = "recritic"
REREVIEW_ROLE = "rereview"
CRITIC_FAILED_ROLE = "critic_failed"


# The per-lane artifacts, as ``{placeholder}`` in ``analyze.md`` against the
# state-key prefix it resolves to. **The only place that correspondence
# lives**: :meth:`Lane.prompt_bindings` reads it to substitute, and
# :meth:`Lane.state` reads it to write, so the key an agent's instruction reads
# and the key ``prepare`` wrote cannot be different keys.
#
# One key per artifact per ``(framework, lane)`` rather than one, for the reason
# ``{lane}`` is substituted at build time: the lane agents run in parallel
# against a single session state, which cannot hold N values for one key.
# Handing every agent every lane's material would also spend most of the block on
# other people's leads, and the material genuinely differs per lane — the candidate
# rules that selected the corpus documents fired per lane, and the rule counts
# behind the scope differ per category.
LANE_ARTIFACTS: Mapping[str, str] = MappingProxyType(
    {
        "candidates": "candidates",
        "scope": "scope",
        "reference_notes": "notes",
        "prior_cases": "cases",
    }
)


# The per-framework artifacts a review prompt reads, as ``{placeholder}`` in
# ``critic.md`` and ``recritic.md`` against the state-key artifact it resolves
# to. The same trick :data:`LANE_ARTIFACTS` plays one node earlier and for the
# same reason: N critics run in parallel against one session state, which cannot
# hold N values for one key, so ``{drafts}`` is substituted at build time into
# ``{draft_view_stride}`` and each critic reads only its own framework's.
REVIEW_ARTIFACTS: Mapping[str, str] = MappingProxyType(
    {
        "drafts": "draft_view",
        "previous_review": "previous_review",
        "critic_issues": "critic_issues",
        "draft_roster": "draft_roster",
        "unreconciled_drafts": "unreconciled_drafts",
    }
)


def _identifier(value: str) -> str:
    """One slug as a Python identifier, which a graph node name must be."""
    return value.replace("-", "_")


@dataclass(frozen=True)
class Lane:
    """One lane agent's lane: its graph node, its keys, its prompt bindings.

    The graph runs one of these per ``(framework, lane)`` pair, in parallel, and
    every per-lane name is derived here. That is the point: the write and the
    substitution used to be spelled in three places — a key function, the
    ``prepare`` write, the instruction's replace chain — held together by a
    lint. A lane now hands out both from one declaration.

    **The framework is part of every name.** Two packages may legitimately
    declare a lane of the same name, and a graph carrying both would otherwise
    build two nodes called ``analyze_spoofing`` and hand them one state key to
    fight over. That is also why the report's node names moved with
    ``schema_version`` 3.0: a consumer keying on ``analyze_spoofing`` in
    ``nodes[].node`` does not error, it matches nothing, silently.
    """

    framework: FrameworkName
    lane: str

    @property
    def slug(self) -> str:
        """This pair as an identifier, unique across every carried framework."""
        return f"{_identifier(self.framework)}_{_identifier(self.lane)}"

    @property
    def node_name(self) -> str:
        """This lane agent's graph node name (an identifier, unlike the ID)."""
        return f"analyze_{self.slug}"

    @property
    def drafts_key(self) -> str:
        """Where this lane agent parks its proposals for its framework's merge."""
        return f"drafts_{self.slug}"

    def key(self, artifact: str) -> str:
        """Where ``prepare`` parks one of this lane's inputs."""
        return f"{LANE_ARTIFACTS[artifact]}_{self.slug}"

    @property
    def prompt_bindings(self) -> dict[str, str]:
        """What each ``{placeholder}`` in ``analyze.md`` becomes for this lane.

        ``{lane}`` becomes the lane slug itself. Every other placeholder becomes
        *the name of this lane's state key*, so what ADK templates at run time is
        ``{candidates_stride_spoofing}`` — one prompt file, one binding set per
        lane, no lane reading another's leads.
        """
        return {
            "lane": self.lane,
            **{name: f"{{{self.key(name)}}}" for name in LANE_ARTIFACTS},
        }

    def state(self, artifacts: Mapping[str, str]) -> dict[str, str]:
        """This lane's state entries, given one rendered value per artifact.

        Keyed by placeholder on the way in and by state key on the way out,
        which is the whole translation. Fails closed on a partial set: a
        missing artifact would leave the placeholder ADK templates pointing at
        a key nothing wrote, and that raises at the first LLM call rather than
        here.
        """
        if set(artifacts) != set(LANE_ARTIFACTS):
            missing = sorted(set(LANE_ARTIFACTS) - set(artifacts))
            extra = sorted(set(artifacts) - set(LANE_ARTIFACTS))
            raise ValueError(
                f"lane {self.framework}/{self.lane!r} state:"
                f" missing {missing}, unknown {extra}"
            )
        return {self.key(name): value for name, value in artifacts.items()}


@dataclass(frozen=True)
class FrameworkNodes:
    """One framework's whole half of the graph: its lanes and its six own nodes.

    Everything between ``prepare`` and ``assemble`` is per framework — a lane
    agent per lane, then that framework's own fan-in, critic, router, bounded
    re-ask, second look and failure node — because a critic rules its own
    framework's drafts against its own framework's question. Two frameworks'
    subgraphs never touch: they fan out from the one prepared model and fan back
    in at ``assemble``, which is the only node that sees both.

    Like :class:`Lane`, this is the one place its names are spelled. A node name
    and the state key it writes are derived from the same ``name``, so a graph
    carrying two frameworks cannot cross their wires.
    """

    name: FrameworkName

    @property
    def package(self) -> FrameworkPackage:
        return package_for(self.name)

    @property
    def schemas(self) -> FrameworkSchemas:
        return schemas_for(self.name)

    @property
    def lanes(self) -> tuple[Lane, ...]:
        return tuple(Lane(self.name, lane) for lane in self.package.lanes)

    def node(self, role: str) -> str:
        """This framework's graph node name for one of the six per-framework roles."""
        return f"{role}_{_identifier(self.name)}"

    def key(self, artifact: str) -> str:
        """This framework's own state key for one of its artifacts."""
        return f"{artifact}_{_identifier(self.name)}"

    @property
    def tier_nodes(self) -> dict[str, str]:
        """This framework's graph node names against the tier keys they run on.

        One ``analyze/<framework>`` key covers every lane, which is
        ``model_tiers.toml`` v5's own rule: a lane is a framework's internal
        fact and all of them run the same judgement on the same tier.
        """
        return {
            **{lane.node_name: f"analyze/{self.name}" for lane in self.lanes},
            self.node(CRITIC_ROLE): f"critic/{self.name}",
            self.node(RECRITIC_ROLE): f"recritic/{self.name}",
        }


def analyze_node_name(framework: FrameworkName, lane: str) -> str:
    """One lane agent's graph node name, for a caller holding the pair."""
    return Lane(framework, lane).node_name


def tier_node_by_graph_node(
    frameworks: Sequence[FrameworkName],
) -> dict[str, str]:
    """Graph node name -> the canonical LLM node name the tier config keys on.

    Built per selection rather than as a module constant, because which nodes
    exist is now a function of which frameworks the graph was built for. **The
    only place that correspondence lives.**
    """
    return {
        EXTRACT_NODE: "extract",
        REPAIR_NODE: "repair",
        **{
            node: tier
            for name in frameworks
            for node, tier in FrameworkNodes(name).tier_nodes.items()
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
Spending every framework's lane agents and critics to score an extraction would
be that many kinds of noise on one number."""

ROUTE_VALID = "valid"
ROUTE_INVALID = "invalid"
ROUTE_ACCEPT = "accept"
ROUTE_REVISE = "revise"
"""The critic re-ask route. ``route_review`` takes it when the critic's output
fails the mechanical check: from ``router`` it reaches the bounded ``recritic``
re-ask, and from ``rereview`` — the second look after that re-ask — it reaches
``critic_failed``, since a repeated failure is ours to own, not the input's to
be rejected for."""


def _routed(route: str, output: dict[str, Any]) -> Event:
    """An ADK ``Event`` carrying the route the graph's edges match on.

    ``route=`` is a convenience kwarg ADK's before-validator lifts onto
    ``actions.route``; it is not a declared field, so the type checker cannot
    see it. Routing through one constructor keeps that a single suppression.
    """
    return Event(route=route, output=output)  # type: ignore[call-arg]


# --- State keys -------------------------------------------------------------
#
# The keys the prompt files template against carry *rendered* text, since ADK
# substitutes ``str(value)`` into an instruction. The structured values the
# FunctionNodes pass between themselves live under their own keys.
#
# Two key families, and the invariant that keeps them honest:
# *structured* keys are the code's view (Pydantic round-trips), *rendered*
# keys are the model's view (:func:`render` output). Both copies of an
# artifact are kept on purpose — reading back exactly the bytes a model saw is
# what makes a failed job debuggable — so the rule that stops them drifting
# is: **a rendered key is written once by the FunctionNode that derives it,
# and never read by Python.** No node mutates an artifact after rendering it.
#
# :class:`SessionState` is what holds that rule. Each key is declared into one
# family below, and a node writes through the method for its family; there is
# deliberately no way to read a rendered key back.

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
STATE_EVIDENCE_CATALOG = "evidence_catalog"
# The domain packs this job's model earned, already composed to text. One key
# for every lane of every framework, because the selection is a fact about the
# model rather than about a lane — or about a framework.
STATE_DOMAIN_SKILLS = "domain_skills"
STATE_PREVIOUS_MODEL = "previous_model"
STATE_VALIDATION_ISSUES = "validation_issues"

STATE_EXTRACTED_MODEL = "extracted_model"
STATE_VALID_MODEL = "valid_model"
# What ``prepare`` put in front of every lane of every framework: the packs this
# model earned. Written where the selection happens rather than recomputed at a
# fan-in, because a second derivation could disagree with the one the agents
# actually read — the same reason the evidence catalog is derived once and
# resolved against itself. Neutral, because #162 ruled one extraction and one
# pack selection serve every framework.
STATE_DOMAIN_PACKS = "domain_packs"
STATE_ANALYSIS = "analysis"
STATE_REJECTION = "rejection"

# The per-framework keys, as artifact names. Each is spelled
# ``<artifact>_<framework>`` by :meth:`FrameworkNodes.key`, so two frameworks'
# fan-ins never write one key.
#
# ``drafts`` holds one framework's merged drafts whole, because that is what
# ``assemble_claims`` merges rulings onto; ``draft_view`` is the *prompt* view of
# the same drafts, narrowed by :func:`_ruling_view` to the fields a verdict is
# reached from. ``marks`` is every service-owned mark that fan-in produced, as
# one :class:`~stride_service.report.AnalysisMarks` — one key rather than one per
# mark kind, since they share an owner, a standing and a policy.
FRAMEWORK_RENDERED_ARTIFACTS: tuple[str, ...] = (
    "draft_view",
    "previous_review",
    "critic_issues",
    "draft_roster",
    "unreconciled_drafts",
)
FRAMEWORK_STRUCTURED_ARTIFACTS: tuple[str, ...] = (
    "drafts",
    "coverage",
    "marks",
    "retrieved",
    "reviewed",
)

#: Keys holding bytes a model reads and Python does not. Written once by the
#: node that derives them, then templated into an instruction by ADK.
SHARED_RENDERED_KEYS: frozenset[str] = frozenset(
    {
        STATE_INPUT_TEXT,
        STATE_SYSTEM_MODEL,
        STATE_BOUNDARY_CROSSINGS,
        STATE_EVIDENCE_CATALOG,
        STATE_DOMAIN_SKILLS,
        STATE_PREVIOUS_MODEL,
        STATE_VALIDATION_ISSUES,
    }
)

#: Keys holding values a later node or a driver reads back. Every one of them
#: round-trips through a Pydantic model or a plain mapping.
SHARED_STRUCTURED_KEYS: frozenset[str] = frozenset(
    {
        STATE_SOURCE_TEXTS,
        STATE_EXTRACTED_MODEL,
        STATE_VALID_MODEL,
        STATE_DOMAIN_PACKS,
        STATE_ANALYSIS,
        STATE_REJECTION,
    }
)


@dataclass(frozen=True)
class GraphKeys:
    """Both key families for one graph, given the frameworks it carries.

    Derived rather than declared as two module constants, because which keys
    exist is now a function of the selection: a graph built for one framework
    must not accept a write to another's key, and one built for two must accept
    both. Every node function takes this and opens its :class:`SessionState`
    through it, so the check a node passes is the check for the graph it is
    actually running in.
    """

    rendered: frozenset[str]
    structured: frozenset[str]

    @classmethod
    def of(cls, frameworks: Sequence[FrameworkName]) -> GraphKeys:
        nodes = [FrameworkNodes(name) for name in frameworks]
        return cls(
            rendered=frozenset(
                {
                    *SHARED_RENDERED_KEYS,
                    *(
                        node.key(artifact)
                        for node in nodes
                        for artifact in FRAMEWORK_RENDERED_ARTIFACTS
                    ),
                    *(
                        lane.key(artifact)
                        for node in nodes
                        for lane in node.lanes
                        for artifact in LANE_ARTIFACTS
                    ),
                }
            ),
            structured=frozenset(
                {
                    *SHARED_STRUCTURED_KEYS,
                    *(
                        node.key(artifact)
                        for node in nodes
                        for artifact in FRAMEWORK_STRUCTURED_ARTIFACTS
                    ),
                    *(lane.drafts_key for node in nodes for lane in node.lanes),
                }
            ),
        )

    def state(self, ctx) -> SessionState:
        """This graph's view of one node's session state."""
        return SessionState(ctx, self.rendered, self.structured)


class UndeclaredStateKey(KeyError):
    """A node wrote or read a key no family declares.

    Undeclared keys are how a rendered artifact and its structured counterpart
    come to disagree, and how a typo becomes a ``KeyError`` at the first LLM
    call instead of at the node that made it.
    """


class SessionState:
    """The one way a node touches the session, with the two families kept apart.

    ADK hands a FunctionNode a context whose ``state`` is a plain dict, and
    binds the next node's parameters out of it by name. That leaves the two
    key families indistinguishable at every call site, and the rule that keeps
    a rendered artifact and its structured counterpart from drifting — *a
    rendered key is written once and never read by Python* — was a comment.

    Here it is the interface. :meth:`prompt` writes a rendered key and
    :meth:`put` writes a structured one; each rejects a key from the other
    family, and there is **no method that reads a rendered key at all**. The
    rule holds because the operation does not exist, not because a reviewer
    remembered it.

    Both methods also reject a key neither family declares, so a mistyped key
    fails at the node that wrote it rather than at the model call that would
    have templated it.

    The two families are handed in rather than read from module constants: a
    graph's keys are a function of the frameworks it was built for
    (:func:`declared_keys`), so a node checks against the graph it is running in.
    """

    def __init__(
        self, ctx, rendered: Collection[str], structured: Collection[str]
    ) -> None:
        self._state = ctx.state
        self._rendered = rendered
        self._structured = structured

    def prompt(self, key: str, text: str) -> None:
        """Write a rendered key: bytes for a model, never read back here."""
        if key not in self._rendered:
            raise UndeclaredStateKey(f"{key!r} is not a rendered state key")
        self._state[key] = text

    def put(self, key: str, value: Any) -> None:
        """Write a structured key: a value a later node or a driver reads."""
        if key not in self._structured:
            raise UndeclaredStateKey(f"{key!r} is not a structured state key")
        self._state[key] = value

    def get(self, key: str) -> Any:
        """Read a structured key, or ``None`` where the node that writes it did not.

        ``None`` is load-bearing rather than a convenience: an LLM node that
        emits no text writes no key, and telling that absence from a written
        empty value is what :class:`SilentNodeError` is for.
        """
        if key not in self._structured:
            raise UndeclaredStateKey(f"{key!r} is not a structured state key")
        return self._state.get(key)


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
    defaulted to an empty list, which is how a silently dropped lane
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
    assemble node stops here and a driver completes the rest through
    :meth:`into_report`.

    **This class owns the report's shape.** The graph has two drivers — the
    service over a job, the eval harness over a corpus case — and each used to
    copy every field across itself. The copies had to agree and nothing checked
    that they did, so a field added here and missed in one driver produced a
    report with a block silently absent. :meth:`into_report` is the one place
    that mapping lives; a driver hands over the four things the graph cannot
    know and reads nothing out of this object itself.

    **One shared model, N finished blocks.** The blocks arrive whole — each
    built by ``assemble`` from its own framework's rulings, coverage and marks —
    rather than as loose arrays this class would have to reassemble, because a
    block's shape is its package's and this class knows no package.
    """

    system_model: SystemModel
    boundary_crossings: list[BoundaryCrossing]
    # One per framework the graph was built for, in that order. The envelope
    # re-checks the order against the job's own selection.
    analyses: list[FrameworkAnalysis]
    # The marks that belong to the *envelope* rather than to any block: the ones
    # about the shared System Model. A block's own marks are already on it.
    marks: AnalysisMarks
    # What ``prepare`` put in front of every lane of every framework. Carried
    # loose rather than as an :class:`~stride_service.report.AnalysisContext`
    # because that record's other field — the instruction digest — is a fact
    # about the *built graph* rather than about this job, and the graph is not
    # something a node holds. :meth:`context` joins them where the driver stamps
    # the rest of the run's static provenance.
    domain_packs: list[str]

    def context(self, instruction_sha256: str) -> AnalysisContext:
        """This analysis's context block, given the built graph's digest.

        The join is here rather than in each driver so the service and the eval
        harness cannot record the block differently — the same reason the two
        share one :class:`~stride_service.execution.GraphExecutor`.
        """
        return AnalysisContext(
            instruction_sha256=instruction_sha256,
            domain_packs=list(self.domain_packs),
        )

    def into_report(
        self,
        *,
        job: Job,
        input_ref: InputRef,
        nodes: Sequence[NodeRun],
        pipeline: Pipeline,
    ) -> Report:
        """This analysis as a report, given what only a driver observed.

        The four arguments are exactly the facts the graph does not hold. A
        ``job`` identity and an ``input_ref`` belong to whoever asked for the
        run; ``nodes`` is what the drive itself observed
        (:class:`~stride_service.execution.GraphRun`); ``pipeline`` is the
        built graph, which carries the per-tier sampling the report records in
        the clear and the instruction digest the context block needs.

        Every other field comes from ``self``, and no driver names one. That is
        the whole point: the service and the eval harness produce the same
        report shape by construction rather than by two field lists agreeing.
        """
        return Report(
            job=job,
            input=input_ref,
            nodes=list(nodes),
            sampling={
                tier: params.model_dump()
                for tier, params in pipeline.tier_sampling.items()
            },
            system_model=self.system_model,
            boundary_crossings=self.boundary_crossings,
            # The one mark family that stayed on the envelope, because its
            # subject is the shared model rather than any framework's claim.
            shared_element_names=self.marks.shared_element_names,
            elements_analyzed=len(self.system_model.elements()),
            analysis_context=self.context(pipeline.instruction_sha256),
            analyses=list(self.analyses),
        )

    def to_state(self) -> dict[str, Any]:
        """The JSON-safe form parked in session state."""
        return {
            "system_model": self.system_model.model_dump(mode="json"),
            "boundary_crossings": [
                crossing.model_dump(mode="json") for crossing in self.boundary_crossings
            ],
            "analyses": [block.model_dump(mode="json") for block in self.analyses],
            "marks": self.marks.model_dump(mode="json"),
            "domain_packs": list(self.domain_packs),
        }

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> Analysis:
        """Rebuild from session state, revalidating every part.

        Every key is read strictly. :meth:`to_state` writes all of them, and the
        only writer of this blob is that method, so a missing key means state
        this build did not produce — which fails here rather than rebuilding an
        analysis whose empty marks and empty context cannot be told apart from
        a run that genuinely had none.

        Each block is rebuilt as *its own framework's* shape, through the same
        registry lookup the envelope dispatches on, so a round trip through state
        does not flatten a package's narrowed claims to the neutral base.
        """
        return cls(
            system_model=SystemModel.model_validate(data["system_model"]),
            boundary_crossings=[
                BoundaryCrossing.model_validate(crossing)
                for crossing in data["boundary_crossings"]
            ],
            analyses=[_block_of(block) for block in data["analyses"]],
            marks=AnalysisMarks.model_validate(data["marks"]),
            domain_packs=list(data["domain_packs"]),
        )


def _block_of(payload: dict[str, Any]) -> FrameworkAnalysis:
    """One analysis block, validated as the shape its own framework registered.

    The same dispatch :class:`~stride_service.report.Report` runs on the way in,
    and it falls back to the neutral base for the same reason: a block naming a
    framework this build does not carry reads as a base claim rather than
    raising, which is the honest outcome.
    """
    schemas = SCHEMAS.get(payload.get("framework"))
    block_type = schemas.block if schemas else FrameworkAnalysis
    return block_type.model_validate(payload)


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
    ctx,
    keys: GraphKeys,
    extracted_model: dict | None = None,
    source_texts: dict | None = None,
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
        extracted_model, normalize_ids=True, sources=source_texts or {}
    )
    state = keys.state(ctx)
    if issues or model is None:
        parked = extracted_model if model is None else model.model_dump(mode="json")
        # Fenced for the same reason as the agents' copy: this is the model
        # built from caller words, handed straight back to a model.
        state.prompt(STATE_PREVIOUS_MODEL, render_fenced(parked))
        state.prompt(
            STATE_VALIDATION_ISSUES,
            render([issue.model_dump(mode="json") for issue in issues]),
        )
        return _routed(ROUTE_INVALID, {"issue_count": len(issues)})

    state.put(STATE_VALID_MODEL, model.model_dump(mode="json"))
    return _routed(ROUTE_VALID, {"issue_count": 0})


def reject_model(validation_issues: str, ctx, keys: GraphKeys) -> dict[str, Any]:
    """Terminal node: the repaired model failed too, so the job is rejected.

    Nothing is auto-repaired and nothing is analyzed on a model that never
    passed the gate — the user gets the validator's issues instead.
    """
    keys.state(ctx).put(STATE_REJECTION, validation_issues)
    return {"rejected": True}


# Element fields stripped from the model rendered to every lane agent and every
# critic. They survive on STATE_VALID_MODEL, so the report still
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


# Draft fields stripped from the set rendered to a critic and its re-ask.
# They survive on the framework's own ``drafts`` key, so assemble_claims still
# carries them into the report exactly as the agent wrote them.
#
# **Named by field rather than by framework**, which is the honest form of a
# neutral rule here: a field a critic does not rule on is one no framework's
# critic rules on, because the reason is the field's own — a recommendation is
# held beside the ruling and copied through untouched. A package adding a field
# of that kind adds it here, and the alternative (a per-package exclusion list)
# would let one framework quietly send its critic the block another does not.
_DRAFT_UNRULED_FIELDS = frozenset({"mitigations"})


def _ruling_view(drafts: Sequence[Claim]) -> list[dict]:
    """The drafts as a critic reads them: no recommendations, no empty branches.

    A critic's steps read ``description`` (evidence), the lane,
    ``affected_element_ids`` (duplicate), and ``grounds`` — plus whatever its own
    framework grades. ``mitigations`` is read by none of them, and the prompt
    already says so. A :class:`~stride_service.report.Mitigation` is a
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
    added to a package's record with a default now disappears from that
    framework's critic's view whenever it holds that default, silently and with
    nothing downstream able to see it. Every field a critic rules on is required
    today and so cannot be dropped;
    ``test_ruling_view_keeps_every_field_the_critic_rules_on`` is what holds that
    true for the next field.

    ``framework`` and ``framework_version`` go too, under the same rule: they are
    the same pair on every draft in one critic's prompt — it rules one
    framework's drafts — so they are a constant repeated per claim.
    """
    return [
        draft.model_dump(
            mode="json",
            exclude={*_DRAFT_UNRULED_FIELDS, "framework", "framework_version"},
            exclude_defaults=True,
        )
        for draft in drafts
    ]


def prepare_analysis(
    valid_model: dict,
    ctx,
    keys: GraphKeys,
    frameworks: Sequence[FrameworkName],
    domain_loader: MarkdownLoader,
    package_loaders: Mapping[FrameworkName, MarkdownLoader],
) -> dict[str, Any]:
    """Derive the whole deterministic view every lane agent reasons from.

    Crossings are computed here rather than extracted, so no agent can be handed
    a crossing that contradicts the zones in the model it is reading. The same
    argument carries three more artifacts, all of them functions of the
    validated model alone:

    * **The evidence catalog** (:mod:`stride_service.evidence`) — the closed
      set of facts an agent may cite. Agents are shown its **references only**:
      each spells out the fact it stands for, and the fields behind it are the
      element and flow IDs of the model rendered directly above, so sending the
      resolved objects too would restate what the agent is about to read.
      Derived here *and* again at the fan-in rather than passed between them,
      so the set an agent chose from and the set its choice resolves against
      cannot differ.
    * **Candidates** (:mod:`stride_service.candidates`) — the structural
      conditions each lane's rules fire on, parked per ``(framework, lane)`` so
      an agent reads only its own. They are *leads*, and nothing downstream of
      the prompt reads them: a candidate cannot become a claim, cannot ground
      one, and does not appear in the report.
    * **Domain packs** (:mod:`stride_service.domains`) — the reference
      material this model earns. Selected here rather than composed into the
      instruction because the selection is per-job and the graph is built once.
    * **The stripped model** — see :func:`_without_source_fields`. Every critic
      templates against this same key, so it is stripped there too, and none of
      them reads a submitter's words except the ones a finding chose to quote.

    **What is shared and what is not.** The model, its crossings, the evidence
    catalog and the domain packs are the job's and are derived once for every
    framework — #162's ruling that one extraction serves them all is exactly what
    makes that legal, and deriving them twice would let two frameworks reason
    about two systems. Candidates and the retrieved corpus are the *package's*:
    they come from its own rules, so they are derived per framework and parked
    per lane.

    The loaders are bound by :func:`prepare_node` rather than read from state:
    they are repo paths, not facts about the job, and ADK binds a FunctionNode's
    parameters from session state.

    Candidate facts carry caller-authored attribute values, so they are fenced
    with :func:`render_fenced` exactly as the model is — the bytes are a subset
    of what already rides in ``{system_model}`` and they get the same
    treatment. The pack text is repo-authored and needs none.
    """
    model = SystemModel.model_validate(valid_model)
    crossings = model.boundary_crossings()
    catalog = evidence_catalog(model)
    packs = select_domain_packs(model)

    state = keys.state(ctx)
    state.prompt(STATE_SYSTEM_MODEL, render_fenced(_without_source_fields(valid_model)))
    state.prompt(
        STATE_BOUNDARY_CROSSINGS,
        render([crossing.model_dump(mode="json") for crossing in crossings]),
    )
    state.prompt(STATE_EVIDENCE_CATALOG, render_catalog(catalog))
    state.prompt(STATE_DOMAIN_SKILLS, compose_domain_skills(domain_loader, packs))
    state.put(STATE_DOMAIN_PACKS, list(packs))

    candidate_count = 0
    knowledge_count = 0
    for name in frameworks:
        nodes = FrameworkNodes(name)
        package = nodes.package
        loader = package_loaders[name]
        candidates = generate_candidates(model, package.lanes, package.rules)
        retrieved: list[str] = []
        for lane in nodes.lanes:
            candidate_set = candidates[lane.lane]
            # Retrieval is by *fired* rule, so a lane that triggered nothing gets
            # nothing: the material follows the leads rather than the lane.
            fired = {candidate.rule_id for candidate in candidate_set.candidates}
            notes = select_documents(package.knowledge.notes, fired, MAX_NOTES)
            cases = select_documents(package.knowledge.cases, fired, MAX_CASES)
            # Keyed by the placeholder each fills, which is the vocabulary the
            # prompt file uses; the lane turns that into its own four state keys.
            lane_state = lane.state(
                {
                    "candidates": render_fenced(candidate_set.model_dump(mode="json")),
                    "scope": lane_scope(lane.lane, package, model, candidate_set),
                    "reference_notes": compose_notes(loader, notes),
                    "prior_cases": compose_cases(loader, cases),
                }
            )
            for key, text in lane_state.items():
                state.prompt(key, text)
            retrieved += [f"notes/{doc}" for doc in notes]
            retrieved += [f"cases/{doc}" for doc in cases]
        # The record of what this framework's agents were given, written here
        # because here is where they are given it, and keyed to the framework
        # because a rule and a document both belong to the package that declared
        # them. Sorted and deduplicated: it is a set of rules that matched, and
        # firing order across independent lanes is not a fact about anything.
        state.put(
            nodes.key("retrieved"),
            {
                "knowledge_docs": sorted(set(retrieved)),
                "fired_rules": sorted(
                    {
                        candidate.rule_id
                        for candidate_set in candidates.values()
                        for candidate in candidate_set.candidates
                    }
                ),
            },
        )
        candidate_count += sum(len(each.candidates) for each in candidates.values())
        knowledge_count += len(set(retrieved))

    return {
        "element_count": len(model.elements()),
        "crossing_count": len(crossings),
        "evidence_count": len(catalog),
        "candidate_count": candidate_count,
        "domain_packs": list(packs),
        "knowledge_doc_count": knowledge_count,
    }


def prepare_node(
    keys: GraphKeys,
    frameworks: Sequence[FrameworkName],
    domain_loader: MarkdownLoader,
    package_loaders: Mapping[FrameworkName, MarkdownLoader],
) -> FunctionNode:
    """The ``prepare`` node, with this deployment's Markdown roots bound to it.

    Everything but ``valid_model`` is bound here rather than read from state for
    the same reason: a repo path and the graph's own framework list are facts
    about the build, not about the job, and ADK binds a FunctionNode's parameters
    from session state.
    """

    def prepare_analysis_node(valid_model: dict, ctx) -> dict[str, Any]:
        return prepare_analysis(
            valid_model, ctx, keys, frameworks, domain_loader, package_loaders
        )

    return FunctionNode(func=prepare_analysis_node, name=PREPARE_NODE)


def _claims_of(payload: object) -> list[Any]:
    """The list inside an LLM node's emission, or empty if the node never ran.

    Lane agents and review nodes emit ``{"claims": [...]}`` rather than a bare
    array, because a bare ``list[...]`` schema is one ADK cannot convert into a
    response format — it sends none and the node generates unconstrained. The
    wrapper is the schema's shape, not the domain's, so it is unwrapped here at
    the boundary and nothing downstream carries it. The field name is neutral
    because the prompt asking for it is shared; see
    :class:`~stride_service.report.ProposalBatch`.

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
    claims = payload.get("claims")
    return claims if isinstance(claims, list) else []


def _model_marks(model: SystemModel) -> AnalysisMarks:
    """The marks that are about the model rather than about a threat.

    One so far. Elements of different types whose names normalize to one slug
    are a suspicion the validity gate cannot carry — its one severity is fatal,
    and a system may legitimately run a process and keep a store of the same
    name. Derived in :func:`assemble_report` rather than bound as a parameter:
    it is a fact about the model that node already holds, so no upstream node
    has to carry it, and deriving it in both places would double every entry.
    """
    return AnalysisMarks(
        shared_element_names=[
            SharedElementName(name_slug=slug, element_ids=ids)
            for slug, ids in model.shared_names().items()
        ]
    )


def merge_drafts(
    valid_model: dict,
    ctx,
    keys: GraphKeys,
    nodes: FrameworkNodes,
    source_texts: dict | None = None,
) -> dict[str, Any]:
    """Merge one framework's lane agents' proposals into the list its critic sees.

    **One of these per selected framework.** Each fans in only its own lanes, so
    two frameworks' drafts never meet: they are ruled by different critics
    against different questions, and a duplicate across them is not a duplicate.

    Resolution first: an agent emits this package's own
    :class:`~stride_service.report.Proposal` subclass, which *names* its
    evidence, and :func:`~stride_service.evidence.resolve_proposals` turns each
    reference back into the ground the catalog holds for it, composes the claim
    ID from the package's own ``IdRule`` and stamps the lane. The catalog is
    re-derived here from the same validated model :func:`prepare_analysis`
    derived it from, rather than parked in state and read back — it is a pure
    function of that model, so deriving it twice is what guarantees the set an
    agent chose from and the set its choice is resolved against are the same set.

    Then the mechanical half of the fan-in: :func:`join_drafts` fails closed if
    a draft cites an element the model does not contain, if two agents reused a
    claim ID, if a grounds reference does not resolve, or if no ground on a
    claim verifies at all — so the critic spends judgement on evidence, lanes
    and whether a verbatim quote actually supports what it was filed under.

    It also returns what it could *not* verify: quote grounds absent from the
    source they name are marked rather than dropped. Those marks join the ones
    the evidence resolution produced as a single
    :class:`~stride_service.report.AnalysisMarks` under this framework's own
    key, which is what :func:`assemble_report` carries into this framework's
    block. The one mark about the *model* is not here (:func:`_model_marks`):
    one model serves N blocks, so it is derived once at the envelope.
    ``source_texts`` defaults to ``None`` so the in-process engine, which drives
    a hand-authored model with no job behind it, is not failed on a citation that
    is not wrong.

    An agent that emitted **nothing** fails the job here, before any of that. A
    framework's lanes are what make its output that framework's method rather
    than a list of findings, so a lane that never ran is not a smaller report —
    it is a different method, and one whose absence nothing downstream can see: a
    critic rules what it is given, a summary counts what exists, and a breakdown
    omits a lane with no claims rather than carrying a zero. A truncated agent
    would delete a lane of the analysis and finish green.

    A lane that ran and found nothing is a different thing and stays legal — it
    emits ``{"claims": []}``, its key is written, and ``_claims_of`` reads it as
    the empty list it is. That distinction is the whole check: the absent key,
    not the empty list.

    Two state keys, two shapes, on purpose. ``drafts`` holds the drafts whole,
    because that is what ``assemble_claims`` merges rulings onto to build the
    block. ``draft_view`` is the *prompt* view, narrowed by :func:`_ruling_view`
    to the fields a verdict is actually reached from.
    """
    state = keys.state(ctx)
    lanes = nodes.lanes
    silent = [lane for lane in lanes if state.get(lane.drafts_key) is None]
    if silent:
        written = ", ".join(repr(lane.drafts_key) for lane in silent)
        raise SilentNodeError(
            f"{len(silent)} of {len(lanes)} {nodes.name} lane agents wrote"
            f" nothing ({written}), so those lanes were never analyzed."
            f" {_TRUNCATION_HINT}"
        )
    package = nodes.package
    schemas = nodes.schemas
    proposal_batch = schemas.proposals
    model = SystemModel.model_validate(valid_model)
    catalog = evidence_catalog(model)
    resolutions = {
        lane.lane: resolve_proposals(
            proposal_batch.model_validate(
                {"claims": _claims_of(state.get(lane.drafts_key))}
            ).claims,
            catalog,
            package,
            lane.lane,
        )
        for lane in lanes
    }
    drafts_by_lane = {
        lane: resolution.drafts for lane, resolution in resolutions.items()
    }
    joined = join_drafts(drafts_by_lane, package, model, source_texts or {})
    merged = joined.drafts
    state.put(nodes.key("drafts"), [draft.model_dump(mode="json") for draft in merged])
    # Every mark this framework's fan-in produced, from both of its producers:
    # the join across its lanes, and each lane's own evidence resolution. Merged
    # rather than parked separately — they share an owner, a standing and a
    # policy, so one key carries them and ``assemble`` reads one parameter.
    marks = joined.marks
    for resolution in resolutions.values():
        marks = marks.merged_with(resolution.marks)
    state.put(nodes.key("marks"), marks.model_dump(mode="json"))
    # Logged rather than reported, and the package decides what is worth saying:
    # a STRIDE lane that numbered its drafts 01, 02, 05 broke nothing a reader
    # could act on, and the agents are who it is about.
    for message in package.record.lane_diagnostics(merged):
        logger.warning(message)
    # Coverage is computed here, over the drafts, because the question it
    # answers — did this lane look at the system — is about what the agents did,
    # not about what survived review. The candidates are regenerated rather than
    # read back from state: they are a pure function of the same validated model
    # and the same package rules, so the two derivations cannot disagree, and the
    # fan-in stays free of a dependency on a key ``prepare`` wrote for the prompt.
    coverage = build_coverage(
        drafts_by_lane,
        generate_candidates(model, package.lanes, package.rules),
        model,
        package,
    )
    state.put(nodes.key("coverage"), [row.model_dump(mode="json") for row in coverage])
    state.prompt(nodes.key("draft_view"), render(_ruling_view(merged)))
    return {
        "framework": nodes.name,
        "draft_count": len(merged),
        "unverified_count": len(marks.unverified_grounds),
        "unresolved_mention_count": len(marks.unresolved_mentions),
    }


def route_review(
    valid_model: dict, ctx, keys: GraphKeys, nodes: FrameworkNodes
) -> Event:
    """Run the mechanical check on one critic's output and route on the result.

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
    claims are in trouble.

    Both of a framework's review nodes run this same function — what differs is
    where their ``revise`` edge points (``recritic`` for the first look,
    ``critic_failed`` for the second), exactly as the two validate nodes share
    one function.

    **Both of this framework's keys are read from state rather than bound as
    parameters**, and that is what makes N critics expressible at all: ADK binds
    a FunctionNode's parameters by name, and a name derived per framework cannot
    be spelled in a signature. Reading them keeps the absence handling that
    binding used to provide, and keeps it honest — **an LLM node that emits no
    text does not write its output key at all**, so a silent critic arrives here
    as ``None`` rather than as a parameter-binding ``ValueError`` naming this
    function, which would read as a graph defect rather than as what it is. A
    model returns nothing when its completion is truncated at
    ``max_output_tokens`` — reasoning tokens are spent against that same cap, so
    the critic, which rules on every draft in one pass, is the node that hits it
    first. Absent output is output that dropped every draft, so it takes the
    ``revise`` edge the graph already has: one bounded re-ask, then
    ``critic_failed`` raises the ``CriticOutputError`` that names what did not
    reconcile.

    An absent *drafts* key is the narrower case: a graph entered past this
    framework's own ``merge`` has none, and an empty draft set with an empty
    ruling set reconciles, which is the honest reading of a framework that
    drafted nothing.
    """
    model = SystemModel.model_validate(valid_model)
    state = keys.state(ctx)
    package_drafts = _drafts_of(state.get(nodes.key("drafts")), nodes.package)
    ruled = _claims_of(state.get(nodes.key("reviewed")))
    rulings = _rulings_of(ruled, nodes.schemas)
    problems = review_issues(package_drafts, rulings, model)
    if problems:
        state.prompt(nodes.key("previous_review"), render(ruled))
        state.prompt(nodes.key("critic_issues"), render(problems.messages))
        # The roster is the covering set the re-ask must reproduce, and an ID is
        # the whole of that claim — the re-ask reproduces rulings, not drafts.
        # Only the drafts it cannot fix without reading travel in full.
        state.prompt(
            nodes.key("draft_roster"), render([draft.id for draft in package_drafts])
        )
        state.prompt(
            nodes.key("unreconciled_drafts"),
            render(
                _ruling_view(
                    [
                        draft
                        for draft in package_drafts
                        if draft.id in problems.implicated
                    ]
                )
            ),
        )
        return _routed(ROUTE_REVISE, {"issue_count": len(problems.messages)})
    return _routed(ROUTE_ACCEPT, {"reviewed_count": len(rulings)})


def _drafts_of(drafts: list | None, package: FrameworkPackage) -> list[Claim]:
    """One framework's parked drafts, revalidated as its own record type."""
    return [package.record.model_validate(draft) for draft in drafts or []]


def _rulings_of(ruled: Sequence[Any], schemas: FrameworkSchemas) -> list[Ruling]:
    """One critic's emission, revalidated as this framework's own ruling type."""
    return list(schemas.rulings.model_validate({"claims": list(ruled)}).claims)


def fail_review(valid_model: dict, ctx, keys: GraphKeys, nodes: FrameworkNodes) -> dict:
    """Terminal node: this framework's critic re-ask still did not reconcile.

    Reached only on the ``revise`` edge out of ``rereview``, which means the
    check found problems on the second look. Raising propagates out of the
    runner as a failed job — not a *rejected* one: rejection means the input
    failed the validity gate and carries ``ValidationIssue``s, whereas a critic
    that will not return its own drafts whole is our defect and has none.

    The two keys are read from state for the reason :func:`route_review` gives,
    and it matters most of all here: this is the node a silent critic *reaches*,
    so a binding failure would kill the run at the one place built to report why
    it died.
    """
    model = SystemModel.model_validate(valid_model)
    state = keys.state(ctx)
    # review_issues is non-empty here by construction; assemble_claims raises
    # the CriticOutputError naming exactly what still does not reconcile.
    assemble_claims(
        _drafts_of(state.get(nodes.key("drafts")), nodes.package),
        _rulings_of(_claims_of(state.get(nodes.key("reviewed"))), nodes.schemas),
        model,
        nodes.schemas,
    )
    raise AssertionError("fail_review reached on reconciled critic output")


def assemble_report(
    valid_model: dict,
    ctx,
    keys: GraphKeys,
    frameworks: Sequence[FrameworkName],
    disclaimers: Mapping[FrameworkName, str],
    domain_packs: list | None = None,
) -> dict[str, Any]:
    """Build the report body deterministically from every critic's rulings.

    **The one node that sees more than one framework.** Every framework's
    subgraph fans back in here, and this is where N blocks become one
    :class:`Analysis` over the one shared model. It reads each framework's four
    parked artifacts off its own keys rather than through ADK parameter binding,
    because the keys are derived per framework and a function signature cannot
    be written per selection.

    Reached only on the ``accept`` edge of every framework's router, so each
    mechanical check has already passed in :func:`route_review`.
    :func:`assemble_claims` re-runs it and fails closed regardless — nothing
    reaches the report on output that did not survive the gate — then splits each
    framework's ruled claims into the actionable and rejected arrays by verdict
    rather than any model's say-so.

    **Absent keys are read as absent rather than as zero.** A graph driven from a
    seeded state that never ran a framework's ``merge`` has no drafts, no marks
    and no coverage for it, and each records that way: an empty block whose
    coverage list is empty says "no account" where a fabricated row of zeros
    would say "looked and found nothing". ``domain_packs`` is the same case one
    node earlier — a graph entered past ``prepare`` was given no packs, and an
    empty list says so.

    The one mark this node adds is :func:`_model_marks`, which is about the
    shared System Model rather than about any framework's claim. It is derived
    here because one model serves N blocks: deriving it per framework would put
    the same finding in the report N times.
    """
    model = SystemModel.model_validate(valid_model)
    state = keys.state(ctx)
    blocks = [
        _framework_block(FrameworkNodes(name), state, model, disclaimers[name])
        for name in frameworks
    ]
    analysis = Analysis(
        system_model=model,
        boundary_crossings=model.boundary_crossings(),
        analyses=blocks,
        marks=_model_marks(model),
        domain_packs=list(domain_packs or []),
    )
    state.put(STATE_ANALYSIS, analysis.to_state())
    return {
        "claim_count": sum(len(block.claims) for block in blocks),
        "rejected_count": sum(len(block.rejected_claims) for block in blocks),
        "framework_count": len(blocks),
    }


def _framework_block(
    nodes: FrameworkNodes,
    state: SessionState,
    model: SystemModel,
    disclaimer: str,
) -> FrameworkAnalysis:
    """One framework's finished analysis block, from its own parked artifacts.

    The block type is the package's own, so a package's narrowed claim arrays and
    narrowed summary are built here rather than validated into existence
    downstream. Its summary comes from that same type
    (:meth:`~stride_service.report.FrameworkAnalysis.summarize`), which is what
    keeps the count the block declares and the count its own validator recomputes
    from disagreeing.

    **A mark lands on the block that declares it.** The fan-in produces one
    :class:`~stride_service.report.AnalysisMarks` carrying every mark kind; only
    the fields this block type actually declares are set from it. That is how
    STRIDE's ``missing_mitigations`` reaches a STRIDE block while a framework
    that recommends nothing carries no such field and is handed nothing —
    without either side naming the other.
    """
    schemas = nodes.schemas
    package = nodes.package
    drafts = _drafts_of(state.get(nodes.key("drafts")), package)
    rulings = _rulings_of(_claims_of(state.get(nodes.key("reviewed"))), schemas)
    claims, rejected = assemble_claims(drafts, rulings, model, schemas)
    marks = AnalysisMarks.model_validate(state.get(nodes.key("marks")) or {})
    retrieved = state.get(nodes.key("retrieved")) or {}
    return schemas.block(
        framework=package.name,
        framework_version=package.version,
        disclaimer=disclaimer,
        claims=claims,
        rejected_claims=rejected,
        coverage=[
            LaneCoverage.model_validate(row)
            for row in state.get(nodes.key("coverage")) or []
        ],
        fired_rules=list(retrieved.get("fired_rules", [])),
        knowledge_docs=list(retrieved.get("knowledge_docs", [])),
        summary=schemas.block.summarize(claims, rejected),
        **{
            field: value
            for field, value in marks.model_dump(mode="json").items()
            if field in schemas.block.model_fields
        },
    )


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

    ``instruction_sha256`` is the other half of "what produced this report", and
    the half the fingerprint cannot reach: it digests every LLM node's composed
    instruction, so a prompt or skill edit moves it while the model and sampling
    stand still. See :func:`instruction_digest`.
    """

    workflow: Workflow
    node_models: dict[str, str]
    tier_sampling: dict[TierName, TierSampling]
    node_sampling: dict[str, TierSampling]
    instruction_sha256: str
    #: The selection this graph was built for, in block order. A driver needs it
    #: to stamp the job's own ``frameworks`` list, and the envelope checks the two
    #: agree — so it rides with the built graph rather than being passed beside it.
    frameworks: tuple[FrameworkName, ...]
    #: This graph's node names against the tier keys they resolve on. Built per
    #: selection (:func:`tier_node_by_graph_node`), so a caller computing a
    #: fingerprint reads it from here rather than rebuilding it.
    tier_nodes: dict[str, str]


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
    tier_node: str,
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
    (:func:`tier_node_by_graph_node`), so each node runs on its own tier's
    decoding params from the config shared with the eval suite — no node on
    library defaults, none on another tier's sampling. The tier key is passed in
    rather than looked up, because the map is now built per selection and the
    caller already holds it. The request deadline comes from the resilience
    config.
    """
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
        tier_node="extract",
        instruction=compose_extract_prompt(prompt_loader),
        output_schema=SystemModel,
        output_key=STATE_EXTRACTED_MODEL,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
        resilience=resilience,
    )


def _instruction(skills: str, prompt: str) -> str:
    """Skill text then prompt text: what to know, then what to do with it.

    Stable-first order, so a framework's lane agents and every job for one lane
    share the longest possible cacheable prefix (tickets 006 and 013).
    """
    return f"{skills.strip()}\n\n{prompt.strip()}\n"


def analyze_instruction(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    package: FrameworkPackage,
    lane: Lane,
) -> str:
    """One lane agent's full instruction, with the per-lane names resolved.

    The skills are the *package's* — its lane skill and, where its record grades
    harm, its severity rubric — and the prompt is the service's shared
    ``analyze.md`` plus that lane's own exemplars.

    The placeholder substitutions happen here rather than in ADK, for one
    reason: a framework's lane agents run in parallel against a single session
    state, which cannot hold N different values for one key. So ``{candidates}``
    becomes *the name of this lane's state key* and what ADK templates at run
    time is ``{candidates_stride_spoofing}`` — one prompt file, one binding set
    per lane, no lane reading another's leads.

    Which placeholder becomes which key is :attr:`Lane.prompt_bindings`, read
    from the same :data:`LANE_ARTIFACTS` that :meth:`Lane.state` writes by. The
    job-varying placeholders stay for ADK to template.
    """
    skills = compose_lane_skills(package_loader, package, lane.lane)
    prompt = compose_analyze_prompt(prompt_loader, package_loader, lane.lane)
    for placeholder, binding in lane.prompt_bindings.items():
        prompt = prompt.replace(f"{{{placeholder}}}", binding)
    return _instruction(skills, prompt)


def _review_instruction(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    package: FrameworkPackage,
    nodes: FrameworkNodes,
    compose_prompt: Callable[[MarkdownLoader], str],
) -> str:
    """One framework's critic or re-ask instruction, with its keys resolved.

    Both share :func:`~stride_service.skills.compose_critic_skills` byte for
    byte, so the re-ask reads the same rubric, the same ``critic.md`` and the
    same lane digest the critic did — it may have to re-rule a draft it dropped
    — and the two share that cacheable prefix across jobs.

    The two placeholders a review prompt carries are this framework's own state
    keys, substituted here for the reason :func:`analyze_instruction` gives:
    N critics run against one session state.
    """
    prompt = compose_prompt(prompt_loader)
    for placeholder, artifact in REVIEW_ARTIFACTS.items():
        prompt = prompt.replace(f"{{{placeholder}}}", f"{{{nodes.key(artifact)}}}")
    return _instruction(compose_critic_skills(package_loader, package), prompt)


@dataclass(frozen=True)
class _FrameworkSubgraph:
    """One framework's built nodes, and the edges that wire them.

    The topology below ``prepare`` is the same shape for every framework — fan
    out to the lanes, join, merge, critic, route, one bounded re-ask, look again
    — so it is built once here and instantiated per selection. Holding the built
    nodes together is what lets :func:`build_pipeline` name the edges without
    re-deriving a single node name.
    """

    nodes: FrameworkNodes
    agents: tuple[LlmAgent, ...]
    critic: LlmAgent
    recritic: LlmAgent
    join: JoinNode
    merge: FunctionNode
    router: FunctionNode
    rereview: FunctionNode
    critic_failed: FunctionNode

    @property
    def llm_nodes(self) -> tuple[LlmAgent, ...]:
        return (*self.agents, self.critic, self.recritic)

    def edges(self, assemble: FunctionNode) -> list[tuple[Any, ...]]:
        """This framework's own edges, converging on the one shared assemble node."""
        return [
            *((agent, self.join) for agent in self.agents),
            (self.join, self.merge, self.critic, self.router),
            (self.router, {ROUTE_ACCEPT: assemble, ROUTE_REVISE: self.recritic}),
            (self.recritic, self.rereview),
            (
                self.rereview,
                {ROUTE_ACCEPT: assemble, ROUTE_REVISE: self.critic_failed},
            ),
        ]


def _framework_subgraph(
    nodes: FrameworkNodes,
    *,
    keys: GraphKeys,
    prompt_loader: MarkdownLoader,
    package_loader: MarkdownLoader,
    tier_nodes: Mapping[str, str],
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
    resilience: ResilienceConfig | None,
) -> _FrameworkSubgraph:
    """Build one framework's lane agents, critic, re-ask and four function nodes."""
    package = nodes.package
    schemas = nodes.schemas
    reviewed_key = nodes.key("reviewed")

    def merge(valid_model: dict, ctx, source_texts: dict | None = None):
        return merge_drafts(valid_model, ctx, keys, nodes, source_texts)

    def route(valid_model: dict, ctx) -> Event:
        return route_review(valid_model, ctx, keys, nodes)

    def failed(valid_model: dict, ctx) -> dict:
        return fail_review(valid_model, ctx, keys, nodes)

    return _FrameworkSubgraph(
        nodes=nodes,
        agents=tuple(
            _llm_node(
                name=lane.node_name,
                tier_node=tier_nodes[lane.node_name],
                instruction=analyze_instruction(
                    package_loader, prompt_loader, package, lane
                ),
                output_schema=schemas.proposals,
                output_key=lane.drafts_key,
                resolve_model=resolve_model,
                resolve_sampling=resolve_sampling,
                resilience=resilience,
            )
            for lane in nodes.lanes
        ),
        critic=_llm_node(
            name=nodes.node(CRITIC_ROLE),
            tier_node=tier_nodes[nodes.node(CRITIC_ROLE)],
            instruction=_review_instruction(
                package_loader, prompt_loader, package, nodes, compose_critic_prompt
            ),
            output_schema=schemas.rulings,
            output_key=reviewed_key,
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
            resilience=resilience,
        ),
        recritic=_llm_node(
            name=nodes.node(RECRITIC_ROLE),
            tier_node=tier_nodes[nodes.node(RECRITIC_ROLE)],
            instruction=_review_instruction(
                package_loader, prompt_loader, package, nodes, compose_recritic_prompt
            ),
            output_schema=schemas.rulings,
            output_key=reviewed_key,
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
            resilience=resilience,
        ),
        join=JoinNode(name=nodes.node(JOIN_ROLE)),
        merge=FunctionNode(func=merge, name=nodes.node(MERGE_ROLE)),
        router=FunctionNode(func=route, name=nodes.node(ROUTER_ROLE)),
        rereview=FunctionNode(func=route, name=nodes.node(REREVIEW_ROLE)),
        critic_failed=FunctionNode(func=failed, name=nodes.node(CRITIC_FAILED_ROLE)),
    )


def build_pipeline(
    *,
    prompt_loader: MarkdownLoader,
    domain_loader: MarkdownLoader,
    package_loaders: Mapping[FrameworkName, MarkdownLoader],
    binding: NodeBinding,
    frameworks: Sequence[FrameworkName],
    entry: Entry = ENTRY_EXTRACT,
    name: str = "stride_pipeline",
) -> Pipeline:
    """Wire the whole graph: prompts, skills, and models onto the topology.

    ``frameworks`` is the selection this graph runs, in the order the report's
    blocks will carry. **A graph is built for one selection**, because the nodes,
    the state keys and the instruction digest are all functions of it: a graph
    carrying nodes a job did not select would leave them unfired and their keys
    unwritten, and the envelope's own check that the blocks answer the job's
    framework list in order would have nothing to answer with. A deployment
    serving several selections builds several pipelines.

    ``binding`` is everything an LLM node runs on — which model, which tier's
    decoding params, and the per-request deadline — as one value, because two
    of its fields are views of the same
    :class:`~stride_service.sampling.SamplingConfig` and sourcing them from
    different ones would leave every node running on params the report does not
    attest to. See :class:`~stride_service.binding.NodeBinding`.

    The loaders are the three roots this architecture has: ``prompts/`` for the
    shared bodies, ``domains/`` for the packs, and one per package rooted at
    ``frameworks/<name>/``. A deployment that redirects
    ``STRIDE_FRAMEWORKS_DIR`` redirects the third and neither of the others.

    ``entry`` selects where the graph starts. ``"extract"`` is production and
    the end-to-end eval mode. ``"prepare"`` is the **analysis** eval mode: a
    blessed System Model is seeded at :data:`STATE_VALID_MODEL` and the
    extraction half is left out entirely, so claim numbers are attributable to
    the lane agents and critics rather than to an element ``extract`` never
    produced. It is a parameter here, not a second topology in the eval tree,
    because two definitions of the same graph drift.
    """
    if entry not in (ENTRY_EXTRACT, ENTRY_PREPARE, ENTRY_EXTRACT_ONLY):
        raise ValueError(f"unknown graph entry point: {entry!r}")
    if not frameworks:
        raise ValueError("a graph must be built for at least one framework")

    resolve_model = binding.resolve_model
    resolve_sampling = binding.resolve_sampling
    resilience = binding.resilience
    tier_nodes = tier_node_by_graph_node(frameworks)

    if entry == ENTRY_EXTRACT_ONLY:
        extract = _extract_node(
            prompt_loader, resolve_model, resolve_sampling, resilience
        )
        return Pipeline(
            workflow=Workflow(name=name, edges=[(START, extract)]),
            node_models={extract.name: _model_name(extract.model)},
            tier_sampling=dict(binding.tier_sampling),
            node_sampling=_node_sampling([extract], resolve_sampling, tier_nodes),
            instruction_sha256=instruction_digest([extract]),
            frameworks=tuple(frameworks),
            tier_nodes=tier_nodes,
        )

    keys = GraphKeys.of(frameworks)
    disclaimers = {
        framework: package_loaders[framework].load(DISCLAIMER_DOC).strip()
        for framework in frameworks
    }
    prepare = prepare_node(keys, frameworks, domain_loader, package_loaders)
    assemble = FunctionNode(
        func=_assemble_node_func(keys, frameworks, disclaimers), name=ASSEMBLE_NODE
    )

    subgraphs = [
        _framework_subgraph(
            FrameworkNodes(framework),
            keys=keys,
            prompt_loader=prompt_loader,
            package_loader=package_loaders[framework],
            tier_nodes=tier_nodes,
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
            resilience=resilience,
        )
        for framework in frameworks
    ]

    extraction_nodes: list[LlmAgent] = []
    if entry == ENTRY_EXTRACT:
        extract = _extract_node(
            prompt_loader, resolve_model, resolve_sampling, resilience
        )
        repair = _llm_node(
            name=REPAIR_NODE,
            tier_node="repair",
            instruction=compose_repair_prompt(prompt_loader),
            output_schema=SystemModel,
            output_key=STATE_EXTRACTED_MODEL,
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
            resilience=resilience,
        )
        validate = FunctionNode(func=_validate_node_func(keys), name=VALIDATE_NODE)
        revalidate = FunctionNode(func=_validate_node_func(keys), name=REVALIDATE_NODE)
        reject = FunctionNode(func=_reject_node_func(keys), name=REJECT_NODE)
        extraction_nodes = [extract, repair]
        # ``list[tuple[Any, ...]]`` because ADK does not export the alias for
        # a chain element, and a routing-map literal only infers its declared
        # key type under an expected type -- which a bare local has none of.
        head_edges: list[tuple[Any, ...]] = [
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
            (prepare, tuple(agent for sub in subgraphs for agent in sub.agents)),
            *(edge for sub in subgraphs for edge in sub.edges(assemble)),
        ],
    )
    llm_nodes = [
        *extraction_nodes,
        *(node for sub in subgraphs for node in sub.llm_nodes),
    ]
    return Pipeline(
        workflow=workflow,
        node_models={node.name: _model_name(node.model) for node in llm_nodes},
        tier_sampling=dict(binding.tier_sampling),
        node_sampling=_node_sampling(llm_nodes, resolve_sampling, tier_nodes),
        instruction_sha256=instruction_digest(llm_nodes),
        frameworks=tuple(frameworks),
        tier_nodes=tier_nodes,
    )


def _validate_node_func(keys: GraphKeys) -> Callable[..., Any]:
    """The validity gate, with this graph's key families bound to it.

    ADK binds a FunctionNode's parameters from session state, so ``keys`` — a
    fact about the built graph rather than about the job — is closed over rather
    than declared. Both validate nodes share this function; what differs is
    where their ``invalid`` edge points.
    """

    def validate(
        ctx, extracted_model: dict | None = None, source_texts: dict | None = None
    ) -> Event:
        return validate_extraction(ctx, keys, extracted_model, source_texts)

    return validate


def _reject_node_func(keys: GraphKeys) -> Callable[..., Any]:
    """The terminal rejection node, with this graph's key families bound to it."""

    def reject(validation_issues: str, ctx) -> dict[str, Any]:
        return reject_model(validation_issues, ctx, keys)

    return reject


def _assemble_node_func(
    keys: GraphKeys,
    frameworks: Sequence[FrameworkName],
    disclaimers: Mapping[FrameworkName, str],
) -> Callable[..., Any]:
    """The ``assemble`` node, with everything that is not the job bound to it."""

    def assemble(valid_model: dict, ctx, domain_packs: list | None = None):
        return assemble_report(
            valid_model, ctx, keys, frameworks, disclaimers, domain_packs
        )

    return assemble


def _model_name(model: str | BaseLlm) -> str:
    """The model string to record, however the node was bound to it."""
    return model if isinstance(model, str) else model.model


def instruction_digest(llm_nodes: Sequence[LlmAgent]) -> str:
    """One hash over what every LLM node was told, before any job reaches it.

    Digested at build time, with the job-varying ``{placeholders}`` still
    unexpanded — so this identifies the **repo-authored** text (prompts,
    category skills, the shared rubric, the critic's digest) and contains no
    submitter bytes at all. That is what makes it publishable beside a report:
    the input's own digest is ``input.source_sha256`` and stays separate,
    because "which instructions ran" and "which text was analysed" are
    different questions and a hash that mixed them could answer neither.

    Node names are part of the payload rather than only their text, so a
    swap that gave two nodes each other's instruction moves the hash. Sorted,
    because a dict ordering is not a fact about the graph.

    It is deliberately **not** part of certification. A blessed fingerprint
    attests to a generation identity — model and decoding params — and widening
    it to the prompts would re-baseline every blessed pair on any prompt edit,
    which is a decision about what a deployment sanctions rather than a fact
    about a run. Recording it is what lets a reader notice; gating on it is a
    separate choice nobody has made.
    """
    payload = json.dumps(
        {node.name: node.instruction for node in llm_nodes},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _node_sampling(
    llm_nodes: Sequence[LlmAgent],
    resolve_sampling: SamplingResolver,
    tier_nodes: Mapping[str, str],
) -> dict[str, TierSampling]:
    """Each LLM node's resolved tier sampling, keyed by graph node name.

    The same ``resolve_sampling`` the graph binds onto the nodes themselves, so
    the params a node runs on and the params its fingerprint attests to can
    never describe different generations. The served model — the fingerprint's
    other half — is not known until the node answers, so the hash itself is
    computed per execution rather than here.
    """
    return {node.name: resolve_sampling(tier_nodes[node.name]) for node in llm_nodes}


def rejection_issues(rendered: str) -> list[ValidationIssue]:
    """Parse the rejection the graph parked in state back into issues."""
    return [ValidationIssue.model_validate(issue) for issue in json.loads(rendered)]


@dataclass(frozen=True)
class Rejected:
    """The graph refused the input: no model ever passed the validity gate.

    Rejected, not *failed*. The input never became a valid System Model, which
    is the submitter's to fix, and the issues say what was wrong with it.
    """

    issues: list[ValidationIssue]


class GraphProducedNothing(RuntimeError):
    """The graph reached neither of its two terminal shapes.

    Ours to own: every path through the topology ends at ``assemble`` or at
    ``reject``, so a final state carrying neither is a defect in the graph
    rather than anything about the job. Each driver catches this and names the
    run it was driving.
    """


GraphResult = Analysis | Rejected


def result_of(final_state: Mapping[str, Any]) -> GraphResult:
    """What a finished drive left behind, as one of the graph's two outcomes.

    The graph has two drivers — the service over a job, the eval harness over a
    corpus case — and each used to read the terminal keys itself: the same two
    membership tests and the same two parses, spelled twice. What differs
    between them is only what they *do* with a rejection, so that is what they
    keep; how a final state is read is here.
    """
    rejection = final_state.get(STATE_REJECTION)
    if rejection is not None:
        return Rejected(issues=rejection_issues(rejection))
    analysis = final_state.get(STATE_ANALYSIS)
    if analysis is None:
        raise GraphProducedNothing("graph produced neither an analysis nor a rejection")
    return Analysis.from_state(analysis)
