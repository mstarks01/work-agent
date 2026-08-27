"""Every fact a lane agent may cite, enumerated from the validated model.

A finding's justification is a :class:`~stride_service.report.Ground`, and the
three non-quote branches of one are pure functions of the System Model: an
attribute the input never settled, a control attribute the input says is not
there, and a Data Flow whose endpoints sit in different trust zones. None
requires judgement to construct, and none is anything an agent knows that the
service does not. So the service constructs them, once, and hands the agent a
list of IDs.

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

IDs ARE STABLE AND MEAN SOMETHING. ``unknown:<element-id>:<attribute>``,
``absent:<element-id>:<attribute>`` and ``crossing:<flow-id>``, all built from
IDs the model already carries, so the same System Model yields the same catalog
on every run and a ref in a log or a diff is readable without a lookup. Opaque
IDs would cost that for nothing: there is no secret here, only facts the agent
is being shown anyway.

Model output is untrusted input (OWASP LLM05). A ref is used as a dictionary
key and never parsed, interpolated, or matched by a pattern compiled from it,
so the only thing an agent can do with the field is name an entry or fail to.
There is no fuzzy match and no repair: a ref that is not in the catalog is
reported as itself, because the alternative — inferring which fact an agent
*meant* — is the class of guess this module exists to remove.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from stride_service.analysis import CONTROL_ATTRIBUTES, control_state
from stride_service.frameworks import FrameworkPackage, schemas_for
from stride_service.report import (
    DROPPED_REASON_MAX_CHARS,
    REFERENCE_MAX_CHARS,
    AnalysisMarks,
    Claim,
    DroppedClaim,
    Ground,
    InvalidProposal,
    Proposal,
    UnknownClaimIdentity,
    UnresolvedEvidence,
)
from stride_service.system_model import (
    DataFlow,
    Element,
    SystemModel,
    TrustBoundary,
    attribute_names,
)

#: An evidence catalog: each reference, in a stable order, against the ground
#: it resolves to. A plain mapping on purpose — ``list(catalog)`` is what the
#: agent is shown, ``ref in catalog`` is the whole of the validity question,
#: and ``catalog[ref]`` is the whole of resolution, so a wrapper type would add
#: a vocabulary without adding an operation.
EvidenceCatalog = dict[str, Ground]

UNKNOWN_PREFIX = "unknown"
ABSENT_PREFIX = "absent"
CROSSING_PREFIX = "crossing"


def unknown_evidence_ref(element_id: str, attribute: str) -> str:
    """The catalog ID for one element's unstated attribute."""
    return f"{UNKNOWN_PREFIX}:{element_id}:{attribute}"


def absent_evidence_ref(element_id: str, attribute: str) -> str:
    """The catalog ID for one control the input states is not there."""
    return f"{ABSENT_PREFIX}:{element_id}:{attribute}"


def crossing_evidence_ref(flow_id: str) -> str:
    """The catalog ID for one flow's derived boundary crossing."""
    return f"{CROSSING_PREFIX}:{flow_id}"


def evidence_catalog(model: SystemModel) -> EvidenceCatalog:
    """Every mechanically derivable fact in one validated System Model.

    Two enumerations, in this order: each element's attributes the input left
    unsettled or stated absent, walked in the model's own element order and each
    element's own field-declaration order; then the derived boundary crossings,
    in the order :meth:`~stride_service.system_model.SystemModel.boundary_crossings`
    yields them. Both orders are properties of the model rather than of this
    call, so an identical model produces an identical catalog — which is what
    lets a ref be compared across runs, samples and reports.

    Only the *type-specific* attributes are eligible
    (:func:`~stride_service.system_model.attribute_names`). ``notes`` holding
    the word "unknown" is a sentence, not an unstated control, and a catalog
    that could not tell those apart would invite a finding grounded on prose.

    Refs cannot collide. An element ID is unique across a validated model and an
    attribute appears once on an element, so each attribute half is injective
    and the two cannot overlap because one attribute has one state; a flow ID is
    an element ID, so the ``crossing`` half is injective; and the three halves
    are separated by their prefixes.

    Requires a valid model, and fails closed on an invalid one exactly as
    ``boundary_crossings`` does — a catalog derived from a model with a
    dangling flow endpoint would offer agents evidence about a system nobody
    described.
    """
    catalog: EvidenceCatalog = dict(
        entry
        for element in model.elements()
        for attribute in attribute_names(element)
        if (entry := _attribute_entry(element, attribute)) is not None
    )
    catalog.update(
        {
            crossing_evidence_ref(crossing.flow_id): Ground(
                kind="derived-fact", flow_id=crossing.flow_id
            )
            for crossing in model.boundary_crossings()
        }
    )
    return catalog


def _attribute_entry(element: Element, attribute: str) -> tuple[str, Ground] | None:
    """One attribute's catalog entry, or ``None`` where it states a control.

    The classifier is :func:`~stride_service.analysis.control_state`, the same
    one the candidate rules read, which is the whole of this function's point:
    the catalog used to test exact equality with the ``unknown`` sentinel while
    the analysis layer read the leading token, so a control the input *hedged*
    (``"unknown; possibly a shared group account"``) and a control the input
    said is *not there* (``"none; accepted by network position"``) were both
    facts a rule could fire on and no agent could cite (#171).

    **The two halves cover different attribute sets, and the asymmetry is the
    honest reading rather than an oversight.** ``unknown`` is the extraction
    sentinel: it means "the input never settled this" on every type-specific
    field, decorated with a hedge or not, so the unverified half runs over all
    of them. ``none`` is not a sentinel — it carries a determinate meaning only
    where the attribute names a control, and "the submitter said this is not
    there" is not a fact about a ``protocol`` or a ``data_description``. So the
    absent half is confined to :data:`~stride_service.analysis.CONTROL_ATTRIBUTES`,
    and a ``technology`` reading ``none`` stays what the input wrote rather than
    becoming a fact an agent may rest a finding on.

    Both entries carry the element and the attribute and nothing else. Which
    *state* the attribute is in is the branch, never a field — a ground whose
    kind and payload could disagree is the shape this whole module exists to
    make unreachable.
    """
    state = control_state(getattr(element, attribute))
    if state == "unverified":
        return unknown_evidence_ref(element.id, attribute), Ground(
            kind="unknown-attribute", element_id=element.id, attribute=attribute
        )
    if state == "absent" and attribute in CONTROL_ATTRIBUTES:
        return absent_evidence_ref(element.id, attribute), Ground(
            kind="absent-attribute", element_id=element.id, attribute=attribute
        )
    return None


def render_catalog(catalog: Mapping[str, Ground]) -> str:
    """The catalog as a table an agent selects from rather than a list it reads.

    **Shape is the point, and it is a fix rather than a decoration.** Rendered
    as a JSON array of ID strings, the catalog reads as a *specimen of the
    format* as much as a closed list, and agents composed well-formed
    references to facts it did not contain — correct grammar, plausible element
    IDs, real attribute names, absent from the set (#138). Every such reference
    fails its job, so this is not a cosmetic concern.

    A table resists that in a way a list cannot. Each row carries prose derived
    from *this* model, so there is no pattern to complete: an agent that wants
    to cite a flow's authentication either finds the row or finds that the row
    is not there. The gloss is what makes the second case legible — the reason
    an attribute is absent is that the input *stated* it, which the ID alone
    never says.

    Order is the catalog's own, which is the model's, so the same System Model
    renders the same table on every run.
    """
    rows = "\n".join(
        f"| `{ref}` | {_gloss(ground)} |" for ref, ground in catalog.items()
    )
    return (
        f"{len(catalog)} facts, and this table is all of them.\n\n"
        "| cite this exactly | what it says |\n| --- | --- |\n"
        f"{rows}\n"
    )


def render_element_roster(model: SystemModel) -> str:
    """Every element ID a claim may name, as a table to select from.

    **The same fix as :func:`render_catalog`, at the seam it was never applied
    to.** That docstring records why: rendered as a specimen of the format, a
    reference set invites an agent to *compose* a well-formed member instead of
    copying one, and a composed reference that resolves to nothing fails its
    whole job (#138, ADR 0012).

    ``affected_element_ids`` had only the constraint — "every one of them
    present in the System Model" — and the model as fenced JSON to read it out
    of. On a live end-to-end sweep a lane agent produced
    ``flow:a-to-b:label:label``, its own label concatenated twice: well-formed,
    plausible, absent from the set. It never appears in ``analysis`` mode, whose
    seeded blessed model has clean IDs to copy (#306).

    **The gloss is type and place, never the attributes.** Those are in the
    System Model already and this table is paid for on every lane agent of every
    framework; repeating them would buy nothing and cost the most expensive
    block in the job-varying half. What a row has to carry is enough for an
    agent to recognise the element it means without reconstructing the ID —
    which is what the left column is for.

    Order is the model's own, so one System Model renders one table every run.
    """
    elements = list(model.elements())
    rows = "\n".join(
        f"| `{element.id}` | {_element_gloss(element)} |" for element in elements
    )
    return (
        f"{len(elements)} elements, and this table is all of them."
        " Name one exactly as it appears here.\n\n"
        "| cite this exactly | what it is |\n| --- | --- |\n"
        f"{rows}\n"
    )


def _element_gloss(element: Element) -> str:
    """One element's type and where it sits, in the fewest words that identify it.

    A flow reads as its endpoints rather than its zone: a flow has no
    ``trust_zone`` of its own, and its endpoints are the thing an agent is
    choosing between when two flows share a source.
    """
    if isinstance(element, DataFlow):
        return f"flow, `{element.source}` to `{element.destination}`"
    if isinstance(element, TrustBoundary):
        return "trust boundary"
    kind = element.id.partition(":")[0]
    return f"{kind} in `{element.trust_zone}`"


def _gloss(ground: Ground) -> str:
    """What one catalogued fact asserts.

    Deliberately short, and it does not repeat the element ID: that is the left
    column already, and this text is paid for on every lane agent's instruction
    through the two exemplar catalogs. What it has to carry is the *kind* of
    fact — an unstated attribute reads very differently from a derived crossing,
    and an agent that conflates them cites the wrong one. *Never stated* against
    *stated absent* is the sharpest of those distinctions and the cheapest to
    render: it is the difference between a question and an answer, and the
    prompt spends a whole procedure step on it.
    """
    if ground.kind == "derived-fact":
        return "crosses a trust boundary"
    if ground.kind == "absent-attribute":
        return f"`{ground.attribute}` stated absent"
    return f"`{ground.attribute}` never stated"


class Resolution(NamedTuple):
    """One lane's drafts, and every reference of theirs that named nothing.

    Two values because the second is no longer fatal on its own: a dropped
    reference is recorded and the analysis continues, so the caller needs both
    halves. Shaped like :class:`~stride_service.critic.JoinedDrafts` — marks
    beside drafts — and carrying the same
    :class:`~stride_service.report.AnalysisMarks`, so the fan-in merges what
    every lane and the join produced without knowing which mark came from where.
    Only ``unresolved_evidence`` is ever populated here; the other four lists
    have no producer this early.

    The drafts are the package's own record type; they are typed as the neutral
    :class:`~stride_service.report.Claim` here because this module builds them
    from a contract rather than from a framework it knows.
    """

    drafts: list[Claim]
    marks: AnalysisMarks


def _grounds_of(
    proposal: Proposal, catalog: Mapping[str, Ground]
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

    A reference that resolves to nothing is **dropped rather than raised on**.
    Nothing can be built from it — the catalog is the only source of a ground's
    branch and fields — so it leaves as a mark the caller records, and whether
    the threat survives is decided by what is left, not by this function.
    """
    grounds = [
        Ground(kind="quote", text=quote.text, source_label=quote.source_label)
        for quote in proposal.quotes
    ]
    unresolved = []
    for ref in proposal.evidence_refs:
        ground = catalog.get(ref.strip())
        if ground is None:
            unresolved.append(ref)
        else:
            grounds.append(ground)
    return grounds, unresolved


#: What every proposal carries for this module rather than for the claim: the
#: two evidence lists, which resolve into ``grounds`` and do not survive as
#: fields. Everything else an agent wrote is carried across untouched.
_RESOLVED_AWAY = frozenset({"evidence_refs", "quotes"})


def resolve_proposals(
    proposals: Iterable[Proposal],
    catalog: Mapping[str, Ground],
    package: FrameworkPackage,
    lane: str,
) -> Resolution:
    """Turn one lane's proposals into drafts: resolve the evidence, stamp the lane.

    **One resolver for every framework**, which is what keeps *the agent selects
    and the service constructs* a construction rather than a convention each
    package could break. Three things are the service's here and none of them is
    a judgement: the grounds, the claim ID, and the lane.

    The catalog is the sole source of truth for evidence: a resolved ground is
    the entry itself, so it carries the branch and the fields that branch
    requires, and the claim this returns cannot be mis-shaped whatever an agent
    emitted.

    ``lane`` is the lane being resolved, and it arrives as an argument because
    that is where the fact lives — the graph builds one node per
    ``(framework, lane)`` and knows which is which before any model runs. The ID
    is composed by the package's own :class:`~stride_service.frameworks.IdRule`
    from that lane and the agent's key, and the lane is stamped into whatever
    field that rule names, so a draft's lane, its ID's prefix and the node that
    produced it agree by construction rather than by an agent keeping three
    spellings in line.

    Everything else the agent wrote passes through untouched — the title, the
    description, the element refs, and whatever this framework judges. The key
    itself does not: it is what the ID was composed *from*, and the gate has
    already refused a record that declares it.

    **A reference that names nothing costs its entry, not the job.** Agents
    compose well-formed references to facts the catalog does not hold — correct
    grammar, plausible element IDs — and failing the whole analysis over one
    discards every lane's work to punish a citation error (#138). So an
    unresolvable reference is dropped and marked, and the claim stands on
    whatever else it cited.

    A claim whose every ground evaporates has nothing supporting it, and a
    finding with empty ``grounds`` is the one thing this schema refuses to
    represent — so the claim is dropped and recorded as a
    :class:`~stride_service.report.DroppedClaim`, with the references it
    cited in the reason. It is the same rule
    :func:`~stride_service.critic.join_drafts` applies to unverified quotes:
    marked per entry, dropped per claim. Nothing here raises on what an agent
    cited.
    """
    key_field = schemas_for(package.name).key_field
    carried = _RESOLVED_AWAY | {key_field}
    drafts: list[Claim] = []
    unresolved_evidence: list[UnresolvedEvidence] = []
    unknown_identities: list[UnknownClaimIdentity] = []
    groundless: list[DroppedClaim] = []
    for proposal in proposals:
        key = getattr(proposal, key_field)
        claim_id = package.compose_id(lane, key)
        # Before the evidence, because a claim naming a requirement its own
        # framework does not have is not a claim whose grounds are worth
        # resolving. Dropped and marked on the #138 rule: an agent composing a
        # well-formed reference to something absent costs its entry, never the
        # run. A package that mints its own IDs answers ``True`` here always.
        if not package.id_rule.knows(lane, key):
            unknown_identities.append(
                UnknownClaimIdentity(claim_id=claim_id, title=proposal.title)
            )
            continue
        grounds, unresolved = _grounds_of(proposal, catalog)
        # A per-reference mark names a claim the block carries, so a claim that
        # is dropped gets none: its groundless mark names the references instead.
        if not grounds:
            cited = ", ".join(repr(ref) for ref in unresolved)
            groundless.append(
                DroppedClaim(
                    claim_id=claim_id,
                    title=proposal.title,
                    reason=(
                        "cites only evidence this job's catalog does not"
                        f" contain ({cited})"
                    )[:DROPPED_REASON_MAX_CHARS],
                )
            )
            continue
        unresolved_evidence += [
            UnresolvedEvidence(claim_id=claim_id, reference=ref[:REFERENCE_MAX_CHARS])
            for ref in unresolved
        ]
        # The agent's own fields plus the lane the graph stamped, as one mapping:
        # what a package's record declares beyond :class:`Claim` is the package's
        # business, so this is deliberately untyped here and validated by the
        # record's own model.
        agent_fields: dict[str, Any] = {
            **package.lane_fields(lane),
            **proposal.model_dump(exclude=set(carried)),
        }
        drafts.append(
            package.record(
                id=claim_id,
                framework=package.name,
                framework_version=package.version,
                grounds=grounds,
                **agent_fields,
            )
        )
    return Resolution(
        drafts,
        AnalysisMarks(
            unresolved_evidence=unresolved_evidence,
            unknown_claim_identities=unknown_identities,
            dropped_claims=groundless,
        ),
    )


def invalid_proposal_marks(
    invalid: Sequence[InvalidProposal], package: FrameworkPackage, lane: str
) -> AnalysisMarks:
    """The marks for the proposals a lane's batch could not validate.

    A schema fault is a fault in one entry, so it costs that entry: the batch
    already dropped it (:class:`~stride_service.report.ProposalBatch`), and
    this records it as a :class:`~stride_service.report.DroppedClaim` with
    the first error pydantic reported. The ID is composed from the package's
    rule where the key is readable, so the mark names the claim the agent
    meant; where it is not, the mark is keyed by the lane and the position,
    which is the only identity left.
    """
    key_field = schemas_for(package.name).key_field
    dropped: list[DroppedClaim] = []
    for entry in invalid:
        key = entry.scalars.get(key_field)
        try:
            claim_id = package.compose_id(lane, key) if key is not None else ""
        except (TypeError, ValueError, KeyError):
            claim_id = ""
        dropped.append(
            DroppedClaim(
                claim_id=claim_id or f"{package.name}:{lane}:proposal-{entry.index}",
                title=str(entry.scalars.get("title") or "(untitled)"),
                reason=f"fails the proposal schema at {entry.error}"[
                    :DROPPED_REASON_MAX_CHARS
                ],
            )
        )
    return AnalysisMarks(dropped_claims=dropped)
