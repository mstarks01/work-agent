"""The ADK Workflow graph: nodes wired to prompts, skills, and models.

The topology, for a job selecting frameworks F1..Fn::

    START -> extract -> validate -+-valid--> prepare --+-run_F1-> F1 subgraph -+
                                  |             ^      |                       |
                                  +-invalid-> repair   +-run_Fn-> Fn subgraph -+
                                                 |     |                       |
                                          revalidate   +-skip_Fx---------------+
                                              |                                |
                                    invalid-> reject                           v
                                                                            assemble

``prepare`` is also the run-time precondition gate. It runs each selected
framework's precondition over the one **Valid System Model**, and emits one
route per framework. ``run_<F>`` reaches that framework's lane agents, and
``skip_<F>`` reaches ``assemble`` directly. A refused framework still produces a
block. That block carries no claims, and its ``scope`` states why each lane did
not run, because the envelope checks that the analyses answer the job's
frameworks in order with none dropped. A refusal is therefore not a job failure:
a job that names two frameworks, one of them refused, still serves the other's
analysis.

There is one subgraph per framework, and all of them converge on the one
``assemble``::

    prepare -> lane agents -> join_<F> -> merge_<F> -> critic_<F> -> router_<F>
                                                                        |
                                assemble <-------------------accept-----+
                                   ^  ^                                 |
                                   |  |                          revise v
                                   |  +--accept-- rereview_<F> <- recritic_<F>
                                   |                  |
                                   |           revise v
                                   +---(none)  critic_failed_<F> (raises)

The shared half runs once and the framework half runs N times, which is #162's
ruling drawn as a graph. There is one extraction, one validity gate, one
prepared view and one assembly, because one **Valid System Model** serves every
framework a job selects. Everything between fans out per framework, because a
critic rules its own framework's drafts against its own framework's question,
and no node between ``prepare`` and ``assemble`` ever sees two frameworks'
claims together.

The service builds a graph for one selection. The node names, the state keys and
the instruction digest are all functions of it, so a graph that carried nodes a
job did not select would leave them unfired and their keys unwritten.

Six of the per-framework nodes are structural rather than analytical:

* ``revalidate`` is the second run of the same validate function, after the one
  repair pass. It is two nodes rather than a back-edge because the repair budget
  is exactly one. The graph cannot loop, so it cannot spend a second pass, and
  "one repair, then reject" is visible in the topology rather than enforced by a
  counter.
* ``reject`` is where the second failure lands. It is a terminal node that parks
  the validator's issues in state, for the runner to return as a rejection.
* ``join`` is ADK's ``JoinNode``, a pure barrier with no user code of its own.
  ``merge`` runs :func:`analysis_service.fan_in.fan_in` behind it.
* ``router`` and ``rereview`` are the critic's ``validate`` and ``revalidate``.
  They are one ``route_review`` function run twice, which keeps the mechanical
  check outside ``assemble``, so the graph can re-ask a malformed critic output
  before assembly. Clean output accepts to ``assemble``. A malformed one revises:
  to ``recritic`` the first time, and to ``critic_failed`` the second.
* ``recritic`` is the one bounded critic re-ask, and the review half's
  ``repair``. It is a structural pass rather than a counted one, because the
  graph cannot loop back for a third.
* ``critic_failed`` is where a still-malformed re-ask lands. It raises rather
  than parking issues, because a critic that will not return its own drafts
  whole is this service's defect, which is a ``failed`` job, rather than the
  input's, which is a ``rejected`` one carrying ``ValidationIssue``s.

Every LLM node binds its model through the caller's ``resolve_model``, whose
canonical names are in :data:`analysis_service.model_tiers.LLM_NODES`. It binds
its skills through :mod:`analysis_service.skills`, and its prompt through
:mod:`analysis_service.prompts`. Graph node names must be Python identifiers, so
``analyze/stride`` in the tier config becomes ``analyze_stride_spoofing`` and its
five siblings here. :func:`tier_node_by_graph_node` is the only place that
correspondence lives. The tier config keys one ``analyze/<framework>`` where the
graph builds one node per lane, which is ``model_tiers.toml`` v5's own rule: a
lane is a framework's internal fact, and all of them run the same judgement on
the same tier. The graph names carry the framework as well, and that is the bump
report schema 3.0 earned. A consumer that keys on ``analyze_spoofing`` in
``nodes[].node`` does not error; it matches nothing, silently.

The bookends are deliberately deterministic, because mechanical work belongs in
code. Lane agents cannot receive a malformed view, the report cannot cite an
element the model does not contain, and a quote a finding rests on is matched
against the submitter's own bytes rather than taken on trust. Every check in
this module fails closed: a FunctionNode that raises aborts the workflow, which
the runner turns into a failed job.

One of those checks is for silence rather than for malformed content. An LLM
node that emits no text writes no ``output_key``, so the absence arrives where
the next node reads state, rather than as anything that raised. ``validate`` and
each framework's ``merge`` name it, as :class:`SilentNodeError`, instead of
reading it as an empty value. That is what stops a truncated lane agent deleting
a lane from a report that still finishes green.

On security: the submitted text is untrusted, and it reaches the extraction
prompt and every lane agent's prompt inside a fenced block that names it as data
(OWASP LLM01). The agents read it so they can quote it, which is the whole of
finding-level attribution, and is why ``analyze.md`` carries the same
data-not-instruction paragraph that ``extract.md`` does. Everything a model
emits is untrusted output, and the service validates it before use (LLM05): by
``output_schema`` at the node boundary, then by the System Model gate and the
critic seams here.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, get_args

import anyio.to_thread
from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import START, FunctionNode, JoinNode, Workflow
from google.genai import types
from pydantic import BaseModel, ValidationError

from analysis_service.assertions import (
    AssertionCatalog,
    AssertionRecord,
    CatalogProposal,
)
from analysis_service.basis import unbased_controls
from analysis_service.candidates import generate_candidates
from analysis_service.claims import (
    AnalysisMarks,
    Claim,
    FrameworkAnalysis,
    FrameworkName,
    LaneCoverage,
    Refusal,
    Ruling,
    SharedElementName,
)
from analysis_service.compact import (
    COMPACT_FORMAT,
    EXTRACTION_FORMATS,
    FULL_FORMAT,
    CompactSystemModel,
    ExtractionFormat,
    parse_extraction,
)
from analysis_service.coverage import lane_scope
from analysis_service.critic import (
    Revision,
    assemble_claims,
    critic_view,
    merge_retry,
    review,
)
from analysis_service.domains import select_domain_packs
from analysis_service.evidence import (
    evidence_catalog,
    prepared_view,
    render_catalog,
    render_element_roster,
    render_rows,
)
from analysis_service.factbundle import (
    DispositionRow,
    EmittedFactBundle,
    SourceFactBundle,
    joined,
    resolve_bundle,
)
from analysis_service.fan_in import fan_in
from analysis_service.frameworks import (
    DISCLAIMER_DOC,
    FrameworkPackage,
    FrameworkSchemas,
    PreconditionResult,
    block_type_for,
    package_for,
    run_precondition,
    schemas_for,
)
from analysis_service.identity import IDENTITY_VERSION, build_identity
from analysis_service.knowledge import (
    MAX_CASES,
    MAX_NOTES,
    compose_cases,
    compose_notes,
    select_per_lane,
)
from analysis_service.markdown_loader import MarkdownLoader, estimate_tokens
from analysis_service.model_tiers import ReviewIndependence, TierName
from analysis_service.patch import PatchBatch, apply_patch
from analysis_service.prompts import (
    compose_analyze_prompt,
    compose_assert_prompt,
    compose_critic_prompt,
    compose_extract_prompt,
    compose_facts_prompt,
    compose_inventory_prompt,
    compose_recritic_prompt,
    compose_repair_prompt,
    compose_reread_prompt,
    compose_rows_prompt,
)
from analysis_service.report import (
    AnalysisContext,
    ExecutionEnvelope,
    InputRef,
    Job,
    ModelRepair,
    NodeRun,
    Report,
    disclaimer_for,
)
from analysis_service.retry import TRUNCATION_REMEDY
from analysis_service.sampling import (
    SamplingResolver,
    TierSampling,
)
from analysis_service.skills import (
    compose_critic_skills,
    compose_domain_skills,
    compose_lane_skills,
)
from analysis_service.sources import fenced
from analysis_service.system_model import BoundaryCrossing, SystemModel
from analysis_service.validation import (
    ValidationIssue,
    parse_and_validate,
    repair_scope,
    restore_unimplicated,
)

if TYPE_CHECKING:
    # Type-only, so composing a graph costs no provider-library import and the
    # binding <-> graph reference stays one-directional at run time.
    from analysis_service.binding import NodeBinding

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
#: The facts-first extraction node (#1003 arm B): the sources in, a **Source
#: Fact Bundle** out, and no graph ID anywhere in it.
FACTS_NODE = "facts"
#: The split route's first call (#1003 arm E): what the sources name.
INVENTORY_NODE = "inventory"
#: The split route's second call: what the sources say about that inventory.
ROWS_NODE = "rows"
#: What turns that bundle into the artifacts the rest of the graph reads. Code,
#: not a model: see :func:`~analysis_service.factbundle.resolve_bundle`.
RESOLVE_NODE = "resolve"
#: What renders the split route's inventory for its second call. Code.
READ_INVENTORY_NODE = "reading_inventory"
ASSERT_NODE = "assert"
#: The source-driven review pass (#1003 arms C and D): the sources read once
#: more against the artifacts built from them, out comes a ``PatchBatch``.
REREAD_NODE = "reread"
#: What renders the model and the catalog for that pass, and parks the record
#: the applicator patches. Code, not a model.
READ_CATALOG_NODE = "reading"
#: What applies the batch, transactionally: see
#: :func:`~analysis_service.patch.apply_patch`.
APPLY_NODE = "apply"
#: The head-only entry's terminal node: resolve the catalog this head produced
#: and stop, so #1003's endpoint is scored without paying for the lanes.
CATALOG_NODE = "catalog"
READ_MODEL_NODE = "read"
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

#: Every per-framework role, so :meth:`FrameworkNodes.node` names a set rather
#: than a count. The count was written into that docstring as "six" and a
#: seventh role arrived without it, which is a fact nothing read; a tuple is a
#: fact ``tests/test_graph.py`` reads.
ROLES: tuple[str, ...] = (
    JOIN_ROLE,
    MERGE_ROLE,
    CRITIC_ROLE,
    ROUTER_ROLE,
    RECRITIC_ROLE,
    REREVIEW_ROLE,
    CRITIC_FAILED_ROLE,
)


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
    substitution come from one declaration, so the key function, the ``prepare``
    write and the instruction's replace chain cannot disagree.

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
        """This framework's graph node name for one of :data:`ROLES`."""
        return f"{role}_{_identifier(self.name)}"

    def key(self, artifact: str) -> str:
        """This framework's own state key for one of its artifacts."""
        return f"{artifact}_{_identifier(self.name)}"

    @property
    def run_route(self) -> str:
        """The route ``prepare`` emits when this framework's precondition holds."""
        return f"run_{_identifier(self.name)}"

    @property
    def skip_route(self) -> str:
        """The route ``prepare`` emits when this framework's precondition refuses.

        **One shared route, unlike every other name on this class.** A ``run``
        route reaches this framework's own lane agents, so it has to carry the
        framework; every ``skip`` reaches ``assemble``, so a route per framework
        would declare N copies of one ``prepare -> assemble`` edge — which ADK's
        graph validation refuses outright, and which a build carrying two
        frameworks is the first thing to hit.

        Nothing is lost by sharing it. The route decides topology and nothing
        else: *which* framework was refused is parked under that framework's own
        ``precondition`` key, and ``assemble`` reads it there. A job refusing one
        of two emits ``["skip", "run_<other>"]``, and ADK fires every edge
        matching any value in that list, so exactly the two intended edges fire.
        """
        return SKIP_ROUTE

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
        # The facts-first node resolves on the **extraction** tier row, because
        # it is the extraction stage under another order. #1003 asks that
        # comparable semantic stages run the same model configuration, and one
        # key for both is how that holds by construction rather than by an
        # operator keeping two rows in step.
        FACTS_NODE: "extract",
        # Both calls of the split route sit on the extraction tier row, for the
        # reason the single call does: they are the extraction stage under
        # another division of labour, and #1003 asks that comparable semantic
        # stages run one model configuration.
        INVENTORY_NODE: "extract",
        ROWS_NODE: "extract",
        REPAIR_NODE: "repair",
        # The review pass resolves on the **repair** tier row, because it is a
        # bounded repair pass: one call, over artifacts and the sources they
        # came from, ending in a patch code applies or discards.
        REREAD_NODE: "repair",
        ASSERT_NODE: "assert",
        **{
            node: tier
            for name in frameworks
            for node, tier in FrameworkNodes(name).tier_nodes.items()
        },
    }


# --- Routes -----------------------------------------------------------------

Entry = Literal["extract", "prepare", "extract-only", "assert-only", "head-only"]

ENTRY_EXTRACT: Entry = "extract"
ENTRY_PREPARE: Entry = "prepare"
"""The analysis eval mode's entry: start at ``prepare`` over a blessed model
seeded in state, so a recall miss cannot be blamed on an element ``extract``
never produced."""

ENTRY_EXTRACT_ONLY: Entry = "extract-only"
"""The extraction eval mode: run ``extract`` and stop, leaving its emission at
:data:`STATE_EXTRACTED_MODEL` for the caller to put through the same
:func:`~analysis_service.validation.parse_and_validate` gate ``validate`` uses.
Spending every framework's lane agents and critics to score an extraction would
be that many kinds of noise on one number."""

ENTRY_ASSERT_ONLY: Entry = "assert-only"
#: The entries that build a ``prepare`` node, and so the ones an assertion
#: pass can sit in front of.
PREPARING_ENTRIES: frozenset[Entry] = frozenset({ENTRY_EXTRACT, ENTRY_PREPARE})

ENTRY_HEAD_ONLY: Entry = "head-only"
"""#1003's arm entry: run one arm's head — the reading node, the validity gate,
the bounded repair, and whichever of the two passes this graph carries — then
resolve the catalog and stop.

**The primary endpoint is scored on the catalog, and no lane writes one.** An
end-to-end run would pay every lane agent and every critic on the judgement
tier to produce findings the endpoint never reads: measured against a recorded
STRIDE sweep, that is roughly seventeen times the spend of the head alone. So
the comparison runs the half it grades.

It is not the assertion eval mode. That one seeds a blessed model and asks what
the sources state about it; this one extracts the model too, which is the half
the arms differ in."""

#: The entries whose graph ends in a resolved catalog, and so the ones an
#: assertion pass or a source review has a reader in. Derived from the two
#: that end in one, because "would anything read this pass" is the question
#: both guards ask and neither should answer twice.
CATALOGUING_ENTRIES: frozenset[Entry] = PREPARING_ENTRIES | {ENTRY_HEAD_ONLY}

#: The entries whose graph builds a reading node, and so the ones a transport
#: and a strategy are properties of. **One reader**: the builder asks it to
#: decide what to build, and
#: :meth:`~analysis_service.deployment.Deployment.pipeline` asks it to decide
#: what to pass — and a deployment that answered it a second way would hand a
#: graph a strategy the graph then refused to build for.
EXTRACTING_ENTRIES: frozenset[Entry] = frozenset(
    {ENTRY_EXTRACT, ENTRY_EXTRACT_ONLY, ENTRY_HEAD_ONLY}
)
"""The assertion eval mode: render a **Valid System Model** seeded in state,
run ``assert`` over it and the sources, and stop.

The model is seeded rather than extracted for the reason :data:`ENTRY_PREPARE`
seeds one: a row this node could not bind to an element would otherwise be
unattributable between the two readings, and the question this mode asks is
what the sources *state*, not whether two calls named one component alike."""

#: Which order a graph reads its sources in. ``graph-first`` is production and
#: every arm-A run: ``extract`` emits a **System Model** in one pass.
#: ``facts-first`` is #1003's arm B, where ``facts`` emits a **Source Fact
#: Bundle** in local handles and ``resolve`` turns it into the same artifacts.
#: Both end at the same validity gate and the same bounded repair, so what a
#: comparison between them measures is the reading order.
ExtractionStrategy = Literal["graph-first", "facts-first", "facts-split"]

GRAPH_FIRST: ExtractionStrategy = "graph-first"
FACTS_FIRST: ExtractionStrategy = "facts-first"

FACTS_SPLIT: ExtractionStrategy = "facts-split"
"""The facts-first reading, in two calls rather than one.

``facts-first`` asks one call to name what the sources hold **and** state what
they say about it. ``graph-first`` splits that same work across ``extract`` and
``assert``. A comparison between those two therefore moves the reading order
and the number of calls together, and #1003's own design cannot say which of
them a difference belongs to.

This route holds the order and splits the calls: ``inventory`` names the
things, the interactions and the zones, and ``rows`` states the facts about
them. Against the other two it is the cell that separates the confound."""

#: Every strategy, derived from the literal so the two cannot disagree.
EXTRACTION_STRATEGIES: frozenset[str] = frozenset(get_args(ExtractionStrategy))

#: The strategies that read the sources facts-first, however many calls they
#: spread the reading over. **The one reader of "does this graph carry its own
#: assertion rows"**: both shapes do, so both refuse an appended assertion pass
#: and both satisfy a source review's need for a catalog. A comparison that
#: named one of them would have let the other quietly take a second pass.
FACTS_STRATEGIES: frozenset[str] = frozenset({FACTS_FIRST, FACTS_SPLIT})


ROUTE_VALID = "valid"
ROUTE_INVALID = "invalid"
ROUTE_UNCONVERTIBLE = "unconvertible"
"""The compact transport's own refusal route, from ``validate`` to ``reject``.

Only the compact route can take it, and only when the emission is not a
well-formed compact model at all. It is separate from :data:`ROUTE_INVALID`
because the two failures have different answers: an invalid model is one
``repair`` can read, and a payload that did not expand is not a System Model in
any shape ``prompts/repair.md`` was written for. Sending it there would ask a
repair pass to perform a format conversion, and #938 rules that out — the
conversion rate is a number the rollout measures rather than a fault a second
model call hides.

``revalidate`` never emits it: ``repair`` writes a full System Model whichever
transport extraction used."""
SKIP_ROUTE = "skip"
"""The run-time precondition gate's refusal route, shared by every framework.

One route rather than one per framework, because every ``skip`` edge has the
same target: ``assemble``. See
:attr:`FrameworkNodes.skip_route`, which reads it, for the whole of the
argument."""
ROUTE_REVIEW = "review"
ROUTE_SETTLED = "settled"
"""The fan-in's two routes. ``merge`` takes ``review`` when there is a draft to
rule on, and ``settled`` only when the lanes drafted nothing at all. A critic
shown an empty view is a paid call that returns nothing, and the view it would
have read is not evidence of anything.

**A draft citing an unknown ground takes ``review`` like any other.** Its own
grounds still say it is conditional, and
:meth:`~analysis_service.claims.Claim.settled_by_grounds` still supplies the
references that say so — but that is a fact about the certainty of the claim,
not about whether its argument follows from what it cites, and a route around
the critic answered the second question with the first."""
ROUTE_ACCEPT = "accept"
ROUTE_REVISE = "revise"
"""The critic re-ask route. ``route_review`` takes it when the critic's output
fails the mechanical check: from ``router`` it reaches the bounded ``recritic``
re-ask, and from ``rereview`` — the second look after that re-ask — it reaches
``critic_failed``, since a repeated failure is ours to own, not the input's to
be rejected for."""


def _routed(route: str | list[str], output: dict[str, Any]) -> Event:
    """An ADK ``Event`` carrying the route the graph's edges match on.

    ``route=`` is a convenience kwarg ADK's before-validator lifts onto
    ``actions.route``; it is not a declared field, so the type checker cannot
    see it. Routing through one constructor keeps that a single suppression.

    A **list** of routes fires every edge that matches any of them, which is how
    ``prepare`` decides N frameworks in one node: it emits one route per selected
    framework rather than one route naming a subset of the selection.
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
STATE_ELEMENT_ROSTER = "element_roster"
# The domain packs this job's model earned, already composed to text. One key
# for every lane of every framework, because the selection is a fact about the
# model rather than about a lane — or about a framework.
STATE_DOMAIN_SKILLS = "domain_skills"
STATE_PREVIOUS_MODEL = "previous_model"
STATE_VALIDATION_ISSUES = "validation_issues"
# The machine-readable half of what ``validate`` parks for ``repair``: the
# normalized model the issues cite and the scope a repair of them may change,
# read back by ``revalidate`` to put every uncited element back (#675 D01).
STATE_REPAIR_BASELINE = "repair_baseline"
# What the repair pass was allowed to change and what it changed anyway,
# written by ``revalidate`` and carried onto the report by ``assemble``.
STATE_MODEL_REPAIR = "model_repair"

# The catalog rendered for the review pass, beside the model it was built over.
# A rendered key: written once, read by a model, never read back here.
STATE_ASSERTION_ROWS = "assertion_rows"
# What ``reread`` emits: a ``PatchBatch``, before code applies or discards it.
STATE_PATCH_BATCH = "patch_batch"
# What ``apply`` made of that batch: one ``OperationOutcome`` per operation, and
# whether the batch was discarded whole. Written by ``apply``, read by a driver.
STATE_PATCH_OUTCOMES = "patch_outcomes"

# What the facts-first node emits: a ``SourceFactBundle``, in local handles,
# before code resolves it into a model and a proposal.
STATE_SOURCE_FACTS = "source_facts"
# What the split route's first call emits, kept apart from the second's so the
# join reads each call for the half it owns.
STATE_SOURCE_INVENTORY = "source_inventory"
# That inventory rendered for the second call. A rendered key: written once,
# read by a model, never read back here.
STATE_INVENTORY = "inventory"
# What ``resolve`` made of every row of that bundle: one ``DispositionRow`` per
# input row, so a fact that reached neither the graph nor the catalog is
# attributable rather than missing. Written by ``resolve`` and read by a driver.
STATE_BUNDLE_DISPOSITIONS = "bundle_dispositions"

STATE_EXTRACTED_MODEL = "extracted_model"
# What ``extract`` emitted, kept apart from the key it arrived in. ``repair``
# writes its own emission over :data:`STATE_EXTRACTED_MODEL`, so after a
# repair nothing else holds the first pass, and a driver that archives a run
# reads it here whichever route the gate took (#961).
STATE_FIRST_PASS = "first_pass"
# What ``assert`` emits: a ``CatalogProposal``, before code resolves its rows
# into a catalog. Structured, because a driver reads it back and resolves it —
# see :func:`~analysis_service.assertions.resolve_catalog`.
STATE_ASSERTION_PROPOSAL = "assertion_proposal"
# What ``prepare`` made of that proposal: an ``AssertionRecord``, the catalog
# every lane selected from and the rows the resolver refused. Written once by
# ``prepare`` and read by every ``merge`` and by ``assemble``, so the catalog an
# agent chose from, the one its choice resolves against and the one the report
# embeds are one value resolved once. Absent on a graph that runs no pass.
STATE_ASSERTION_CATALOG = "assertion_catalog"
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
# What each selected framework was asked for, as ``name -> options``, exactly as
# the input ladder validated it against that package's own ``options`` model.
#
# **Job data, not graph shape.** Two jobs selecting the same frameworks with
# different options share one built graph, so the values arrive per run and the
# driver seeds them. A framework whose options select which requirements apply
# produces a different answer under different ones, which is why they reach the
# lane agents and the block rather than only the report's ``job`` field.
STATE_FRAMEWORK_OPTIONS = "framework_options"

#: The job-wide state keys ``analyze.md`` templates, which every lane of every
#: framework reads as one value. With :data:`LANE_ARTIFACTS` and ``{lane}`` they
#: are every placeholder the prompt declares, which ``tests/test_graph.py``
#: holds, so :meth:`~analysis_service.execution.GraphRun.lane_material` keeps
#: the whole of what a lane read and a key the prompt gains cannot go unkept.
LANE_SHARED_KEYS: tuple[str, ...] = (
    STATE_INPUT_TEXT,
    STATE_SYSTEM_MODEL,
    STATE_BOUNDARY_CROSSINGS,
    STATE_EVIDENCE_CATALOG,
    STATE_ELEMENT_ROSTER,
    STATE_DOMAIN_SKILLS,
)

# The per-framework keys, as artifact names. Each is spelled
# ``<artifact>_<framework>`` by :meth:`FrameworkNodes.key`, so two frameworks'
# fan-ins never write one key.
#
# ``drafts`` holds one framework's merged drafts whole, because that is what
# ``assemble_claims`` merges rulings onto; ``draft_view`` is the *prompt* view of
# the same drafts, built by :func:`~analysis_service.critic.critic_view` and
# narrowed to the fields a verdict is reached from. ``marks`` is every service-owned mark that fan-in produced, as
# one :class:`~analysis_service.claims.AnalysisMarks` — one key rather than one per
# mark kind, since they share an owner, a standing and a policy.
FRAMEWORK_RENDERED_ARTIFACTS: tuple[str, ...] = (
    "draft_view",
    "previous_review",
    "critic_issues",
    "draft_roster",
    "unreconciled_drafts",
)
# ``precondition`` holds what this framework's own gate answered about the
# shared model, as one of the three :data:`~analysis_service.frameworks.
# PreconditionResult` states. Written by ``prepare`` for every selected
# framework and read by ``assemble``, so the block a refused framework produces
# states the reason its lanes did not run.
FRAMEWORK_STRUCTURED_ARTIFACTS: tuple[str, ...] = (
    "drafts",
    "coverage",
    # Unit -> why this job could not settle it. Written by the fan-in, read by
    # ``assemble``, and empty for a package that defers nothing.
    "deferred",
    # Unit -> why the package's own rules ruled it out of this model, before
    # any lane ran. Written by ``prepare``, read by ``assemble``, and empty
    # for a package whose rules rule nothing out.
    "ruled_out",
    "marks",
    "precondition",
    "retrieved",
    "reviewed",
    "accepted",
    # The first critic pass's rulings, as returned, and the drafted IDs its
    # problems named. Written by the first ``route_review`` on its ``revise``
    # edge, read by the second, which merges the re-ask onto them.
    "first_review",
    "repairable",
)

#: Keys holding bytes a model reads and Python does not. Written once by the
#: node that derives them, then templated into an instruction by ADK.
SHARED_RENDERED_KEYS: frozenset[str] = frozenset(
    {
        STATE_INPUT_TEXT,
        STATE_SYSTEM_MODEL,
        STATE_BOUNDARY_CROSSINGS,
        STATE_EVIDENCE_CATALOG,
        STATE_ELEMENT_ROSTER,
        STATE_DOMAIN_SKILLS,
        STATE_PREVIOUS_MODEL,
        STATE_VALIDATION_ISSUES,
        STATE_ASSERTION_ROWS,
        STATE_INVENTORY,
    }
)

#: Keys holding values a later node or a driver reads back. Every one of them
#: round-trips through a Pydantic model or a plain mapping.
SHARED_STRUCTURED_KEYS: frozenset[str] = frozenset(
    {
        STATE_SOURCE_TEXTS,
        STATE_SOURCE_FACTS,
        STATE_SOURCE_INVENTORY,
        STATE_BUNDLE_DISPOSITIONS,
        STATE_PATCH_BATCH,
        STATE_PATCH_OUTCOMES,
        STATE_EXTRACTED_MODEL,
        STATE_FIRST_PASS,
        STATE_ASSERTION_PROPOSAL,
        STATE_ASSERTION_CATALOG,
        STATE_VALID_MODEL,
        STATE_DOMAIN_PACKS,
        STATE_ANALYSIS,
        STATE_REJECTION,
        STATE_FRAMEWORK_OPTIONS,
        STATE_REPAIR_BASELINE,
        STATE_MODEL_REPAIR,
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
    (:meth:`GraphKeys.of`), so a node checks against the graph it is running in.
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
    :class:`~analysis_service.retry.TruncatedCompletionError` catches that one
    upstream, off ``finish_reason``, before a validator sees a partial document
    and misreports it as malformed. This class remains the net for the silent
    half, where nothing but the absent key says anything happened at all.

    Absence is deliberately **not** read as emptiness. An agent that finds no
    threats in its lane emits ``{"threats": []}`` and its key is written; a
    truncated one writes nothing. The two look identical once a missing key is
    defaulted to an empty list, which is how a silently dropped lane would
    reach a finished report.

    Ours to own rather than the input's, so it fails the job rather than
    rejecting it — the same split :func:`fail_review` makes, and the reason this
    is a ``RuntimeError`` beside :class:`~analysis_service.pipeline.PipelineError`
    rather than a ``ValueError`` beside the input errors.
    """


# Every SilentNodeError says the same two things: nothing was written, and here
# is the knob. Kept in one place because the two raise sites are one bug.
#
# Only the first half is this module's. The knob is the same one
# :class:`~analysis_service.retry.TruncatedCompletionError` names, because the two
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
    service over a job, the eval harness over a corpus case — and neither copies
    a field across itself. Two copies would have to agree with nothing checking
    that they did, and a field added here and missed in one driver would produce
    a report with a block silently absent. :meth:`into_report` is the one place
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
    # loose rather than as an :class:`~analysis_service.report.AnalysisContext`
    # because that record's other field — the instruction digest — is a fact
    # about the *built graph* rather than about this job, and the graph is not
    # something a node holds. :meth:`context` joins them where the driver stamps
    # the rest of the run's static provenance.
    domain_packs: list[str]
    # What the repair pass was allowed to change and what it changed anyway;
    # ``None`` where no repair ran.
    model_repair: ModelRepair | None = None
    # What the assertion pass produced; ``None`` where the graph ran none.
    assertions: AssertionRecord | None = None

    def context(self, instruction_sha256: str) -> AnalysisContext:
        """This analysis's context block, given the built graph's digest.

        The join is here rather than in each driver so the service and the eval
        harness cannot record the block differently — the same reason the two
        share one :class:`~analysis_service.execution.GraphExecutor`.
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
        (:class:`~analysis_service.execution.GraphRun`); ``pipeline`` is the
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
            model_repair=self.model_repair,
            assertions=self.assertions,
            disclaimer=disclaimer_for(self.assertions),
            analysis_context=self.context(pipeline.instruction_sha256),
            execution=ExecutionEnvelope(
                identity_version=IDENTITY_VERSION,
                build=dict(build_identity()),
                review_independence=pipeline.review_independence,
                extraction_format=pipeline.extraction_format,
                extraction_strategy=pipeline.extraction_strategy,
            ),
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
            "model_repair": (
                None
                if self.model_repair is None
                else self.model_repair.model_dump(mode="json")
            ),
            "assertions": (
                None
                if self.assertions is None
                else self.assertions.model_dump(mode="json")
            ),
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
            model_repair=(
                None
                if data["model_repair"] is None
                else ModelRepair.model_validate(data["model_repair"])
            ),
            assertions=(
                None
                if data["assertions"] is None
                else AssertionRecord.model_validate(data["assertions"])
            ),
        )


def _block_of(payload: dict[str, Any]) -> FrameworkAnalysis:
    """One analysis block, validated as the shape its own framework registered.

    The same dispatch :class:`~analysis_service.report.Report` runs on the way in,
    and it falls back to the neutral base for the same reason: a block naming a
    framework this build does not carry reads as a base claim rather than
    raising, which is the honest outcome.
    """
    block_type = block_type_for(payload.get("framework")) or FrameworkAnalysis
    return block_type.model_validate(payload)


def render(value: Any) -> str:
    """The one way a structured value enters a prompt: pretty, stable JSON."""
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def render_fenced(value: Any) -> str:
    """A rendered value plus a fence it cannot close.

    ``render`` is ``json.dumps`` with ``ensure_ascii=False``. It escapes quotes,
    backslashes, ``\\n`` and ``\\r`` — and **not** backticks, and **not**
    U+2028, U+2029 or U+0085, which it passes through as themselves. Those three
    are line terminators to ``str.splitlines`` and to most renderers, so a value
    holding one plus a backtick run has both halves of a closing fence.

    A System Model carries caller words by rule — ``source_excerpt`` verbatim,
    and ``notes`` quoting what a speaker said — and a merged draft carries a
    lane agent's own prose. Against a fence written into the prompt file, such a
    value closes the block and lands the bytes after it in instruction position.
    Against this one it cannot, because the fence is sized over the body: a body
    holding ``` gets a four-backtick fence.

    That is the hole :func:`~analysis_service.sources.render_sources` closes one
    node upstream, with the same technique and the same sizing rule — sized once
    over the whole rendered document, because this seam renders one JSON blob
    rather than N caller-controlled blocks.

    **Every rendered key a prompt interpolates goes through here**, and no
    prompt file writes a fence of its own. A static fence is only ever as long
    as itself, so the two cannot be mixed: one written in the file would be the
    one a body could close.
    """
    return fenced(render(value))


def unfence(rendered: str) -> str:
    """The body back out of :func:`render_fenced`.

    One rendered key is also read back by Python: the parked rejection, which
    the driver turns into the issues a caller is shown. It still ships fenced,
    because the same string is interpolated into ``repair.md`` and a key that
    shipped bare would be the one a body could close.
    """
    # Split on the delimiter `render_fenced` joined with, not on every line
    # boundary Python knows. `str.splitlines` also breaks on U+2028, U+2029 and
    # U+0085, which `render` passes through as themselves -- so splitting that
    # way and re-joining with "\n" rewrites a terminator the value carried into
    # a raw newline, and a raw newline inside a JSON string is what `json.loads`
    # refuses. The inverse of a join is a split on the same string.
    lines = rendered.split("\n")
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1] == lines[0]:
        return "\n".join(lines[1:-1])
    return rendered


# --- Deterministic node functions -------------------------------------------
#
# Plain functions, wrapped in FunctionNodes below. Parameters bind by name
# from session state; ``ctx`` is ADK's node context, whose ``state`` writes
# become the session's state delta.


#: How many node bodies may hold a worker thread at once, across every job in
#: the process. Separate from anyio's default limiter on purpose: that one is
#: also what `require_subject` verifies a bearer token through, and a graph that
#: could fill it would make every arriving caller wait for a node body.
#:
#: Small because a node body is CPU-bound, so more threads than cores buys
#: queueing rather than throughput, and because the point of the bound is to
#: leave the default limiter's slots for the request path.
_NODE_THREADS = anyio.CapacityLimiter(8)


def _node(func: Callable[..., Any], name: str) -> FunctionNode:
    """Wrap a node body in a FunctionNode that runs it off the event loop.

    ADK calls a synchronous node body inline in the coroutine that drives the
    workflow, so its CPU time is the event loop's: while it runs, the process
    serves no other job, no health check and no event stream, and the job
    deadline's timer cannot fire either, because a cooperative timeout cannot
    interrupt a synchronous call. These bodies are not cheap — one of them
    fuzzy-matches every quote a model emitted against the whole submitted
    source — so the loop is the wrong thread for them.

    ADK awaits a body that is a coroutine function, so wrapping it in one moves
    the work to a worker thread and gives the loop back. It does **not** make
    the deadline able to stop a running body -- nothing can, short of a process
    boundary -- so the wrapper holds the task until the body ends and the
    deadline lands between nodes. The
    wrapper keeps the wrapped signature, which is what ADK binds parameters
    from, and touches nothing else: a node reaches the session only through
    :class:`SessionState`, whose writes are plain dict writes, and only one
    node of a run is in flight at a time.
    """

    @functools.wraps(func)
    async def offloaded(**kwargs: Any) -> Any:
        # On a limiter of its own, because the default one is shared and
        # `require_subject` verifies every bearer token through it: node bodies
        # filling all forty slots put an arriving caller behind a whole body
        # before their token was even read.
        #
        # A cancelled await here does NOT stop the body. `run_sync` takes its
        # token outside the scope `abandon_on_cancel=False` shields, so the
        # deadline returns the token, settles the job and frees its subject's
        # slot while the thread runs on. Nothing in-process can stop a running
        # Python call, so what keeps that harmless is that a body is short:
        # `grounding.MAX_REPAIR_SECONDS_PER_BODY` is the bound that makes it
        # so, and it is the reason this is a wrapper and not a supervisor.
        return await anyio.to_thread.run_sync(
            functools.partial(func, **kwargs), limiter=_NODE_THREADS
        )

    return FunctionNode(func=offloaded, name=name)


def validate_extraction(
    ctx,
    keys: GraphKeys,
    extracted_model: dict | None = None,
    source_texts: dict | None = None,
    extraction_format: str = FULL_FORMAT,
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

    ``extraction_format`` is the transport the node ahead of this one wrote in,
    and it is bound to the node rather than read from state: ``validate`` sits
    behind ``extract`` and ``revalidate`` behind ``repair``, which writes a full
    System Model whichever transport extraction used. Reading the shape to
    decide would be exactly the inference #938 forbids — the route a run took
    has to be a fact the build recorded, not a guess about whichever payload
    happened to parse. Both go through :func:`~analysis_service.compact.parse_extraction`,
    which is also what the eval harness calls, so no run is graded against a
    gate production never applied.

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
    state = keys.state(ctx)
    baseline = state.get(STATE_REPAIR_BASELINE)
    if baseline is None:
        # The first gate, on every route out of it: what arrived is kept
        # before ``repair`` can write over the key it arrived in.
        state.put(STATE_FIRST_PASS, extracted_model)
    model, issues = parse_extraction(
        extracted_model, extraction_format, sources=source_texts or {}
    )
    if model is None and extraction_format != FULL_FORMAT:
        # The emission was not a compact model at all, so there is nothing for
        # ``repair`` to repair: see :data:`ROUTE_UNCONVERTIBLE`. The rejection
        # carries the conversion issues, and the payload is parked beside them
        # so a reader can see what arrived.
        state.prompt(STATE_PREVIOUS_MODEL, render_fenced(extracted_model))
        state.prompt(
            STATE_VALIDATION_ISSUES,
            render_fenced([issue.model_dump(mode="json") for issue in issues]),
        )
        return _routed(ROUTE_UNCONVERTIBLE, {"issue_count": len(issues)})
    # The second pass, over what ``repair`` returned: every element the
    # issues did not name is put back as it was, then the whole is validated
    # again, so a "while I'm here" edit never reaches the report and the
    # repair prompt's rule is enforced rather than asked for. Only a parsed
    # model can be overlaid; a repair that fails the schema outright is parked
    # as-is and takes the invalid edge, which from here is the rejection.
    if baseline is not None and model is not None:
        repair = ModelRepair(scope=baseline["scope"], implicated=baseline["implicated"])
        if repair.scope == "elements":
            overlaid, restored = restore_unimplicated(
                baseline["model"], model.model_dump(mode="json"), repair.implicated
            )
            repair = repair.model_copy(update={"restored": restored})
            model, issues = parse_and_validate(
                overlaid, normalize_ids=True, sources=source_texts or {}
            )
        state.put(STATE_MODEL_REPAIR, repair.model_dump(mode="json"))
    if issues or model is None:
        parked = extracted_model if model is None else model.model_dump(mode="json")
        # Fenced for the same reason as the agents' copy: this is the model
        # built from caller words, handed straight back to a model.
        state.prompt(STATE_PREVIOUS_MODEL, render_fenced(parked))
        state.prompt(
            STATE_VALIDATION_ISSUES,
            render_fenced([issue.model_dump(mode="json") for issue in issues]),
        )
        scope, implicated = repair_scope(issues, parked)
        state.put(
            STATE_REPAIR_BASELINE,
            {"model": parked, "scope": scope, "implicated": implicated},
        )
        return _routed(ROUTE_INVALID, {"issue_count": len(issues)})

    # Logged and nothing else, after the gate has passed the model: a control
    # the source does not echo is a diagnostic and never a route. See
    # :mod:`analysis_service.basis` for why token overlap cannot gate.
    for flag in unbased_controls(model, source_texts or {}):
        logger.warning("stated control with no basis in its source: %s", flag)
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


def render_model(valid_model: dict) -> str:
    """The model as every agent reads it: stripped, then fenced.

    **The one reader of "what does the model look like to a model".** Two
    entries write :data:`STATE_SYSTEM_MODEL` — ``prepare`` in the analysing
    graph and ``read`` in the assertion one — and they call this rather than
    each fencing a view of their own, so no agent can be shown a model another
    agent would not recognise.
    """
    return render_fenced(_without_source_fields(valid_model))


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
def prepare_analysis(
    valid_model: dict,
    ctx,
    keys: GraphKeys,
    frameworks: Sequence[FrameworkName],
    domain_loader: MarkdownLoader,
    package_loaders: Mapping[FrameworkName, MarkdownLoader],
    assertions: bool = False,
) -> Event:
    """Run each framework's precondition, then derive what its lane agents read.

    Crossings are computed here rather than extracted, so no agent can be handed
    a crossing that contradicts the zones in the model it is reading. The same
    argument carries three more artifacts, all of them functions of the
    validated model alone:

    * **The evidence catalog** (:mod:`analysis_service.evidence`) — the closed
      set of facts an agent may cite. Agents are shown its **references only**:
      each spells out the fact it stands for, and the fields behind it are the
      element and flow IDs of the model rendered directly above, so sending the
      resolved objects too would restate what the agent is about to read.
      Derived here *and* again at the fan-in rather than passed between them,
      so the set an agent chose from and the set its choice resolves against
      cannot differ.
    * **Candidates** (:mod:`analysis_service.candidates`) — the structural
      conditions each lane's rules fire on, parked per ``(framework, lane)`` so
      an agent reads only its own. They are *leads*, and nothing downstream of
      the prompt reads them: a candidate cannot become a claim, cannot ground
      one, and does not appear in the report.
    * **Domain packs** (:mod:`analysis_service.domains`) — the reference
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
    parameters from session state. So is ``assertions``: whether an ``assert``
    node sits ahead of this one is a fact about the built graph, and it says
    whether a proposal is owed. Where it is, this node resolves it through
    :meth:`~analysis_service.assertions.AssertionRecord.of` — once, here,
    because every later reader takes the record off
    :data:`STATE_ASSERTION_CATALOG` rather than resolving again — and the
    evidence catalog is derived from the model *and* the settled rows. An
    ``assert`` node that wrote nothing fails the job here, on the rule
    :func:`merge_drafts` applies to a silent lane: a pass the deployment
    selected and a pass that never ran are not the same report.

    Candidate facts carry caller-authored attribute values, so they are fenced
    with :func:`render_fenced` exactly as the model is — the bytes are a subset
    of what already rides in ``{system_model}`` and they get the same
    treatment. The pack text is repo-authored and needs none.

    **This node is also the run-time precondition gate**, and it is here because
    here is where the topology is still one decision: the **Valid System Model**
    exists, and the fan-out has not happened yet. Each selected framework's
    precondition runs once over that one model, through
    :func:`~analysis_service.frameworks.run_precondition`, and the answer is parked
    under that framework's own key for ``assemble`` to read. Only ``satisfied``
    earns the framework's artifacts and its ``run`` route; ``refuted`` and
    ``undecidable`` both take its ``skip`` route straight to ``assemble``, where
    the framework still produces a block. Nothing is derived for a refused
    framework, so its candidates, its retrieval and its lane prompts cost
    nothing.
    """
    model = SystemModel.model_validate(valid_model)
    state = keys.state(ctx)
    record = _resolve_assertions(state, model) if assertions else None
    held = None if record is None else record.catalog
    if held is not None:
        # ADR 0034's migration, applied where the catalog answers. The model
        # every consumer reads from here on is the projected one, and it is put
        # back on ``STATE_VALID_MODEL`` so the lane agents' model, the report's
        # embedded model and the model a ground resolves against stay one
        # value. A zone predicate projects into ``trust_zone``, so the
        # crossings below are derived after this and never before.
        model, projected, catalog = prepared_view(model, held)
        if projected:
            valid_model = model.model_dump(mode="json")
            state.put(STATE_VALID_MODEL, valid_model)
    else:
        catalog = evidence_catalog(model)
    crossings = model.boundary_crossings()
    packs = select_domain_packs(model)

    options = state.get(STATE_FRAMEWORK_OPTIONS) or {}
    # Before the fan-out, which is the earliest node that holds the selection and
    # its options together. ``assemble`` checks the same thing, because a graph
    # entered past here never runs this node — but by then 23 ``strong``-tier
    # calls have been paid for, and a lane agent has been told to rule at a level
    # nobody supplied.
    _check_options(frameworks, options)
    state.prompt(STATE_SYSTEM_MODEL, render_model(valid_model))
    state.prompt(
        STATE_BOUNDARY_CROSSINGS,
        render_fenced([crossing.model_dump(mode="json") for crossing in crossings]),
    )
    state.prompt(STATE_EVIDENCE_CATALOG, render_catalog(catalog, held))
    # Beside the model rather than instead of it: a lane agent reasons over the
    # whole model and *selects* out of this. Neutral by construction — the roster
    # enumerates the one shared model, so every framework's lanes read the same
    # table and a package registered tomorrow inherits it.
    state.prompt(STATE_ELEMENT_ROSTER, render_element_roster(model))
    state.prompt(STATE_DOMAIN_SKILLS, compose_domain_skills(domain_loader, packs))
    state.put(STATE_DOMAIN_PACKS, list(packs))

    candidate_count = 0
    knowledge_count = 0
    routes: list[str] = []
    preconditions: dict[FrameworkName, PreconditionResult] = {}
    for name in frameworks:
        nodes = FrameworkNodes(name)
        package = nodes.package
        result = run_precondition(package, model)
        state.put(nodes.key("precondition"), result)
        preconditions[name] = result
        if result != "satisfied":
            routes.append(nodes.skip_route)
            continue
        routes.append(nodes.run_route)
        loader = package_loaders[name]
        candidates = generate_candidates(model, package.lanes, package.rules, held)
        # Selected for every lane at once, because a lane's tie-break reads what
        # earlier lanes were sent: `select_per_lane` is the one reader of that
        # accumulation, so the offline coverage lint measures the selection this
        # job actually makes rather than a second spelling of it.
        fired_by_lane = [
            {candidate.rule_id for candidate in candidates[lane.lane].candidates}
            for lane in nodes.lanes
        ]
        notes_by_lane = select_per_lane(
            package.knowledge.notes, fired_by_lane, MAX_NOTES
        )
        cases_by_lane = select_per_lane(
            package.knowledge.cases, fired_by_lane, MAX_CASES
        )
        retrieved: list[str] = []
        ruled_out: dict[str, str] = {}
        for position, lane in enumerate(nodes.lanes):
            candidate_set = candidates[lane.lane]
            # The package's own rules may rule a lane's units out of this model
            # before its agent runs; the agent is told, and the block's scope
            # carries them as not-applicable with the rule's reason.
            lane_ruled_out = package.record.ruled_out(
                model, options.get(name) or {}, lane.lane
            )
            ruled_out.update(lane_ruled_out)
            # Retrieval is by *fired* rule, so a lane that triggered nothing gets
            # nothing: the material follows the leads rather than the lane.
            notes = notes_by_lane[position]
            cases = cases_by_lane[position]
            # Keyed by the placeholder each fills, which is the vocabulary the
            # prompt file uses; the lane turns that into its own four state keys.
            lane_state = lane.state(
                {
                    "candidates": render_fenced(candidate_set.model_dump(mode="json")),
                    "scope": lane_scope(
                        lane.lane,
                        package,
                        model,
                        candidate_set,
                        options.get(name) or {},
                        units=package.record.units_for(
                            options.get(name) or {}, lane.lane
                        ),
                        ruled_out=tuple(lane_ruled_out),
                    ),
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
        state.put(nodes.key("ruled_out"), ruled_out)
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

    return _routed(
        # Deduplicated, because the refusal route is shared: two refused
        # frameworks emit one ``skip`` between them, and ADK fires the one edge
        # it names once.
        list(dict.fromkeys(routes)),
        {
            "element_count": len(model.elements()),
            "crossing_count": len(crossings),
            "evidence_count": len(catalog),
            "candidate_count": candidate_count,
            "domain_packs": list(packs),
            "knowledge_doc_count": knowledge_count,
            "preconditions": preconditions,
            "assertion_count": None if record is None else len(record.catalog.entries),
            "assertions_refused": (None if record is None else record.refused_rows()),
        },
    )


def _resolve_assertions(state: SessionState, model: SystemModel) -> AssertionRecord:
    """The catalog this job runs on: built once, gated once, parked once.

    **One seam, and two ways to reach it.** ``assert`` and ``resolve`` each
    leave a proposal, which is resolved here against the model the validity
    gate passed. A graph carrying the review pass leaves an
    :class:`~analysis_service.assertions.AssertionRecord` instead, because
    ``apply`` built it over the model it patched — and it is put through
    :meth:`~analysis_service.assertions.AssertionRecord.over` here rather than
    trusted, so a catalog a lane selects from answered the same rules whichever
    node produced it. There is no path that reaches ``prepare`` ungated.
    """
    sources = state.get(STATE_SOURCE_TEXTS) or {}
    held = state.get(STATE_ASSERTION_CATALOG)
    if held is not None:
        patched = AssertionRecord.model_validate(held)
        record = AssertionRecord.over(
            patched.catalog,
            model,
            sources,
            proposed=patched.proposed,
            issues=patched.issues,
        )
    else:
        proposed = state.get(STATE_ASSERTION_PROPOSAL)
        if proposed is None:
            raise SilentNodeError(
                f"nothing was written to {STATE_ASSERTION_PROPOSAL!r}, so the"
                " assertion pass this deployment selected never ran."
                f" {_TRUNCATION_HINT}"
            )
        record = AssertionRecord.of(
            CatalogProposal.model_validate(proposed), model, sources
        )
    state.put(STATE_ASSERTION_CATALOG, record.model_dump(mode="json"))
    return record


def _held_assertions(state: SessionState) -> AssertionCatalog | None:
    """The catalog ``prepare`` parked, or ``None`` on a graph that ran no pass."""
    record = state.get(STATE_ASSERTION_CATALOG)
    return None if record is None else AssertionRecord.model_validate(record).catalog


def prepare_node(
    keys: GraphKeys,
    frameworks: Sequence[FrameworkName],
    domain_loader: MarkdownLoader,
    package_loaders: Mapping[FrameworkName, MarkdownLoader],
    assertions: bool = False,
) -> FunctionNode:
    """The ``prepare`` node, with this deployment's Markdown roots bound to it.

    Everything but ``valid_model`` is bound here rather than read from state for
    the same reason: a repo path, the graph's own framework list and whether
    an ``assert`` node runs ahead are facts about the build, not about the job,
    and ADK binds a FunctionNode's parameters from session state.
    """

    def prepare_analysis_node(valid_model: dict, ctx) -> Event:
        return prepare_analysis(
            valid_model,
            ctx,
            keys,
            frameworks,
            domain_loader,
            package_loaders,
            assertions,
        )

    return _node(prepare_analysis_node, PREPARE_NODE)


def _claims_of(payload: object) -> list[Any]:
    """The list inside an LLM node's emission, or empty if the node never ran.

    Lane agents and review nodes emit ``{"claims": [...]}`` rather than a bare
    array, because a bare ``list[...]`` schema is one ADK cannot convert into a
    response format — it sends none and the node generates unconstrained. The
    wrapper is the schema's shape, not the domain's, so it is unwrapped here at
    the boundary and nothing downstream carries it. The field name is neutral
    because the prompt asking for it is shared; see
    :class:`~analysis_service.claims.ProposalBatch`.

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


def _batch_of(payload: object) -> dict[str, Any]:
    """A lane node's emission as the batch its schema validated, or an empty one.

    The node already validated and dumped the batch, so state holds both its
    lists, and re-validating the whole payload keeps the invalid entries the
    node salvaged. A key that was never written reads as a batch of nothing,
    the same absence :func:`_claims_of` reads for the review nodes.

    A payload that is a mapping goes back as it stands, including one carrying
    no ``claims``. That shape is nothing this graph writes, so the caller's
    validation refuses it rather than reading it as an empty batch — a lane
    whose drafts became unreadable is not a lane that drafted nothing.
    """
    return payload if isinstance(payload, dict) else {"claims": []}


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
) -> Event:
    """Park one framework's fan-in and route on what is left for the critic.

    **One of these per selected framework.** The merge itself is
    :func:`~analysis_service.fan_in.fan_in`, which takes the lane batches as
    the agents emitted them and returns the drafts, the marks, the deferred
    units and the coverage as one value. This node reads the batches out of
    state, calls it once, parks each part of what it returns under this
    framework's own key, and routes: ``review`` when there is a draft,
    ``settled`` when there is none, so a critic is never called to read an
    empty view (:data:`ROUTE_REVIEW`).

    ``source_texts`` defaults to ``None`` so the in-process engine, which drives
    a hand-authored model with no job behind it, is not failed on a citation
    that is not wrong.

    An agent that emitted **nothing** fails the job here, before any of that. A
    framework's lanes are what make its output that framework's method rather
    than a list of findings, so a lane that never ran is not a smaller report —
    it is a different method, and one whose absence nothing downstream can see: a
    critic rules what it is given, a summary counts what exists, and a breakdown
    omits a lane with no claims rather than carrying a zero. A truncated agent
    would delete a lane of the analysis and finish green.

    A lane that ran and found nothing is a different thing and stays legal — it
    emits ``{"claims": []}``, its key is written, and ``_batch_of`` reads it as
    the empty batch it is. That distinction is the whole check: the absent key,
    not the empty list.

    Two state keys, two shapes, on purpose. ``drafts`` holds the drafts whole,
    because that is what ``assemble_claims`` merges rulings onto to build the
    block. ``draft_view`` is the *prompt* view, built by
    :func:`~analysis_service.critic.critic_view` and narrowed to the fields a
    verdict is actually reached from. The re-ask builds its own view through
    the same function, so the two passes cannot disagree about what a critic
    reads.
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
    proposal_batch = nodes.schemas.proposals
    model = SystemModel.model_validate(valid_model)
    merged = fan_in(
        {
            lane.lane: proposal_batch.model_validate(
                _batch_of(state.get(lane.drafts_key))
            )
            for lane in lanes
        },
        nodes.package,
        model,
        source_texts or {},
        state.get(nodes.key("ruled_out")) or {},
        _held_assertions(state),
    )
    state.put(nodes.key("deferred"), merged.deferred)
    state.put(
        nodes.key("drafts"), [draft.model_dump(mode="json") for draft in merged.drafts]
    )
    state.put(nodes.key("marks"), merged.marks.model_dump(mode="json"))
    state.put(
        nodes.key("coverage"), [row.model_dump(mode="json") for row in merged.coverage]
    )
    state.prompt(
        nodes.key("draft_view"),
        render_fenced(
            critic_view(
                merged.drafts,
                model,
                repaired=merged.marks.repaired_quotes,
                unverified=merged.marks.unverified_grounds,
                assertions=_held_assertions(state),
            )
        ),
    )
    return _routed(
        ROUTE_REVIEW if merged.drafts else ROUTE_SETTLED,
        merge_summary(nodes.name, merged.drafts, merged.marks),
    )


def merge_summary(
    framework: FrameworkName, drafts: Sequence[Claim], marks: AnalysisMarks
) -> dict[str, Any]:
    """What the ``merge`` node outputs, and so the user turn its critic receives.

    ADK hands a node's output to the next node through ``to_user_content``, so
    this dictionary is the critic's user turn. The one reader of that shape: the
    node returns it, and the critic replay builds its request from it.
    """
    return {
        "framework": framework,
        "draft_count": len(drafts),
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

    **The check and the re-ask view are one value.**
    :func:`~analysis_service.critic.review` returns either an
    :class:`~analysis_service.critic.Accepted` or a
    :class:`~analysis_service.critic.Revision`, and the Revision carries its own
    messages, its roster and the drafts those messages name. So the prompt and
    the check cannot disagree about which claims are in trouble: they come out
    of one call over one set. This function parks what the value carries and
    takes the edge it names, and composes nothing.

    Both of a framework's review nodes run this same function — what differs is
    where their ``revise`` edge points (``recritic`` for the first look,
    ``critic_failed`` for the second), exactly as the two validate nodes share
    one function.

    **Both of this framework's keys are read from state rather than bound as
    parameters**, and that is what makes N critics expressible at all: ADK binds
    a FunctionNode's parameters by name, and a name derived per framework cannot
    be spelled in a signature. Reading them keeps the absence handling
    honest — **an LLM node that emits no
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

    An absent *reviewed* key with no drafts beside it is the ``settled`` route
    out of ``merge``: the lanes drafted nothing, the critic was never called,
    and an empty ruling set against an empty draft set reconciles, so the same
    check takes ``accept``. A draft its own grounds make conditional does not
    reach here that way — it is shown to the critic like any other, and a
    ruling missing for it is a dropped draft the re-ask is asked to supply.
    """
    model = SystemModel.model_validate(valid_model)
    state = keys.state(ctx)
    package_drafts = _drafts_of(state.get(nodes.key("drafts")), nodes.package)
    ruled = _claims_of(state.get(nodes.key("reviewed")))
    parked = AnalysisMarks.model_validate(state.get(nodes.key("marks")) or {})
    first = state.get(nodes.key("first_review"))
    if first is not None:
        # The second look. The re-ask was asked to change only what the
        # problems named and to carry every other ruling across unchanged;
        # this is where that is held rather than hoped for. What it changed
        # beyond its brief is recorded beside the first pass's problems and
        # discarded, and ``reviewed`` is rewritten so ``assemble`` reads the
        # merged set and never the re-ask's own.
        ruled, drift = merge_retry(
            first,
            _canonical_rulings(ruled, nodes.schemas),
            state.get(nodes.key("repairable")) or [],
            [draft.id for draft in package_drafts],
        )
        state.put(nodes.key("reviewed"), {"claims": list(ruled)})
        parked = parked.model_copy(
            update={"unreconciled_rulings": [*parked.unreconciled_rulings, *drift]}
        )
        state.put(nodes.key("marks"), parked.model_dump(mode="json"))
    rulings = rulings_of(ruled, nodes.schemas)
    outcome = review(
        package_drafts,
        rulings,
        model,
        repaired=parked.repaired_quotes,
        unverified=parked.unverified_grounds,
        assertions=_held_assertions(state),
    )
    if isinstance(outcome, Revision):
        # ``previous_review`` is the parked payload itself rather than anything
        # recomputed, because what the re-ask must reconcile with is the bytes
        # the critic actually returned.
        state.prompt(nodes.key("previous_review"), render_fenced(ruled))
        state.prompt(nodes.key("critic_issues"), render_fenced(outcome.messages))
        state.prompt(nodes.key("draft_roster"), render_fenced(outcome.roster))
        state.prompt(
            nodes.key("unreconciled_drafts"), render_fenced(outcome.unreconciled)
        )
        # The same bytes, kept where code reads them back: the second look
        # merges the re-ask onto these, and ``repairable`` is the whole of
        # what it may replace.
        state.put(nodes.key("first_review"), _canonical_rulings(ruled, nodes.schemas))
        state.put(nodes.key("repairable"), list(outcome.repairable))
        # Recorded onto this framework's marks before the re-ask runs, so the
        # report says how the first pass failed even when the re-ask repairs it
        # completely. Merged rather than assigned: ``marks`` already holds what
        # fan-in produced, and a second look must not drop it.
        state.put(
            nodes.key("marks"),
            parked.model_copy(
                update={
                    "unreconciled_rulings": [
                        *parked.unreconciled_rulings,
                        *outcome.problems,
                    ]
                }
            ).model_dump(mode="json"),
        )
        return _routed(ROUTE_REVISE, {"issue_count": len(outcome.messages)})
    # The marker ``assemble`` reads a framework as finished by. ``reviewed``
    # alone cannot say so: it holds the first critic's malformed rulings for
    # as long as the re-ask runs.
    state.put(nodes.key("accepted"), True)
    return _routed(ROUTE_ACCEPT, {"reviewed_count": outcome.count})


def _drafts_of(drafts: list | None, package: FrameworkPackage) -> list[Claim]:
    """One framework's parked drafts, revalidated as its own record type."""
    return [package.record.model_validate(draft) for draft in drafts or []]


def rulings_of(ruled: Sequence[Any], schemas: FrameworkSchemas) -> list[Ruling]:
    """One critic's emission, revalidated as this framework's own ruling type.

    Public because it is the one reader of "what shape is a critic's answer",
    and the graph is not its only caller: ``evals/critic_review`` replays a
    critic outside the graph and has to parse what comes back the same way.
    Parsing with the neutral :class:`~analysis_service.claims.Ruling` instead
    refuses every STRIDE ruling, because ``frameworks/stride/critic.md`` asks
    for a ``confidence`` the neutral model forbids.
    """
    return list(schemas.rulings.model_validate({"claims": list(ruled)}).claims)


def _canonical_rulings(ruled: Sequence[Any], schemas: FrameworkSchemas) -> list[dict]:
    """The same emission, as the ruling model spells it.

    What :func:`~analysis_service.critic.merge_retry` compares. A provider may
    spell one ruling two ways across two calls -- an optional key present as
    ``null`` in one and absent in the other -- and a comparison over the raw
    payloads would read that as a re-ask changing a ruling it was told to carry
    across. The model's own dump is one spelling per ruling.
    """
    return [ruling.model_dump(mode="json") for ruling in rulings_of(ruled, schemas)]


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
    # ``review`` returned a Revision here by construction; assemble_claims
    # raises the CriticOutputError naming exactly what still does not reconcile.
    assemble_claims(
        _drafts_of(state.get(nodes.key("drafts")), nodes.package),
        rulings_of(_claims_of(state.get(nodes.key("reviewed"))), nodes.schemas),
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

    **This node is triggered once per framework, and it assembles on the last
    of those triggers only.** ADK schedules a ``FunctionNode`` on each incoming
    trigger rather than on all of its predecessors, so a two-framework job
    reaches here twice. :func:`_framework_finished` is the gate: a trigger that
    finds any selected framework still running returns without building
    anything, and the trigger that finds them all finished builds the one
    report. A two-framework job therefore builds one report rather than an
    incomplete one it then overwrites.

    Refusing the later triggers as well as the earlier ones is what makes it
    exactly one. Once every framework is finished, every remaining trigger would
    compose the same blocks from the same parked artifacts, so a second pass
    can only spend the time again — and :data:`STATE_ANALYSIS`, already written,
    is what says the pass happened.

    **Two triggers that run at once are not serialized, and need not be.**
    Node bodies run on the worker pool, and the gate is a read of the session
    state followed by a write, so two triggers that both find every framework
    finished before either writes both assemble. What keeps that safe is the
    assembly, not the gate: it is a pure function of the parked artifacts, so
    both passes write the same value and the second costs time and nothing
    else. A lock here would buy one assembly instead of two identical ones,
    at the price of a lock on the one node every framework's route ends at.

    **A join node is the wrong fix here**, and the run-time gate is why. ADK's
    ``JoinNode`` waits for *all* predecessors to complete, and a refused
    framework's subgraph never runs, so its terminal node never completes and the
    join would never fire. This gate reads each framework's own finished state
    instead, where a refusal is one of the two ways to be finished.
    """
    model = SystemModel.model_validate(valid_model)
    state = keys.state(ctx)
    options = state.get(STATE_FRAMEWORK_OPTIONS) or {}
    # Checked on every trigger, before the gate, because it is a driver contract
    # rather than assembly work: a job whose options are missing says so on the
    # first framework to finish rather than on the last.
    _check_options(frameworks, options)
    pending = [
        name
        for name in frameworks
        if not _framework_finished(state, FrameworkNodes(name))
    ]
    if pending or state.get(STATE_ANALYSIS) is not None:
        return {"assembled": False, "pending_frameworks": pending}
    blocks = [
        _framework_block(
            FrameworkNodes(name),
            state,
            model,
            disclaimers[name],
            options.get(name) or {},
        )
        for name in frameworks
    ]
    repair = state.get(STATE_MODEL_REPAIR)
    record = state.get(STATE_ASSERTION_CATALOG)
    analysis = Analysis(
        system_model=model,
        boundary_crossings=model.boundary_crossings(),
        analyses=blocks,
        marks=_model_marks(model),
        domain_packs=list(domain_packs or []),
        model_repair=None if repair is None else ModelRepair.model_validate(repair),
        assertions=None if record is None else AssertionRecord.model_validate(record),
    )
    state.put(STATE_ANALYSIS, analysis.to_state())
    return {
        "claim_count": sum(len(block.claims) for block in blocks),
        "rejected_count": sum(len(block.rejected_claims) for block in blocks),
        "framework_count": len(blocks),
    }


def _framework_finished(state: SessionState, nodes: FrameworkNodes) -> bool:
    """Has this framework reached a state its block can be built from?

    Two ways, and a framework takes exactly one of them. Its own router wrote
    the ``accepted`` marker, which is what says its critic reconciled; or its
    precondition refused it at ``prepare``, which is what sent it down the
    ``skip`` route with no subgraph to run at all. A framework part-way through
    its lanes, or one whose critic is in the bounded re-ask, has taken neither.

    The refusal half reads :data:`_REFUSALS`, the same table that says *why*
    a refused framework did not run, so what counts as a refusal is written
    down once.

    A graph entered past ``prepare`` wrote no precondition key, and
    :func:`_refusal` reads that absence as ``satisfied``. This reads it
    the same way: such a framework is finished once it is accepted, and never
    by refusal.
    """
    if state.get(nodes.key("accepted")):
        return True
    return str(state.get(nodes.key("precondition"))) in _REFUSALS


class MissingFrameworkOptions(ValueError):
    """A driver ran a framework without seeding the options that framework needs.

    **A driver contract, checked where it is first readable.** Options are job
    data rather than graph shape, so the driver seeds them per run
    (:data:`STATE_FRAMEWORK_OPTIONS`); a package that declares a required option
    cannot have its block built without one, because no package field carries a
    default and inventing one is the thing this whole path refuses to do.

    Named and raised early rather than left to fail inside a block's own
    construction. Without this, a driver that forgot the key got a raw Pydantic
    error out of a scope helper, naming a model rather than the framework, the
    option or the key to seed — and it arrived after the provider billed every
    node either way.
    """


def _check_options(
    frameworks: Sequence[FrameworkName], options: Mapping[str, Any]
) -> None:
    """Every selected framework's options are present and well-formed, or raise.

    Checked over the whole selection at once so a driver missing two is told
    about two. The values themselves go through each package's own options model,
    which is the one declaration of what that framework needs.
    """
    problems = []
    for name in frameworks:
        try:
            package_for(name).options.model_validate(options.get(name) or {})
        except ValidationError as exc:
            fields = sorted(
                ".".join(str(part) for part in error["loc"]) for error in exc.errors()
            )
            problems.append(f"{name}: {', '.join(fields)}")
    if problems:
        raise MissingFrameworkOptions(
            f"the job's options do not satisfy every selected framework"
            f" ({'; '.join(problems)}); a driver seeds them under"
            f" {STATE_FRAMEWORK_OPTIONS!r}, and no package field carries a default"
        )


def _framework_block(
    nodes: FrameworkNodes,
    state: SessionState,
    model: SystemModel,
    disclaimer: str,
    options: Mapping[str, Any],
) -> FrameworkAnalysis:
    """One framework's finished analysis block, from its own parked artifacts.

    The block type is the package's own, so a package's narrowed claim arrays and
    narrowed summary are built here rather than validated into existence
    downstream. Its summary comes from that same type
    (:meth:`~analysis_service.claims.FrameworkAnalysis.summarize`), which is what
    keeps the count the block declares and the count its own validator recomputes
    from disagreeing.

    **A mark lands on the block that declares it.** The fan-in produces one
    :class:`~analysis_service.claims.AnalysisMarks` carrying every mark kind; only
    the fields this block type actually declares are set from it. That is how
    STRIDE's ``missing_mitigations`` reaches a STRIDE block while a framework
    that recommends nothing carries no such field and is handed nothing —
    without either side naming the other.

    **A refused framework reaches here too**, on its ``skip`` route out of
    ``prepare`` rather than through its own critic. Every key its subgraph would
    have written is absent, so its claims, coverage and marks read as absent
    already; the one thing it adds is its block's own ``scope``, which states why
    the framework did not run. That is what keeps the envelope's own check
    answerable — the analyses must answer the job's frameworks in order, with
    none dropped — while the framework's judgement stays unspent.

    **An option a block declares as a field is stamped from the job's own
    selection.** ASVS records the level it ruled at, so a reader holding the
    block alone can tell which requirement set produced the answer. The rule is
    one line and neutral: a field named after an option gets that option's value.
    A package whose block declares no such field is handed nothing.
    """
    schemas = nodes.schemas
    package = nodes.package
    # An earlier trigger of ``assemble`` (see :func:`assemble_report`) can
    # arrive while this framework is still running: after ``merge`` parked its
    # drafts and before its critic ruled, or while a re-ask is replacing a
    # malformed first ruling still sitting on ``reviewed``. Either way reading
    # the drafts against those rulings would fail the whole job as a critic
    # that dropped every draft. A framework whose router did not accept
    # therefore reads as unfinished: no drafts, no rulings. A critic that never
    # reconciles is caught on its own router, which raises in ``critic_failed``.
    accepted = state.get(nodes.key("accepted"))
    drafts = _drafts_of(state.get(nodes.key("drafts")) if accepted else None, package)
    rulings = rulings_of(
        _claims_of(state.get(nodes.key("reviewed")) if accepted else None), schemas
    )
    claims, rejected = assemble_claims(drafts, rulings, model, schemas)
    marks = AnalysisMarks.model_validate(state.get(nodes.key("marks")) or {})
    retrieved = state.get(nodes.key("retrieved")) or {}
    return schemas.block(
        framework=package.name,
        framework_version=package.version,
        disclaimer=disclaimer,
        claims=claims,
        rejected_claims=rejected,
        scope=schemas.block.scope_entries(
            lanes=package.lanes,
            claims=(*claims, *rejected),
            options=options,
            refusal=_refusal(package, state.get(nodes.key("precondition"))),
            deferred=state.get(nodes.key("deferred")) or {},
            ruled_out=state.get(nodes.key("ruled_out")) or {},
        ),
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
        **{
            field: value
            for field, value in options.items()
            if field in schemas.block.model_fields
        },
    )


# What a refused framework's block says about each lane that did not run, by
# the state its own precondition answered. **The two are never collapsed**,
# because the remedy differs: ``refuted`` says do not name this framework for
# this system, and ``undecidable`` says the input never said, which the
# submitter answers by submitting more.
_REFUSALS: Mapping[str, Refusal] = MappingProxyType(
    {
        "refuted": Refusal(
            "not-applicable",
            "the {framework} precondition refutes this system, so no {framework}"
            " lane ran; this framework does not apply to a system of this shape",
        ),
        "undecidable": Refusal(
            "undecidable",
            "the input never says whether {framework} applies to this system, so"
            " no {framework} lane ran; submitting more about the system settles it",
        ),
    }
)


def _refusal(package: FrameworkPackage, result: object) -> Refusal | None:
    """Why this framework did not run, as a scope state and a reason, or ``None``.

    What the reason is *used for* is the block's own — the neutral base answers
    in lanes, and a package holding a requirement catalog answers in
    requirements. What the reason *says* is the graph's, because the gate is the
    graph's.

    ``result`` is read as ``satisfied`` when the key is absent, which is a graph
    entered past ``prepare``: the eval harness's analysis mode seeds a blessed
    model and runs no gate, and reporting a refusal there would describe one that
    never happened.
    """
    refusal = _REFUSALS.get(str(result))
    if refusal is None:
        return None
    return Refusal(refusal.state, refusal.reason.format(framework=package.name))


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
    :mod:`analysis_service.pipeline` rather than once at build time.

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
    #: What each LLM node was told, per node: its size and its own digest. The
    #: hash above says a prompt edit happened; this says which node moved and by
    #: how much, which is what a reader comparing two sweeps needs.
    node_instructions: dict[str, InstructionSize]
    #: The selection this graph was built for, in block order. A driver needs it
    #: to stamp the job's own ``frameworks`` list, and the envelope checks the two
    #: agree — so it rides with the built graph rather than being passed beside it.
    frameworks: tuple[FrameworkName, ...]
    #: This graph's node names against the tier keys they resolve on. Built per
    #: selection (:func:`tier_node_by_graph_node`), so a caller computing a
    #: fingerprint reads it from here rather than rebuilding it.
    tier_nodes: dict[str, str]
    #: How far this deployment required criticism to sit from the analysis it
    #: checks. Carried on the built graph rather than looked up where the report
    #: is stamped, because the tier config is what enforced it and the driver
    #: holds no config — the same reason ``instruction_sha256`` rides here.
    review_independence: ReviewIndependence = "shared"
    #: Which transport ``extract`` wrote in, and therefore which schema it was
    #: given and which prompt described it. Recorded rather than inferred: #938
    #: requires the selected route to be a fact of the build, because a reader
    #: who works it out from whichever shape parsed cannot tell a compact run
    #: from a full run whose output happened to be compatible. It rides on the
    #: built graph for the reason ``review_independence`` does — the config
    #: decided it and the driver holds no config.
    #:
    #: It is not in the execution fingerprint, and it does not need to be: the
    #: two routes read different composed instructions, so ``instruction_sha256``
    #: already separates them, and ``prompts/extract-compact.md`` names the
    #: format version so a transport change cannot leave that digest standing.
    #:
    #: ``None`` on a graph with no ``extract`` node — the analysis and assertion
    #: eval entries, which are seeded a model rather than extracting one. A
    #: default of ``"full"`` there would be a fact with no reader: the report
    #: would name a transport nothing in that run used.
    extraction_format: ExtractionFormat | None = FULL_FORMAT
    #: Which order the head of this graph reads its sources in, on the same
    #: reasoning: a reader who worked it out from the report's own System Model
    #: would find the two strategies identical by construction, because both
    #: pass the same gate and produce the same shape. ``None`` on a graph with
    #: no extraction node, for the reason the transport is ``None`` there.
    extraction_strategy: ExtractionStrategy | None = GRAPH_FIRST


def _generate_content_config(sampling: TierSampling) -> types.GenerateContentConfig:
    """One node's own tier's decoding params, and nothing else.

    ``resolve_sampling`` hands each node its :class:`TierSampling`, so nodes on
    different tiers never share one graph-wide constant.

    **The per-request timeout is not here, and no ``http_options`` is set.** It
    was, and the carrier changed its unit: ADK hands
    ``types.HttpOptions.timeout`` to LiteLLM unchanged, the field is documented
    in milliseconds, and LiteLLM reads seconds. The timeout is now a LiteLLM
    kwarg on the adapter, where the unit is LiteLLM's own — see
    :func:`~analysis_service.binding.build_tier_adapters`. Nothing in this file
    reads ``config/resilience.toml`` any more, which is why no builder below
    carries it.
    """
    return sampling.to_generate_content_config()


def _llm_node(
    *,
    name: str,
    tier_node: str,
    instruction: str,
    output_schema: Any,
    output_key: str,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
) -> LlmAgent:
    """One LLM node: its model, its full instruction, its emitted schema.

    ``include_contents='none'`` is set explicitly: a node sees its instruction
    and the state templated into it, never the transcript of the nodes before
    it. Model *and* sampling are resolved off the one canonical node name
    (:func:`tier_node_by_graph_node`), so each node runs on its own tier's
    decoding params from the config shared with the eval suite — no node on
    library defaults, none on another tier's sampling. The tier key is passed in
    rather than looked up, because the map is now built per selection and the
    caller already holds it. The per-request timeout is not here: it rides the
    adapter ``resolve_model`` returns.
    """
    return LlmAgent(
        name=name,
        model=resolve_model(tier_node),
        instruction=instruction,
        output_schema=output_schema,
        output_key=output_key,
        include_contents="none",
        generate_content_config=_generate_content_config(resolve_sampling(tier_node)),
    )


#: The schema each extraction transport asks the model to fill. A table rather
#: than a branch, so a third transport is a row here and a row in
#: ``prompts.compose_extract_prompt``, and ``tests/test_compact.py`` holds this
#: one against :data:`~analysis_service.compact.EXTRACTION_FORMATS`.
EXTRACTION_SCHEMAS: dict[str, type[BaseModel]] = {
    FULL_FORMAT: SystemModel,
    COMPACT_FORMAT: CompactSystemModel,
}


def _extract_node(
    prompt_loader: MarkdownLoader,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
    extraction_format: str = FULL_FORMAT,
) -> LlmAgent:
    """The extraction node, shared by the production graph and eval mode 1.

    The transport decides two things together — the schema the model fills and
    the prompt that describes it — and both are read off ``extraction_format``
    here, so a run cannot be asked for one shape and told about another.
    """
    return _llm_node(
        name=EXTRACT_NODE,
        tier_node="extract",
        instruction=compose_extract_prompt(prompt_loader, extraction_format),
        output_schema=EXTRACTION_SCHEMAS[extraction_format],
        output_key=STATE_EXTRACTED_MODEL,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
    )


def _facts_node(
    prompt_loader: MarkdownLoader,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
) -> LlmAgent:
    """The facts-first extraction node: the sources in, one bundle out.

    It sees no model and no graph ID, which is the whole of what #1003 arm B
    changes at this end. Everything after :func:`resolve_source_facts` is the
    route arm A runs.
    """
    return _llm_node(
        name=FACTS_NODE,
        tier_node="extract",
        instruction=compose_facts_prompt(prompt_loader),
        output_schema=EmittedFactBundle,
        output_key=STATE_SOURCE_FACTS,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
    )


def _inventory_node(
    prompt_loader: MarkdownLoader,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
) -> LlmAgent:
    """The split route's first call: what the sources name, and nothing about it."""
    return _llm_node(
        name=INVENTORY_NODE,
        tier_node="extract",
        instruction=compose_inventory_prompt(prompt_loader),
        output_schema=EmittedFactBundle,
        output_key=STATE_SOURCE_INVENTORY,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
    )


def _rows_node(
    prompt_loader: MarkdownLoader,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
) -> LlmAgent:
    """The split route's second call: what the sources say about that inventory."""
    return _llm_node(
        name=ROWS_NODE,
        tier_node="extract",
        instruction=compose_rows_prompt(prompt_loader),
        output_schema=EmittedFactBundle,
        output_key=STATE_SOURCE_FACTS,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
    )


def render_inventory(inventory: dict) -> str:
    """The first call's emission as the second call reads it.

    Fenced, for the reason a model is fenced: it is built from caller words and
    handed straight back to a model. Its ``facts`` list is dropped rather than
    shown — the second call owns that half, and showing it an empty one invites
    it to read the absence as a gap to fill.
    """
    return render_fenced(
        {
            field: inventory.get(field, [])
            for field in ("mentions", "interactions", "unresolved")
        }
    )


def read_inventory(ctx, keys: GraphKeys, source_inventory: dict | None = None) -> dict:
    """Render the inventory for the call that states facts about it."""
    if source_inventory is None:
        raise SilentNodeError(
            f"nothing was written to {STATE_SOURCE_INVENTORY!r}, so the second"
            f" call has no inventory to read. {_TRUNCATION_HINT}"
        )
    keys.state(ctx).prompt(STATE_INVENTORY, render_inventory(source_inventory))
    return {
        "mentions": len(source_inventory.get("mentions", [])),
        "interactions": len(source_inventory.get("interactions", [])),
    }


def _read_inventory_func(keys: GraphKeys) -> Callable[..., Any]:
    """The inventory renderer, with this graph's key families bound to it."""

    def reading(ctx, source_inventory: dict | None = None) -> dict[str, Any]:
        return read_inventory(ctx, keys, source_inventory)

    return reading


def resolve_source_facts(
    ctx,
    keys: GraphKeys,
    source_facts: dict | None = None,
    source_texts: dict | None = None,
    source_inventory: dict | None = None,
) -> dict[str, Any]:
    """Turn one bundle into the artifacts the rest of the graph already reads.

    Three writes, and none of them is new machinery. The model goes to
    :data:`STATE_EXTRACTED_MODEL`, which is where ``extract`` puts its emission,
    so the validity gate and the bounded repair behind it run unchanged. The
    rows whose handles resolved go to :data:`STATE_ASSERTION_PROPOSAL`, which is
    where the ``assert`` node puts its proposal, so ``prepare`` resolves them
    through the seam it always used — **there is no second way to inject a
    catalog**, and the gate cannot be stepped around. The dispositions go to
    :data:`STATE_BUNDLE_DISPOSITIONS` for a driver that asks what every input
    row came to.

    The proposal rather than the resolved record, because a model that fails the
    gate is repaired and the rows should bind to the model the gate passed. The
    record this stage computed is what #1003's offline comparison reads; the
    job's catalog is what ``prepare`` builds from the same rows.
    """
    if source_facts is None:
        raise SilentNodeError(
            f"nothing was written to {STATE_SOURCE_FACTS!r}, so there are no"
            f" source facts to resolve. {_TRUNCATION_HINT}"
        )
    state = keys.state(ctx)
    bundle = SourceFactBundle.model_validate(source_facts)
    ignored: tuple[DispositionRow, ...] = ()
    if source_inventory is not None:
        # The split route: each call is read for the half it owns, and a call
        # that answered the other half is reported rather than merged in.
        bundle, ignored = joined(
            SourceFactBundle.model_validate(source_inventory), bundle
        )
    resolution = resolve_bundle(bundle, source_texts or {})
    state.put(STATE_EXTRACTED_MODEL, resolution.model.model_dump(mode="json"))
    state.put(STATE_ASSERTION_PROPOSAL, resolution.proposal.model_dump(mode="json"))
    state.put(
        STATE_BUNDLE_DISPOSITIONS,
        [row.model_dump(mode="json") for row in (*ignored, *resolution.dispositions)],
    )
    return {
        "elements": len(resolution.model.elements()),
        "assertions": len(resolution.proposal.assertions),
        "gaps": len(resolution.gaps),
    }


def _resolve_node_func(keys: GraphKeys) -> Callable[..., Any]:
    """The bundle resolver, with this graph's key families bound to it."""

    def resolve(
        ctx,
        source_facts: dict | None = None,
        source_texts: dict | None = None,
        source_inventory: dict | None = None,
    ) -> dict[str, Any]:
        return resolve_source_facts(
            ctx, keys, source_facts, source_texts, source_inventory
        )

    return resolve


def _reread_node(
    prompt_loader: MarkdownLoader,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
) -> LlmAgent:
    """The source-driven review node: the sources and the artifacts in, a batch out."""
    return _llm_node(
        name=REREAD_NODE,
        tier_node="repair",
        instruction=compose_reread_prompt(prompt_loader),
        output_schema=PatchBatch,
        output_key=STATE_PATCH_BATCH,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
    )


def read_for_review(
    ctx,
    keys: GraphKeys,
    valid_model: dict,
    source_texts: dict | None = None,
) -> dict[str, Any]:
    """Render what the review pass reads, and park the record it will patch.

    The model and the rows go to rendered keys, through the two functions every
    other reader of them calls, so no agent is shown a model or a row another
    agent would not recognise. The record itself is parked structurally,
    because ``apply`` patches it and ``prepare`` gates whatever comes out.

    It resolves the proposal here rather than leaving that to ``prepare``. The
    review has to see identities it can retract, and an identity exists only
    once the rows are resolved — so the resolution moves ahead of the pass that
    reads it, and ``prepare`` then reads the record this produced.
    """
    state = keys.state(ctx)
    proposed = state.get(STATE_ASSERTION_PROPOSAL)
    if proposed is None:
        raise SilentNodeError(
            f"nothing was written to {STATE_ASSERTION_PROPOSAL!r}, so the review"
            f" pass has no catalog to read. {_TRUNCATION_HINT}"
        )
    model = SystemModel.model_validate(valid_model)
    record = AssertionRecord.of(
        CatalogProposal.model_validate(proposed), model, source_texts or {}
    )
    state.prompt(STATE_SYSTEM_MODEL, render_model(valid_model))
    state.prompt(STATE_ASSERTION_ROWS, render_rows(record.catalog))
    state.put(STATE_ASSERTION_CATALOG, record.model_dump(mode="json"))
    return {"elements": len(model.elements()), "rows": len(record.catalog.entries)}


def apply_source_review(
    ctx,
    keys: GraphKeys,
    valid_model: dict,
    patch_batch: dict | None = None,
    source_texts: dict | None = None,
) -> dict[str, Any]:
    """Apply what the review proposed, or leave the artifacts as they were.

    :func:`~analysis_service.patch.apply_patch` decides both, and this node only
    parks what it answered. A discarded batch writes the model and the record
    back unchanged, which is what makes the failure cost visible rather than
    silent: the outcomes say every operation was rolled back and why.
    """
    if patch_batch is None:
        raise SilentNodeError(
            f"nothing was written to {STATE_PATCH_BATCH!r}, so the review pass"
            f" this deployment selected never ran. {_TRUNCATION_HINT}"
        )
    state = keys.state(ctx)
    held = state.get(STATE_ASSERTION_CATALOG)
    if held is None:
        raise SilentNodeError(
            f"nothing was written to {STATE_ASSERTION_CATALOG!r}, so there is no"
            " record for the review to patch"
        )
    result = apply_patch(
        PatchBatch.model_validate(patch_batch),
        SystemModel.model_validate(valid_model),
        AssertionRecord.model_validate(held),
        source_texts or {},
    )
    state.put(STATE_VALID_MODEL, result.model.model_dump(mode="json"))
    state.put(STATE_ASSERTION_CATALOG, result.record.model_dump(mode="json"))
    state.put(
        STATE_PATCH_OUTCOMES,
        {
            "rolled_back": result.rolled_back,
            "outcomes": [
                outcome.model_dump(mode="json") for outcome in result.outcomes
            ],
        },
    )
    return {"applied": len(result.applied), "rolled_back": result.rolled_back}


def _read_for_review_func(keys: GraphKeys) -> Callable[..., Any]:
    """The review's reading node, with this graph's key families bound to it."""

    def reading(
        valid_model: dict, ctx, source_texts: dict | None = None
    ) -> dict[str, Any]:
        return read_for_review(ctx, keys, valid_model, source_texts)

    return reading


def _apply_node_func(keys: GraphKeys) -> Callable[..., Any]:
    """The patch applicator, with this graph's key families bound to it."""

    def apply(
        valid_model: dict,
        ctx,
        patch_batch: dict | None = None,
        source_texts: dict | None = None,
    ) -> dict[str, Any]:
        return apply_source_review(ctx, keys, valid_model, patch_batch, source_texts)

    return apply


def park_catalog(ctx, keys: GraphKeys, valid_model: dict) -> dict[str, Any]:
    """Resolve the catalog this head produced, park it, and stop.

    The head-only entry's terminal node, and it decides nothing of its own:
    :func:`_resolve_assertions` is the seam every catalog reaches a reader
    through, so an arm scored here answered the rules an arm that ran the lanes
    would have answered. A graph carrying the review pass has its record parked
    already and this re-gates it; one carrying only an extraction pass resolves
    the proposal against the model the gate passed.
    """
    record = _resolve_assertions(
        keys.state(ctx), SystemModel.model_validate(valid_model)
    )
    return {
        "rows": len(record.catalog.entries),
        "subjects": len(record.catalog.subjects),
        "issues": len(record.issues),
    }


def _catalog_node_func(keys: GraphKeys) -> Callable[..., Any]:
    """The terminal catalog node, with this graph's key families bound to it."""

    def catalog(valid_model: dict, ctx) -> dict[str, Any]:
        return park_catalog(ctx, keys, valid_model)

    return catalog


def _assert_node(
    prompt_loader: MarkdownLoader,
    resolve_model: ModelResolver,
    resolve_sampling: SamplingResolver,
) -> LlmAgent:
    """The assertion node: the sources and the model in, one flat list out."""
    return _llm_node(
        name=ASSERT_NODE,
        tier_node="assert",
        instruction=compose_assert_prompt(prompt_loader),
        output_schema=CatalogProposal,
        output_key=STATE_ASSERTION_PROPOSAL,
        resolve_model=resolve_model,
        resolve_sampling=resolve_sampling,
    )


def _read_model_node_func(keys: GraphKeys) -> Callable[..., Any]:
    """Render the model for the assertion node, and nothing else.

    A rendered key is written by the node that derives it, so the assertion
    graph derives its own rather than taking bytes from its driver. It renders
    through :func:`render_model`, which ``prepare`` also calls, so the two
    graphs show one model one way. On a production graph that carries the
    pass this node sits between the validity gate and ``prepare``, and
    ``prepare`` renders the same value through the same function again, so
    the key holds one spelling whichever node wrote it last.
    """

    def read(valid_model: dict, ctx) -> dict[str, Any]:
        keys.state(ctx).prompt(STATE_SYSTEM_MODEL, render_model(valid_model))
        return {"elements": len(valid_model.get("data_flows", []))}

    return read


def _instruction(skills: str, prompt: str) -> str:
    """Skill text then prompt text: what to know, then what to do with it.

    **The prefix this shares is across jobs for one lane, and never across the
    lanes of one job.** The skill text is a lane's own, so the first bytes of
    two lanes' instructions already differ; what one lane repeats from case to
    case is its skill, the rubric and ``analyze.md`` down to the first
    job-varying placeholder.

    The lanes of one job read one **System Model** and one set of sources
    between them, and ``analyze.md`` lays those out after ``{scope}``, which
    :data:`LANE_ARTIFACTS` makes per lane. So the material every lane shares
    sits behind the first thing that separates them, and a provider that caches
    on a common prefix reaches none of it. Reordering the block is a prompt
    change and needs the measurement any prompt change needs.
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


def critic_instruction(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    package: FrameworkPackage,
    nodes: FrameworkNodes,
) -> str:
    """One framework's critic instruction, as the graph builds its critic node.

    Public so a replay of an archived critic call builds the same template the
    node was given, rather than a second copy of how it is put together.
    """
    return _review_instruction(
        package_loader, prompt_loader, package, nodes, compose_critic_prompt
    )


def _review_instruction(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    package: FrameworkPackage,
    nodes: FrameworkNodes,
    compose_prompt: Callable[[MarkdownLoader], str],
) -> str:
    """One framework's critic or re-ask instruction, with its keys resolved.

    Both share :func:`~analysis_service.skills.compose_critic_skills` byte for
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
    re-deriving a single node name. ``merge`` routes around the critic when
    code settled every draft (:data:`ROUTE_SETTLED`), straight to ``router``.
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
            (self.join, self.merge),
            (self.merge, {ROUTE_REVIEW: self.critic, ROUTE_SETTLED: self.router}),
            (self.critic, self.router),
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
) -> _FrameworkSubgraph:
    """Build one framework's lane agents, critic, re-ask and four function nodes."""
    package = nodes.package
    schemas = nodes.schemas
    reviewed_key = nodes.key("reviewed")

    def merge(valid_model: dict, ctx, source_texts: dict | None = None) -> Event:
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
            )
            for lane in nodes.lanes
        ),
        critic=_llm_node(
            name=nodes.node(CRITIC_ROLE),
            tier_node=tier_nodes[nodes.node(CRITIC_ROLE)],
            instruction=critic_instruction(
                package_loader, prompt_loader, package, nodes
            ),
            output_schema=schemas.rulings,
            output_key=reviewed_key,
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
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
        ),
        join=JoinNode(name=nodes.node(JOIN_ROLE)),
        merge=_node(merge, nodes.node(MERGE_ROLE)),
        router=_node(route, nodes.node(ROUTER_ROLE)),
        rereview=_node(route, nodes.node(REREVIEW_ROLE)),
        critic_failed=_node(failed, nodes.node(CRITIC_FAILED_ROLE)),
    )


def build_pipeline(
    *,
    prompt_loader: MarkdownLoader,
    domain_loader: MarkdownLoader,
    package_loaders: Mapping[FrameworkName, MarkdownLoader],
    binding: NodeBinding,
    frameworks: Sequence[FrameworkName],
    entry: Entry = ENTRY_EXTRACT,
    extraction_format: ExtractionFormat = FULL_FORMAT,
    extraction_strategy: ExtractionStrategy = GRAPH_FIRST,
    assertions: bool = False,
    source_review: bool = False,
    name: str = "analysis_pipeline",
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
    :class:`~analysis_service.sampling.SamplingConfig` and sourcing them from
    different ones would leave every node running on params the report does not
    attest to. See :class:`~analysis_service.binding.NodeBinding`.

    The loaders are the three roots this architecture has: ``prompts/`` for the
    shared bodies, ``domains/`` for the packs, and one per package rooted at
    ``frameworks/<name>/``. A deployment that redirects
    ``ANALYSIS_FRAMEWORKS_DIR`` redirects the third and neither of the others.

    ``entry`` selects where the graph starts. ``"extract"`` is production and
    the end-to-end eval mode. ``"prepare"`` is the **analysis** eval mode: a
    blessed System Model is seeded at :data:`STATE_VALID_MODEL` and the
    extraction half is left out entirely, so claim numbers are attributable to
    the lane agents and critics rather than to an element ``extract`` never
    produced. It is a parameter here, not a second topology in the eval tree,
    because two definitions of the same graph drift.

    ``assertions`` puts the ``assert`` node into a graph that prepares, between
    the validity gate and ``prepare``: the **Valid System Model** is rendered,
    the node proposes what the sources state about it, and ``prepare``
    resolves the proposal into the catalog every lane selects from. It is a
    property of the deployment (``ANALYSIS_ASSERTIONS``) for the reason the
    transport is, and it is refused on an entry that never prepares, because
    a pass nothing reads would spend a submitter's money.

    ``extraction_strategy`` selects which order the head of the graph reads in.
    ``graph-first`` is production and changes nothing. ``facts-first`` (#1003
    arm B) replaces ``extract`` with ``facts`` and ``resolve``: the node emits a
    **Source Fact Bundle** in local handles, and code turns it into the model
    the validity gate reads and the proposal ``prepare`` resolves. Everything
    from the gate onward is the same graph, which is what makes a comparison
    between the two a comparison of reading order.

    **A facts-first graph is refused an ``assert`` node.** Its bundle already
    carries what the sources state, so a second pass over the model it built
    would be a second extraction of one thing — the appended pass #1003 rules
    out, and two readings of one question besides.

    ``source_review`` puts the bounded repair pass (#1003 arms C and D) between
    the catalog and ``prepare``: ``reading`` renders the model and the rows,
    ``reread`` proposes typed operations over the sources, and ``apply`` applies
    the batch or discards it whole. **One pass for both arms**, on either
    strategy, so what a C-against-D comparison measures is the extraction order
    and never two applicators. It is refused on a graph that produces no catalog
    for it to read, and on one that never prepares.
    """
    if entry not in (
        ENTRY_EXTRACT,
        ENTRY_PREPARE,
        ENTRY_EXTRACT_ONLY,
        ENTRY_ASSERT_ONLY,
        ENTRY_HEAD_ONLY,
    ):
        raise ValueError(f"unknown graph entry point: {entry!r}")
    if extraction_format not in EXTRACTION_FORMATS:
        raise ValueError(f"unknown extraction format: {extraction_format!r}")
    if extraction_strategy not in EXTRACTION_STRATEGIES:
        raise ValueError(f"unknown extraction strategy: {extraction_strategy!r}")
    extracts = entry in EXTRACTING_ENTRIES
    split = extraction_strategy == FACTS_SPLIT
    # Whether this graph runs the lanes. The head-only entry stops at the
    # catalog, so it builds no ``prepare``, no fan-out and no framework
    # subgraph — and every node below that line is absent rather than unfired.
    analyses = entry != ENTRY_HEAD_ONLY
    # Both facts-first shapes: one call or two, the reading order is the same
    # and everything after ``resolve`` is identical. Derived rather than listed,
    # so a third facts-first spelling is covered by the strategy it names.
    facts_first = extraction_strategy in (FACTS_FIRST, FACTS_SPLIT)
    if facts_first and not extracts:
        raise ValueError(
            f"entry {entry!r} builds no extraction node, so it cannot be built"
            f" for the {extraction_strategy!r} strategy"
        )
    if facts_first and extraction_format != FULL_FORMAT:
        raise ValueError(
            f"the {extraction_strategy!r} strategy writes a bundle rather than a"
            f" model, so it has no {extraction_format!r} transport"
        )
    if facts_first and assertions:
        raise ValueError(
            f"the {extraction_strategy!r} strategy already reads what the"
            " sources state, so an assertion pass over the model it built"
            " would extract one thing twice"
        )
    # Every route that leaves a catalog behind, which is what the review reads.
    catalogs = assertions or facts_first
    if source_review and entry not in CATALOGUING_ENTRIES:
        raise ValueError(
            f"entry {entry!r} ends in no catalog, so nothing would read a source review"
        )
    if entry == ENTRY_HEAD_ONLY and not catalogs:
        raise ValueError(
            "the head-only entry is scored on the catalog its head produces,"
            " and this graph builds no node that produces one"
        )
    if source_review and not catalogs:
        raise ValueError(
            "a source review reads the catalog beside the model, and this graph"
            " builds no node that produces one"
        )
    if not extracts and extraction_format != FULL_FORMAT:
        raise ValueError(
            f"entry {entry!r} builds no extract node, so it cannot be built for"
            f" the {extraction_format!r} transport"
        )
    if assertions and entry not in CATALOGUING_ENTRIES:
        raise ValueError(
            f"entry {entry!r} ends in no catalog, so nothing would read"
            " an assertion pass"
        )
    if not frameworks:
        raise ValueError("a graph must be built for at least one framework")

    resolve_model = binding.resolve_model
    resolve_sampling = binding.resolve_sampling
    tier_nodes = tier_node_by_graph_node(frameworks)

    def pipeline(workflow: Workflow, llm_nodes: list[LlmAgent]) -> Pipeline:
        """The metadata every entry shape attests to, composed once."""
        return Pipeline(
            workflow=workflow,
            node_models={node.name: _model_name(node.model) for node in llm_nodes},
            tier_sampling=dict(binding.tier_sampling),
            node_sampling=_node_sampling(llm_nodes, resolve_sampling, tier_nodes),
            instruction_sha256=instruction_digest(llm_nodes),
            node_instructions=instruction_sizes(llm_nodes),
            frameworks=tuple(frameworks),
            tier_nodes=tier_nodes,
            review_independence=binding.review_independence,
            extraction_format=extraction_format if extracts else None,
            extraction_strategy=extraction_strategy if extracts else None,
        )

    if entry == ENTRY_EXTRACT_ONLY:
        if facts_first:
            facts = _facts_node(prompt_loader, resolve_model, resolve_sampling)
            return pipeline(Workflow(name=name, edges=[(START, facts)]), [facts])
        extract = _extract_node(
            prompt_loader, resolve_model, resolve_sampling, extraction_format
        )
        return pipeline(Workflow(name=name, edges=[(START, extract)]), [extract])

    if entry == ENTRY_ASSERT_ONLY:
        assert_keys = GraphKeys.of(frameworks)
        read = _node(_read_model_node_func(assert_keys), READ_MODEL_NODE)
        catalog = _assert_node(prompt_loader, resolve_model, resolve_sampling)
        return pipeline(Workflow(name=name, edges=[(START, read, catalog)]), [catalog])

    keys = GraphKeys.of(frameworks)
    # The tail: the analysing graph's ``prepare`` and its fan-out, or the one
    # terminal node that resolves the catalog and stops. Named once, so every
    # edge that carries a valid model is written once whichever graph this is.
    subgraphs: list[_FrameworkSubgraph] = []
    assemble = None
    if analyses:
        disclaimers = {
            framework: package_loaders[framework].load(DISCLAIMER_DOC).strip()
            for framework in frameworks
        }
        tail: Any = prepare_node(
            keys, frameworks, domain_loader, package_loaders, assertions
        )
        assemble = _node(
            _assemble_node_func(keys, frameworks, disclaimers), ASSEMBLE_NODE
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
            )
            for framework in frameworks
        ]
    else:
        tail = _node(_catalog_node_func(keys), CATALOG_NODE)
    # Where the valid model goes next: straight to ``prepare``, or through the
    # assertion pass first. One name for both, so the three edges that carry
    # a valid model are written once whichever graph this is.
    # What sits between the validity gate and ``prepare``, in the order a valid
    # model passes through it. Each pass names its own head and its own tail, so
    # a graph carrying both chains them and one carrying neither goes straight
    # to ``prepare``; the three edges that carry a valid model are written once
    # whichever graph this is.
    assertion_nodes: list[LlmAgent] = []
    passes: list[tuple[Any, list[Any]]] = []
    if assertions:
        read = _node(_read_model_node_func(keys), READ_MODEL_NODE)
        assert_node = _assert_node(prompt_loader, resolve_model, resolve_sampling)
        assertion_nodes = [assert_node]
        passes.append((read, [read, assert_node]))
    if source_review:
        reading = _node(_read_for_review_func(keys), READ_CATALOG_NODE)
        reread = _reread_node(prompt_loader, resolve_model, resolve_sampling)
        apply_node = _node(_apply_node_func(keys), APPLY_NODE)
        assertion_nodes = [*assertion_nodes, reread]
        passes.append((reading, [reading, reread, apply_node]))
    chain = [node for _, nodes in passes for node in nodes]
    pass_edges: list[tuple[Any, ...]] = [(*chain, tail)] if chain else []
    first = passes[0][0] if passes else tail

    extraction_nodes: list[LlmAgent] = []
    if extracts:
        # The head differs and nothing after it does. ``facts`` writes a bundle
        # and ``resolve`` writes the model at the key ``extract`` writes it at,
        # so one validity gate, one bounded repair and one rejection serve both
        # strategies — which is the property #1003 needs to compare them.
        # What sits between the reading node and ``resolve``. Empty on every
        # route but the split one, which renders its inventory and then states
        # facts about it.
        between: list[Any] = []
        second: LlmAgent | None = None
        if split:
            extract = _inventory_node(prompt_loader, resolve_model, resolve_sampling)
            second = _rows_node(prompt_loader, resolve_model, resolve_sampling)
            between = [
                _node(_read_inventory_func(keys), READ_INVENTORY_NODE),
                second,
            ]
            resolve = _node(_resolve_node_func(keys), RESOLVE_NODE)
        elif facts_first:
            extract = _facts_node(prompt_loader, resolve_model, resolve_sampling)
            resolve = _node(_resolve_node_func(keys), RESOLVE_NODE)
        else:
            extract = _extract_node(
                prompt_loader, resolve_model, resolve_sampling, extraction_format
            )
        repair = _llm_node(
            name=REPAIR_NODE,
            tier_node="repair",
            instruction=compose_repair_prompt(prompt_loader),
            output_schema=SystemModel,
            output_key=STATE_EXTRACTED_MODEL,
            resolve_model=resolve_model,
            resolve_sampling=resolve_sampling,
        )
        validate = _node(_validate_node_func(keys, extraction_format), VALIDATE_NODE)
        # ``repair`` emits a full System Model whichever transport ``extract``
        # used, so the second gate is always the full-model one.
        revalidate = _node(_validate_node_func(keys, FULL_FORMAT), REVALIDATE_NODE)
        reject = _node(_reject_node_func(keys), REJECT_NODE)
        extraction_nodes = [extract, repair]
        if second is not None:
            extraction_nodes.append(second)
        # ``list[tuple[Any, ...]]`` because ADK does not export the alias for
        # a chain element, and a routing-map literal only infers its declared
        # key type under an expected type -- which a bare local has none of.
        head_edges: list[tuple[Any, ...]] = [
            (START, extract, *between, resolve, validate)
            if facts_first
            else (START, extract, validate),
            (
                validate,
                {
                    ROUTE_VALID: first,
                    ROUTE_INVALID: repair,
                    ROUTE_UNCONVERTIBLE: reject,
                },
            ),
            (repair, revalidate),
            (revalidate, {ROUTE_VALID: first, ROUTE_INVALID: reject}),
        ]
    else:
        head_edges = [(START, first)]

    # The fan-out is routed rather than unconditional, which is the whole of the
    # run-time precondition gate's topology: ``prepare`` emits one route per
    # selected framework, and ADK fires every edge matching any of them. A
    # framework that satisfies its precondition takes ``run`` to its own lane
    # agents; one that does not takes ``skip`` to ``assemble``, so its block is
    # built and no lane of it ever runs.
    fan_out: dict[Any, Any] = {}
    for sub in subgraphs:
        fan_out[sub.nodes.run_route] = sub.agents
        fan_out[sub.nodes.skip_route] = assemble
    analysis_edges: list[tuple[Any, ...]] = []
    if assemble is not None:
        analysis_edges = [
            (tail, fan_out),
            *(edge for sub in subgraphs for edge in sub.edges(assemble)),
        ]
    workflow = Workflow(
        name=name,
        edges=[*head_edges, *pass_edges, *analysis_edges],
    )
    llm_nodes = [
        *extraction_nodes,
        *assertion_nodes,
        *(node for sub in subgraphs for node in sub.llm_nodes),
    ]
    return pipeline(workflow, llm_nodes)


def _validate_node_func(
    keys: GraphKeys, extraction_format: str = FULL_FORMAT
) -> Callable[..., Any]:
    """The validity gate, with this graph's key families bound to it.

    ADK binds a FunctionNode's parameters from session state, so ``keys`` — a
    fact about the built graph rather than about the job — is closed over rather
    than declared, and ``extraction_format`` rides with it for the same reason.
    Both validate nodes share this function; what differs is where their
    ``invalid`` edge points and which transport each was built for.
    """

    def validate(
        ctx, extracted_model: dict | None = None, source_texts: dict | None = None
    ) -> Event:
        return validate_extraction(
            ctx, keys, extracted_model, source_texts, extraction_format
        )

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


@dataclass(frozen=True)
class InstructionSize:
    """What one LLM node was told, as a size and its own digest.

    The pipeline-wide :func:`instruction_digest` answers *did anything change*.
    This answers *which node, and by how much* — the two questions a reader
    comparing two sweeps across a prompt edit actually has, and the second one
    no hash can answer.

    ``tokens`` uses the same coarse estimator the caps are written in
    (:func:`~analysis_service.markdown_loader.estimate_tokens`), so a number here
    and a number in ``TOKEN_CAPS`` are in one unit. It is the *composed* node
    instruction — skills then prompt, placeholders unexpanded — so it is larger
    than any one file's cap and is not compared against one.
    """

    tokens: int
    sha256: str


def instruction_sizes(llm_nodes: Sequence[LlmAgent]) -> dict[str, InstructionSize]:
    """Every LLM node's instruction, measured and digested, keyed by node name.

    Folded here, off the same ``llm_nodes`` list :func:`instruction_digest`
    hashes, so the per-node record and the pipeline-wide hash cannot describe
    different text. Build time, with the job-varying ``{placeholders}`` still
    unexpanded, so it carries no submitter bytes for the same reason the digest
    carries none.
    """
    sizes: dict[str, InstructionSize] = {}
    for node in llm_nodes:
        if not isinstance(node.instruction, str):
            # ADK also accepts a callable that composes the instruction per
            # request. Nothing here builds one, and measuring a node that did
            # would silently report the size of no text at all, so this refuses
            # rather than returns a number that means nothing.
            raise TypeError(f"node {node.name!r} carries a computed instruction")
        sizes[node.name] = InstructionSize(
            tokens=estimate_tokens(node.instruction),
            sha256=hashlib.sha256(node.instruction.encode("utf-8")).hexdigest(),
        )
    return sizes


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

    It is part of certification. The digest is one of the seven parts of the
    **Execution Identity**, so a prompt edit re-baselines every blessed
    fingerprint, and runs read as uncertified until a sanctioned sweep blesses
    the new ones. That cost was once the argument for recording the digest and
    not gating it. #504 made the opposite call: a run on edited prompts is not
    the run a deployment sanctioned, and reporting it as certified was the
    defect.
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


def revise_rounds(
    node_runs: Sequence[NodeRun], frameworks: Sequence[FrameworkName]
) -> int:
    """How many times a critic was asked a second time, across the whole job.

    The graph's ``revise`` edge: a critic whose rulings do not reconcile with
    the drafts is re-asked once, bounded, and the re-ask is its own node. So the
    count is how many of those nodes ran, and a job that reconciled first time
    reports zero.

    Worth recording because it is the cheapest signal that a critic is
    struggling with a package's question -- a rate that climbs after a prompt
    edit says the edit made the ruling harder to give, which no other number in
    the report says.

    Node names are derived per framework rather than matched by prefix, so a
    package registered tomorrow is counted by the same line.
    """
    re_asks = {FrameworkNodes(name).node(RECRITIC_ROLE) for name in frameworks}
    return sum(1 for run in node_runs if run.node in re_asks)


def rejection_issues(rendered: str) -> list[ValidationIssue]:
    """Parse the rejection the graph parked in state back into issues."""
    return [
        ValidationIssue.model_validate(issue) for issue in json.loads(unfence(rendered))
    ]


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
    corpus case — and neither reads the terminal keys itself. What differs
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
