"""Every fact a category agent may cite, enumerated from the validated model.

A finding's justification is a :class:`~stride_service.report.Ground`, and the
two non-quote branches of one are pure functions of the System Model: an
attribute whose value is the ``unknown`` sentinel, and a Data Flow whose
endpoints sit in different trust zones. Neither requires judgement to
construct, and neither is anything an agent knows that the service does not.
So the service constructs them, once, and hands the agent a list of IDs.

**The LLM decides which evidence supports a finding; this module decides how
that evidence is represented.** An agent answers with
``evidence_refs`` — IDs copied out of the catalog — and
:func:`resolve_proposals` turns each back into the ground it came from. An
agent that picks the right fact can no longer file it under the wrong branch,
omit the field that branch requires, or invent an element ID, because it
supplies none of those things: it supplies a choice from a closed set.

WHAT THIS IS NOT. A catalog entry says *this fact is in the validated system
representation* and nothing more. ``authentication`` being unknown on a flow is
not a spoofing threat, and a crossing is not a vulnerability; whether either
participates in a credible attack stays the agent's judgement and the critic's
to rule on. The catalog is deliberately incapable of expressing a conclusion —
every entry is derived by the two rules above, so there is no seam through
which "the authentication is weak" could enter it.

Quotes are not catalogued, and could not be: a quote is a span of the
submitter's own words chosen for what it states, which is exactly the
judgement no enumeration can make. An agent proposes one as a
:class:`~stride_service.report.QuoteCandidate` — the span and the source it
came from — and :func:`resolve_proposals` assembles the ground. What it does
not do is *check* it; presence in the named source stays
:func:`~stride_service.critic.join_drafts`'s question, answered by the pinned
ladder in :mod:`stride_service.grounding` against the job's actual bytes, which
this module does not hold.

IDs ARE STABLE AND MEAN SOMETHING. ``unknown:<element-id>:<attribute>`` and
``crossing:<flow-id>``, both built from IDs the model already carries, so the
same System Model yields the same catalog on every run and a ref in a log or a
diff is readable without a lookup. Opaque IDs would cost that for nothing:
there is no secret here, only facts the agent is being shown anyway.

Model output is untrusted input (OWASP LLM05). A ref is used as a dictionary
key and never parsed, interpolated, or matched by a pattern compiled from it,
so the only thing an agent can do with the field is name an entry or fail to.
There is no fuzzy match and no repair: a ref that is not in the catalog is
reported as itself, because the alternative — inferring which fact an agent
*meant* — is the class of guess this module exists to remove.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from stride_service.critic import DraftJoinError
from stride_service.report import DraftThreat, Ground, ThreatProposal
from stride_service.system_model import UNKNOWN, SystemModel, attribute_names

#: An evidence catalog: each reference, in a stable order, against the ground
#: it resolves to. A plain mapping on purpose — ``list(catalog)`` is what the
#: agent is shown, ``ref in catalog`` is the whole of the validity question,
#: and ``catalog[ref]`` is the whole of resolution, so a wrapper type would add
#: a vocabulary without adding an operation.
EvidenceCatalog = dict[str, Ground]

UNKNOWN_PREFIX = "unknown"
CROSSING_PREFIX = "crossing"


class EvidenceResolutionError(DraftJoinError):
    """A proposal cites evidence this job's catalog does not contain.

    A :class:`~stride_service.critic.DraftJoinError` because it is one: a draft
    naming a fact that does not exist is the same fault as a draft naming an
    element that does not exist, discovered at the same seam, with the same
    consequence. Callers that already handle the fan-in refusing a set of
    drafts handle this without knowing it exists.
    """


def unknown_evidence_ref(element_id: str, attribute: str) -> str:
    """The catalog ID for one element's unstated attribute."""
    return f"{UNKNOWN_PREFIX}:{element_id}:{attribute}"


def crossing_evidence_ref(flow_id: str) -> str:
    """The catalog ID for one flow's derived boundary crossing."""
    return f"{CROSSING_PREFIX}:{flow_id}"


def evidence_catalog(model: SystemModel) -> EvidenceCatalog:
    """Every mechanically derivable fact in one validated System Model.

    Two enumerations, in this order: each element's attributes whose value is
    the ``unknown`` sentinel, walked in the model's own element order and each
    element's own field-declaration order; then the derived boundary crossings,
    in the order :meth:`~stride_service.system_model.SystemModel.boundary_crossings`
    yields them. Both orders are properties of the model rather than of this
    call, so an identical model produces an identical catalog — which is what
    lets a ref be compared across runs, samples and reports.

    Only the *type-specific* attributes are eligible
    (:func:`~stride_service.system_model.attribute_names`). ``notes`` holding
    the word "unknown" is a sentence, not an unstated control, and a catalog
    that could not tell those apart would invite a finding grounded on prose.

    Two refs cannot collide. An element ID is unique across a validated model
    and an attribute appears once on an element, so the ``unknown`` half is
    injective; a flow ID is an element ID, so the ``crossing`` half is; and the
    two halves are separated by their prefixes.

    Requires a valid model, and fails closed on an invalid one exactly as
    ``boundary_crossings`` does — a catalog derived from a model with a
    dangling flow endpoint would offer agents evidence about a system nobody
    described.
    """
    catalog: EvidenceCatalog = {
        unknown_evidence_ref(element.id, attribute): Ground(
            kind="unknown-attribute", element_id=element.id, attribute=attribute
        )
        for element in model.elements()
        for attribute in attribute_names(element)
        if getattr(element, attribute) == UNKNOWN
    }
    catalog.update(
        {
            crossing_evidence_ref(crossing.flow_id): Ground(
                kind="derived-fact", flow_id=crossing.flow_id
            )
            for crossing in model.boundary_crossings()
        }
    )
    return catalog


def _grounds_of(
    proposal: ThreatProposal, catalog: Mapping[str, Ground]
) -> tuple[list[Ground], list[str]]:
    """One proposal's grounds, and every reference of its that named nothing.

    Quotes first, then evidence, and the order is fixed rather than incidental:
    :class:`~stride_service.report.UnverifiedGround` marks a quote by its
    *index* into the finished list, so a reader following a mark back to the
    quote it is about depends on this being the one place the list is built.
    Quotes lead because they are the submitter's own words, which is what a
    reader looks for first.

    A ref is stripped of surrounding whitespace before lookup — which spelling
    of a name arrived is mechanical — and matched exactly thereafter.
    """
    grounds = [
        Ground(kind="quote", text=quote.text, source_label=quote.source_label)
        for quote in proposal.quotes
    ]
    unresolved = []
    for ref in proposal.evidence_refs:
        ground = catalog.get(ref.strip())
        if ground is None:
            unresolved.append(
                f"threat {proposal.id!r} cites evidence {ref!r}, which is not"
                " in this job's evidence catalog"
            )
        else:
            grounds.append(ground)
    return grounds, unresolved


def resolve_proposals(
    proposals: Iterable[ThreatProposal], catalog: Mapping[str, Ground]
) -> list[DraftThreat]:
    """Turn one lane's proposals into drafts, resolving every reference.

    The catalog is the sole source of truth: a resolved ground is the entry
    itself, so it carries the branch and the fields that branch requires, and
    the :class:`~stride_service.report.DraftThreat` this returns cannot be
    mis-shaped whatever an agent emitted. The agent's other seven fields pass
    through untouched — this resolves evidence and nothing else.

    Every unresolvable reference across the whole batch is reported together,
    rather than the first one aborting the pass. An agent that misread the
    catalog usually misread it more than once, and a fan-in with no re-ask path
    gets one chance to say what was wrong.
    """
    drafts = []
    issues: list[str] = []
    for proposal in proposals:
        grounds, unresolved = _grounds_of(proposal, catalog)
        issues += unresolved
        if unresolved:
            continue
        drafts.append(
            DraftThreat(
                id=proposal.id,
                category=proposal.category,
                title=proposal.title,
                description=proposal.description,
                affected_element_ids=proposal.affected_element_ids,
                grounds=grounds,
                severity=proposal.severity,
                mitigations=proposal.mitigations,
            )
        )
    if issues:
        raise EvidenceResolutionError("; ".join(issues))
    return drafts
