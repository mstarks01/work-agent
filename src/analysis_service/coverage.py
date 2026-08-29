"""Coverage accounting: what each lane was offered, and what its drafts cite.

A claim count says how much an agent found. It cannot say whether a lane that
found nothing had examined the system and cleared it, or had never looked at
half of it — and those two are the difference between a report and a
misleading one. This module computes the second number.

Everything here is derived in code from three artifacts that already exist: the
validated System Model, the deterministic candidates one package's rules raised
(:mod:`analysis_service.candidates`), and the drafts that package's lane agents
actually filed. Nothing is asserted by a model, and nothing here can change a
finding — coverage is recorded beside the analysis, never fed back into it.

**Per framework, and it sits inside that framework's block for the same
reason.** A lane belongs to the package that declared it, the denominators are
counted from that package's own rules, and a coverage table pooled across
frameworks would divide one framework's citations by another's leads.

**The honest limit, stated once here and again on
:class:`~analysis_service.report.LaneCoverage`.** What is measured is
*citation*, not attention: an agent that read a flow and rightly concluded it
was harmless cites nothing, and looks from here exactly like one that skipped
it. There is no observable that separates them — a model's own claim to have
examined something is precisely the assertion this design refuses to trust. So
the fields are named for citation, and the number that means something is the
aggregate across a corpus rather than any one lane on any one case.

Computed at the fan-in, over the drafts rather than the ruled claims: coverage
is a fact about what the agents did with the system, and a draft the critic
later rejects was still a part of the system being examined.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from types import MappingProxyType

from analysis_service.analysis import unknown_controls
from analysis_service.candidates import CandidateSet
from analysis_service.critic import mentioned_ids
from analysis_service.frameworks import FrameworkPackage
from analysis_service.references import canonical
from analysis_service.report import Claim, LaneCoverage
from analysis_service.system_model import SystemModel

__all__ = ["build_coverage", "cited_element_ids", "lane_scope"]


def lane_scope(
    lane: str,
    package: FrameworkPackage,
    model: SystemModel,
    candidate_set: CandidateSet | None,
    options: Mapping[str, object] = MappingProxyType({}),
) -> str:
    """One lane's denominators and its job's options, as a line the agent reads.

    The same numbers :func:`build_coverage` records afterwards, and derived
    from the same three calls — which is the whole reason this lives here
    rather than in the graph. A second derivation could disagree with the one
    the report publishes, and then "17 elements" in the instruction and "17
    elements" in the coverage row would be two claims rather than one fact.

    Only the denominators. The ``*_cited`` halves need drafts that do not exist
    yet, and an agent cannot be told what it is about to cite.

    **What this is for**, and it is not a quota: a lane that files nothing
    should be able to mean *examined and cleared* rather than *never looked*,
    and an agent cannot say that about a system whose size it was never told.
    An agent that files a claim per element to make a number go up has
    misread it, which is why the prompt spends a sentence saying so.

    **The job's options ride here** because this is the one per-lane block the
    graph writes per job. A framework whose options select which requirements
    apply produces a different answer under different ones, so its lane agents
    have to be told what was asked for. They are rendered neutrally, as the
    package's own field names against the values the input ladder validated:
    this module knows no package, and a framework with no options renders
    nothing extra.
    """
    offered = candidate_set.candidates if candidate_set else ()
    fired = len({candidate.rule_id for candidate in offered})
    selected = "".join(
        f" This job asked for {package.name} with {name} {value}."
        for name, value in sorted(options.items())
    )
    return (
        f"Scope for your lane: {len(model.elements())} elements, "
        f"{len(model.boundary_crossings())} boundary crossings, "
        f"{len(unknown_controls(model))} unstated controls. "
        f"{len(package.rules_for(lane))} {lane} rules ran; {fired} fired, "
        f"raising {len(offered)} candidates.{selected}\n"
    )


def cited_element_ids(
    drafts: Iterable[Claim], element_ids: Collection[str]
) -> frozenset[str]:
    """Every element ID these drafts point at, from both places one appears.

    ``affected_element_ids`` is the structured claim and prose citations are
    the argument; a flow named only in the description was still examined, so
    counting only the structured field would undercount coverage against the
    prompt's own instruction to cite IDs inline.

    **Both halves are resolved against the model, and the prose half is why.**
    A structural reference already resolves — :func:`~analysis_service.critic.
    join_drafts` fails the job on one that does not — but a prose mention is
    marked rather than failed on, so a description naming ``process:web-api``
    in a job with no such element reaches here. Counted raw it would make
    ``elements_cited`` exceed ``elements``, and it would do so worst on exactly
    the runs :class:`~analysis_service.report.UnresolvedMention` exists to catch:
    a lane contaminated by the exemplar system inflates the number that is
    supposed to say how much of *this* system it looked at.

    Resolution is :func:`~analysis_service.references.canonical`, the same fold
    the mark path applies, so one spelling of one element counts once. Reading
    prose mentions raw would count ``Process:Auth-Service`` as an element of its
    own here while the exact-membership tests below missed it — one mention
    inflating one field and suppressing three.
    """
    return frozenset(
        resolved
        for draft in drafts
        for reference in (
            *draft.affected_element_ids,
            *mentioned_ids(draft.description),
        )
        if (resolved := canonical(reference, element_ids))
    )


def build_coverage(
    drafts_by_lane: Mapping[str, list[Claim]],
    candidates: Mapping[str, CandidateSet],
    model: SystemModel,
    package: FrameworkPackage,
) -> list[LaneCoverage]:
    """One :class:`LaneCoverage` per lane, in the package's declared order.

    Every lane gets a row, including one that filed nothing — a missing row
    would be the exact ambiguity this accounting exists to remove. The lane set
    is the package's own, so a framework's block accounts for its own lanes and
    for nothing else.
    """
    elements = [element.id for element in model.elements()]
    crossings = model.boundary_crossings()
    controls = unknown_controls(model)
    return [
        _row(
            lane,
            package,
            drafts_by_lane.get(lane, []),
            candidates.get(lane),
            element_ids=elements,
            crossing_flow_ids=[crossing.flow_id for crossing in crossings],
            control_element_ids=[control.element_id for control in controls],
        )
        for lane in package.lanes
    ]


def _row(
    lane: str,
    package: FrameworkPackage,
    drafts: list[Claim],
    candidate_set: CandidateSet | None,
    *,
    element_ids: Collection[str],
    crossing_flow_ids: list[str],
    control_element_ids: list[str],
) -> LaneCoverage:
    cited = cited_element_ids(drafts, element_ids)
    offered = candidate_set.candidates if candidate_set else ()
    return LaneCoverage(
        lane=lane,
        drafts=len(drafts),
        rules=len(package.rules_for(lane)),
        rules_fired=len({candidate.rule_id for candidate in offered}),
        candidates=len(offered),
        # A candidate counts as cited when *every* element it names is cited:
        # a rule fires on a flow together with the endpoints it is about, and
        # a draft that picked up one endpoint for unrelated reasons has not
        # taken up the lead.
        candidates_cited=sum(
            1 for candidate in offered if set(candidate.element_ids) <= cited
        ),
        elements=len(element_ids),
        elements_cited=len(cited),
        boundary_crossings=len(crossing_flow_ids),
        boundary_crossings_cited=sum(
            1 for flow_id in crossing_flow_ids if flow_id in cited
        ),
        unknown_controls=len(control_element_ids),
        unknown_controls_cited=sum(
            1 for element_id in control_element_ids if element_id in cited
        ),
    )
