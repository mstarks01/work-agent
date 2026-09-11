"""The report: the structured JSON payload the front-end retrieves for a job.

There is one envelope and many frameworks. A :class:`Report` carries the facts
about one job — its identity, its inputs, the nodes that ran, and the single
**Valid System Model** every framework analysed — plus one
:class:`FrameworkAnalysis` block per framework the job selected. A field sits
where the thing it describes sits. Nine fields describe the job or the shared
model and stay on the envelope, and everything one framework produced rides in
that framework's own block.

The shapes inside a block — the neutral :class:`~analysis_service.claims.Claim`,
its grounds, the proposal and ruling wrappers, the marks and the block itself —
live in :mod:`analysis_service.claims`. This module holds the envelope and what
only the envelope describes: the job, the input, the node runs, the execution
envelope and the sampling record. It imports the framework registry and the
evidence resolver at the top, because nothing below them imports it back.

The report embeds the full validated System Model plus derived boundary
crossings once, so it is self-contained: every element reference in every block
resolves inside one payload.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    computed_field,
    field_validator,
    model_validator,
)

from analysis_service.claims import FrameworkAnalysis, FrameworkName, SharedElementName
from analysis_service.evidence import ground_issues
from analysis_service.frameworks import block_type_for
from analysis_service.sources import Source, clean_system_name
from analysis_service.system_model import (
    BoundaryCrossing,
    SystemModel,
)
from analysis_service.vendors import ServedTrust, vendor_for_route

# The payload schema readers key on. Consumers that ignore unknown fields
# tolerate a minor bump; a major bump is a breaking change to a field's
# meaning. The policy, stated once here and in docs/Report-Schema.md:
# **additive fields are a minor bump; changing the meaning or the spelling of
# an existing value is major.**
#
# 2.0 is that rule applied to the finding-attribution cutover. ``grounds``
# becoming required on every threat is additive and would have been minor on
# its own; what earns the major is that ``nodes[].node`` changed the *values*
# it carries, from ``analyst_<category>`` to ``analyze_<category>``. A consumer
# keying on ``analyst_spoofing`` does not error — it matches nothing, silently.
#
# 2.1 adds ``nodes[].usage``. Purely additive — an optional object on a record
# that already existed, no field changing meaning or spelling — so it is minor
# by the rule above, and a 2.0 consumer that ignores unknown fields reads a 2.1
# report unchanged.
#
# 2.2 adds ``unresolved_mentions``, the marks for element IDs a description
# cites in prose that the model does not contain. A new optional top-level
# list, exactly the shape ``unverified_grounds`` already had, so it is minor by
# the same rule: a 2.1 consumer that ignores unknown fields reads a 2.2 report
# unchanged, and one that renders the marks gains a signal it never had.
#
# 2.3 adds ``missing_mitigations``, on the same argument again: a third
# optional top-level list of service-owned marks, no existing field changing
# meaning or spelling.
#
# 2.4 adds ``coverage``, the per-category account of what deterministic
# analysis put in front of each agent and how much of it the drafts came back
# citing. Optional, additive, service-owned and computed in code, so the same
# rule applies a fourth time.
#
# 2.5 adds ``shared_element_names``, the marks for elements of different types
# whose names normalize to one slug. A fifth optional top-level list of
# service-owned marks, no existing field changing meaning or spelling, so the
# rule holds a fifth time. Minor rather than major although it is the first
# mark about the *model* rather than the threats: what a consumer must do with
# an unknown field does not depend on what the field describes.
#
# 2.6 widens the *values* the ``sampling`` clear block can carry to every type
# a resolved sampling param holds — which now includes the reasoning effort's
# enum string. No field is added, removed or renamed. It is the first entry
# here that is a fix rather than an addition: the block was typed to numbers
# only, so a deployment that set ``thinking`` — an offered, documented,
# build-gated param — produced reports that could not be assembled at all, and
# failed at the end of a paid-for run rather than at startup. No report with
# such a value has ever existed, so nothing a 2.5 consumer already parses
# changes meaning; what changes is that a value it never could have seen is now
# reachable, and a consumer reading the block as numbers must widen with it.
#
# 2.7 adds ``analysis_context``: the instruction digest, the domain packs this
# job's model earned, and the deterministic rules that fired. Optional,
# service-owned and computed in code, so the additive rule holds again — and it
# is the first block recording what *informed* the analysis rather than what
# the analysis found. It is not evidence and cannot become any: nothing here
# supports a threat, and the ``grounds`` that do are untouched.
#
# 2.8 adds ``knowledge_docs`` to that same block: the local-corpus documents
# the fired rules retrieved for the agents. Additive and service-owned like the
# rest of the block, and under the same rule — a document informed the
# analysis, and no consumer may read one as support for a threat.
#
# 2.9 adds ``unresolved_evidence``, a sixth optional list of service-owned
# marks: evidence references a threat cited that its job's catalog did not
# hold. Additive by the same rule as the four mark lists before it.
#
# What moved beside it is a *behaviour*, not a field. Such a reference used to
# fail the whole job; it is now dropped and marked, and only a threat left with
# no grounds at all still fails (#138). No existing field changes meaning or
# spelling, so this stays minor — but a consumer that treated a returned report
# as "every citation resolved" was relying on an absence rather than on a
# field, and this list is where that guarantee now lives.
# 2.10 corrects what ``coverage[].elements_cited`` counts, and holds every
# ``*_cited`` half to the total beside it. The field's *definition* is unchanged
# — the docs always read it as "of the elements in the model, how many the
# drafts cite" — but the computation counted prose citations raw, so an ID a
# description named that the model does not contain was counted as a cited
# element. That put the numerator above its denominator, and it did so hardest
# on the runs ``unresolved_mentions`` exists to flag.
#
# The second entry here that is a fix rather than an addition, and unlike 2.6 a
# value this schema *did* emit is now refused: a stored row with more cited than
# offered no longer re-validates. Minor by the rule above, because no field is
# added, removed or renamed and none changes meaning — what changes is that the
# number finally means what the field always said it did. A consumer that
# computed a citation rate off an affected row read a rate over 1.0 and will now
# read a smaller, correct one.
#
# 3.0 is the framework cutover, and it is major on every count the rule names:
# fields move, a field changes its spelling, and one changes what it carries.
# ``threats`` and seven other top-level fields become ``analyses[].claims`` and
# their per-framework siblings; the four mark classes rename ``threat_id`` to
# ``claim_id``; ``coverage[].category`` becomes ``coverage[].lane``; and every
# claim gains the required ``(framework, framework_version)`` pair.
#
# **There is no version gate and none is needed.** ``Report`` keeps
# ``extra="forbid"``, so a 2.10 payload carrying ``threats`` at the top level is
# refused by this model, and a 3.0 payload carrying ``analyses`` is refused by
# the old one. The no-shim behaviour falls out of the shapes rather than out of
# anything reading ``schema_version``.
#
# 3.0 also carries a fourth ``GroundKind``, ``absent-attribute`` (#171), which
# would have earned a major bump of its own had it arrived separately: a
# consumer switching over the three kinds it knew now meets a fourth. It rides
# this one instead because 3.0 has never shipped, and two hard cutovers for one
# release is a cost paid twice for nothing.
# 3.0 also carries ``nodes[].attempts``, the provider-call count the retry
# driver stamps on each LLM node. Optional with a default of 1, and it rides
# the unshipped major for the same reason.
# 3.0 also carries ``unknown_claim_identities``, a seventh list of
# service-owned marks: a claim naming an identifier its framework's own catalog
# does not hold. It rides 3.0 for the same reason ``absent-attribute`` does —
# 3.0 has never shipped — and it would have been additive and minor on its own.
#
# What moves beside it is again a *behaviour*. Such a claim used to reach the
# report and cite the standard's version-safe reference format for a
# requirement the standard does not contain; it is now dropped and marked, on
# the rule 2.9 already set for a citation that resolves to nothing. Only a
# framework carrying a catalog it did not author can produce one, so the list
# is empty for STRIDE by construction rather than by accident.
#
# 3.0 also carries ``dropped_claims``, an eighth list of service-owned
# marks: a claim that lost every ground it cited, at evidence resolution or at
# the quote check. It rides 3.0 for the same reason the two before it do.
#
# What moves beside it is every whole-job failure one entry of one claim could
# cause: a proposal that fails its own schema, a claim that lost every ground,
# every element it named, or its ID to an earlier draft. Each is now dropped
# and marked with its reason. ``unresolved_references``, a tenth list, records
# an element ID dropped from ``affected_element_ids`` the way
# ``unresolved_mentions`` records one dropped from prose.
#
# 3.0 also carries ``repaired_quotes``, a ninth list: a quote ground the
# ladder refused and the service rewrote to the source's own nearest span. The
# ground carries the submitter's words after that, and the mark carries the
# agent's, so a consumer reading a quote as "what the agent wrote" must read
# this list too.
# 3.0 also carries ``unreconciled_rulings``, an eleventh list: how the *first*
# critic pass failed to reconcile with its drafts, before the bounded re-ask
# repaired it. Each entry is a record — the claim, a closed ``kind``, and the
# sentence — rather than the sentence alone (#710). A consumer reading the
# list as strings meets objects, which would be major on its own; it rides
# 3.0 for the reason every change above does, and because the only way to
# count a cause before it was a regular expression over prose. Archived runs
# were typed by
# ``evals/migrations/2026-09-10-unreconciled-ruling-kinds.py``, which reads
# the kind out of the sentence once so nothing downstream ever does.
#
# 3.0 also carries a fifth ``GroundKind``, ``absent-element``: a term no element
# of the model names, which is the only branch whose referent is the whole model
# rather than a part of it. A consumer switching over the four kinds it knew now
# meets a fifth whose ``element_id`` and ``flow_id`` are both empty. It rides 3.0
# for the reason ``absent-attribute`` does — 3.0 has never shipped — and it would
# have been major on its own. The reason is a property rather than a package's
# name: **a framework may need to justify a claim by the absence of a thing from
# the model, and every other branch can only name something present.**
#
# 3.0 also carries ``rejected_because`` on a verdict: which of the critic's
# checks — the draft's own substance, the lane it was filed in, or another
# draft already covering it — ended a rejected draft. A consumer reading the
# rejected array as an audit trail had to parse the reason prose for this and can
# now read a field. It rides 3.0 because 3.0 has never shipped, and it is
# additive: ``None`` is the honest answer for a rejection recorded before the
# field, so a report written then still validates and still reads. The
# substance check answers in two values, ``evidence`` and ``reasoning``, because
# only the first rules on the draft's unit (#659); a consumer switching over the
# three it knew meets a fourth, which also rides the unshipped 3.0.
#
# A ``needs-info`` verdict names what has to be answered in one of two
# spellings: an element and one of its attributes, or a ``subject`` — a question
# with no place in the System Model at all. A consumer switching on the element
# pair alone meets an entry where both halves are empty and ``subject`` carries
# the whole of the question, which is why the second spelling is a 3.0 change
# rather than a minor one. The reason is a property rather than a package's
# name: **a framework may need a fact the system model has no slot for.**
#
# 3.0 also respells one ``scope[].state`` value and adds a fourth (#659).
# ``applicable`` is now ``not-raised``: the old word read as a verdict that
# the unit applies, where the fact is only that no lane raised a claim on it.
# ``undecidable`` is new, for a framework whose precondition could not tell
# whether it applies at all; it was folded into ``not-applicable`` before,
# which told a reader the unit was ruled out when the input had never said.
# Both would be major on their own, and both ride 3.0 because it has never
# shipped. The archived sweeps under ``evals/runs/`` were migrated by
# ``evals/migrations/2026-09-07-scope-states.py``.
#
# 3.0 also adds ``model_repair`` to the envelope (#675): what the repair pass
# was allowed to change and which elements it changed anyway and had put
# back. ``None`` where no repair ran, and on archived runs that did.
#
# 3.0 also adds two fields to ``repaired_quotes[]`` (#675): ``moved``, what
# the substitution changed in the claim's own terms (a negation, a number),
# required and checked on load against the two texts it is computed from; and
# ``scan_complete``, whether the rung ranked every window, ``None`` where the
# run predates the field. The 18 archived repairs were filled in by
# ``evals/migrations/2026-09-07-repaired-quote-moved.py``, which recomputes
# ``moved`` from the texts and records no ``scan_complete``.
#
# 3.0 also tightens what two ``coverage[]`` halves count (#675). A control is
# ``unknown_controls_cited`` only where a draft's attribute ground names that
# element *and* that attribute, where it used to be credited for every control
# on any element a draft cited; a candidate is ``candidates_cited`` only where
# one draft cites every element it names, where a union across the lane's
# drafts used to do. Both are a meaning change to an existing field and would
# be major on their own. Archived rows carry the old, larger numbers.
SCHEMA_VERSION = "3.0"

# The envelope's disclaimer, which is about the *service* rather than about any
# one framework. It no longer says "threat model": that is false of a report
# whose blocks include a framework that rules on requirement applicability
# rather than on attacks, and a sentence that is false of half a payload is
# worse than a general one. Each package carries its own, from
# ``frameworks/<name>/disclaimer.md``, saying what its own claims assert.
DEFAULT_DISCLAIMER = (
    "AI-generated security analysis. Not reviewed by a human security analyst."
)

# One value in a report's per-tier sampling clear block. Wide on purpose: it is
# every scalar type a resolved sampling param can hold, and the block is a
# *record* of what a run resolved rather than a place a value is decided. The
# enumeration is `TierSampling`'s field types — number, count, flag, and the
# reasoning effort's enum string — and `tests/test_report.py` pins the two in
# step, because a param whose type this cannot carry does not fail at load
# time: it fails when the report is assembled, after the whole graph has been
# paid for.
#
# Nothing is validated by being narrow here. The values arrive from a
# `TierSampling` that already validated them — range, enum and reserved-param
# rules all live there — and this module cannot import it without cycling
# through skills. Narrowing this union would not add a check; it would only
# decide which correctly-configured deployments can produce a report.
SamplingValue = bool | int | float | str | None


class TokenUsage(BaseModel):
    """What one node execution spent, as the provider reported it.

    Vendor-neutral field names, on the same principle as the model tiers: the
    provider's own spellings (``prompt_token_count``,
    ``candidates_token_count``, ``thoughts_token_count``,
    ``cached_content_token_count``) are one vendor's product vocabulary, and
    this record outlives the vendor it was read from.
    ``analysis_service.execution`` owns the mapping.

    NOTHING HERE IS DERIVED, and that is the point. ``total_tokens`` is
    recorded rather than summed, and no validator asserts a relationship
    between the parts, because the parts do not agree across vendors on what
    they contain: Gemini reports ``thoughts`` *outside* ``candidates``, while
    an OpenAI-family reasoning model counts them *inside* completion tokens.
    A sum check would fail honest data from one of them, and a derived total
    would silently mean two different things depending on who answered. Same
    rule NodeRun already applies to ``model`` and ``requested_model``: record
    both, compute neither.

    ``reasoning_tokens`` is the field this record was added for. It is spent
    against the tier's ``max_output_tokens`` and it is invisible in the
    output, so a node can be the run's largest consumer while looking small.
    ``cached_prompt_tokens`` is the other one: it is the only direct evidence
    of whether a prefix a prompt was laid out to cache actually cached.

    Every field defaults to 0 rather than being required — a provider that
    reports three of the five is common, and dropping the record for the two
    it withheld would lose the three it gave. A node that reported *nothing*
    carries no ``TokenUsage`` at all, so an all-zero record never stands in
    for an absent one.
    """

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    cached_prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class NodeRun(BaseModel):
    """Per-node execution metadata: which model ran, its sampling identity, timing.

    Two model fields, because they answer different questions and the report
    **records both rather than computing either**:

    * ``model`` is the **served** build, vendor-prefixed —
      ``vertex_ai/gemini-2.5-pro-002``. What actually answered, read back from
      the response.
    * ``requested_model`` is the **configured** route —
      ``vertex_ai/gemini-2.5-pro``. What the run asked for.

    Their disagreement *is* the drift signal, and it needs no comparison logic
    here: drift falls out of certification for free, because a moved build
    yields a fingerprint no manifest blessed. A served-vs-configured comparison
    would instead need a per-vendor served-id normalization table — a fourth
    mirrored vendor fact, and one that fails silently.

    **The served build is what the provider said, and nothing here verifies
    it.** ``model`` is read off the provider's own event stream, so a
    compromised translator can put any string in it. That is why the two fields
    are both recorded and why ``execution_fingerprint`` binds both: a manifest
    blesses the *pair*, and the requested half comes from the deployment's
    configuration rather than from the provider. The report states the trust
    level once, on the envelope's ``execution`` block, rather than repeating it
    on every node.

    ``execution_fingerprint`` is the identity hash a deployment's manifest
    blesses: ``sha256`` of the versioned execution identity — both routes, the
    resolved tier sampling, the built graph's instruction digest, and the
    versions of the distributions that sit between the node and its provider.
    See :mod:`analysis_service.identity`. Computed per node *execution*, so a
    build that moves partway through an eval sweep gives one node two hashes.

    ``instruction_sha256`` is the digest of the graph this node ran in, and on a
    report it repeats what ``analysis_context`` already says. **The repetition is
    load-bearing.** A report holds one graph, so the two always agree there — but
    a :class:`NodeRun` also travels alone: an eval sweep folds one flat list of
    them across several graphs, because a case declares which frameworks it
    carries and each distinct selection builds its own. In that list the report
    block is gone and the node's own digest is the only thing that says which
    instruction set produced its fingerprint. A row carrying a hash it cannot
    account for is a row nobody can verify.

    A deterministic FunctionNode carries none of these four.

    ``usage`` is what the node execution cost, ``None`` for a deterministic
    FunctionNode and for any LLM node whose provider reported nothing. It is
    deliberately *not* coupled to ``model`` the way ``execution_fingerprint``
    is: a fingerprint keyed on a served build is incoherent without one, but a
    token count is a fact about the call regardless of whether the provider
    also named the build that served it, and refusing to record it would
    discard a real measurement to satisfy a symmetry nobody needs.

    ``attempts`` is how many provider calls the execution took, counted by the
    retry driver. ``usage`` meters the one that answered; a failed attempt
    reports nothing, so the count is the only trace the prompt bytes it sent
    leave. A settlement charges them from it (see
    :func:`analysis_service.budgets.measured_tokens`).

    ``served_trust`` says what this row's ``model`` is worth as evidence, and it
    is **here rather than once per report**. A deployment may select a
    different vendor per tier, so one report can hold rows whose served builds
    are worth different things, and a single value for the whole run has to be
    wrong on one of them. It is *derived* from ``requested_model`` rather than
    stored, so the report and the fingerprint read one rule through one reader
    and cannot disagree.
    """

    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=100)
    model: str | None = None  # served; None for deterministic FunctionNodes
    requested_model: str | None = None  # configured
    instruction_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)
    usage: TokenUsage | None = None
    attempts: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _drop_derived_trust(cls, data: Any) -> Any:
        """Ignore a ``served_trust`` on input; it is output, not state.

        The field is serialized so a reader of a stored report can see it, which
        means a round trip hands it straight back. Recomputing from
        ``requested_model`` rather than trusting the key is what keeps one rule
        to one reader: an edited value cannot survive, and the value inside the
        fingerprint is derived the same way from the same route.
        """
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "served_trust"}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def served_trust(self) -> ServedTrust | None:
        """What ``model`` is worth as evidence, for the vendor that served it.

        ``None`` on a deterministic FunctionNode, which asked no provider and
        has no served build to weigh.
        """
        if self.requested_model is None:
            return None
        return vendor_for_route(self.requested_model).served_trust

    @model_validator(mode="after")
    def _fingerprint_needs_its_inputs(self) -> Self:
        # Every per-node input to the identity has to be here beside the hash,
        # or the row states a fingerprint nobody can rebuild. A deterministic
        # node has none of them and no fingerprint, which is the consistent
        # absence.
        if self.execution_fingerprint is None:
            return self
        missing = [
            name
            for name, value in (
                ("a served model", self.model),
                ("a requested model", self.requested_model),
                ("an instruction digest", self.instruction_sha256),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                f"execution_fingerprint requires {' and '.join(missing)}"
                " on the same node"
            )
        return self


class ExecutionEnvelope(BaseModel):
    """What executed this run, and how much its self-report is worth.

    One block per report rather than one per node, because every node in a
    drive ran on the same install and asked the same graph. Repeating it would
    be the same fact ten times over, free to drift.

    ``identity_version`` is the schema of the payload each node's
    ``execution_fingerprint`` hashes. A reader recomputing a fingerprint needs
    it, and certification refuses a manifest written for a different one rather
    than comparing across them.

    What a served build is *worth* is **not** here. It was
    ``served_model_trust``, a constant reading ``provider_reported`` for the
    whole report, and it was wrong twice over: a translator that fills the
    served identifier from the request confirms nothing, and a deployment may
    select a different vendor per tier, so one report can hold rows with
    different answers. It moved to :attr:`NodeRun.served_trust`, where the
    vendor is known and one reader answers for every caller.

    ``build`` is the installed version of every distribution whose code sits
    between a node and its provider — this service, the agent runtime and the
    model translator. It is here rather than in a deployment note because it is
    inside the fingerprint: a ``litellm`` bump moves every hash, and a reader
    who cannot see which version ran cannot tell a sanctioned run from a
    silently upgraded one.

    ``review_independence`` is how far this deployment required each framework's
    criticism to sit from its own analysis. It is a **statement, never a
    warning**: a deployment that asked for an independent reviewer and could not
    have one fails to load, so no report exists to warn on. What this answers is
    the other direction — a reader of a ``shared`` run can see the review was
    same-domain rather than infer it from two node rows naming one model. The
    detail behind it is already on :class:`NodeRun`: the ``analyze/<name>`` and
    ``critic/<name>`` rows each carry their own requested route, served build
    and fingerprint.

    Deliberately **not** in the fingerprint. The policy decides nothing at run
    time — the loader has already enforced it — so hashing it would re-baseline
    every blessed identity on a policy edit that moved no model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_version: int = Field(ge=1)
    build: dict[str, str] = Field(default_factory=dict)
    review_independence: Literal["shared", "distinct_model", "distinct_provider"] = (
        "shared"
    )


class ModelRepair(BaseModel):
    """What the one repair pass was allowed to change, and what it changed anyway.

    Recorded by the revalidate gate. ``scope`` is ``elements`` when every
    issue named an element, and then ``implicated`` is those elements: the
    repair may change them, add elements, and nothing else, and every other
    element is put back as it was. ``restored`` names the ones that had to be,
    because the repair changed or dropped an element the issues never cited.
    ``scope`` is ``whole`` when an issue named no element — a fault over the
    whole object — and then nothing is put back, because no narrower patch
    could answer it; ``implicated`` and ``restored`` are empty.
    """

    model_config = ConfigDict(extra="forbid")

    scope: Literal["elements", "whole"]
    implicated: list[str] = Field(default_factory=list)
    restored: list[str] = Field(default_factory=list)


class AnalysisContext(BaseModel):
    """What informed the analysis, as distinct from what proves a finding.

    The report already records the two ends of a run: the served build and
    sampling each node ran on (:class:`NodeRun`), and the facts each finding
    rests on (``grounds``). Between them sits everything the service *put in
    front of* the lane agents — the instruction text they were given and the
    reference packs this model earned — and none of it was recorded anywhere. Two runs of the same model on the same
    input could differ because a pack selection flipped or a skill was edited,
    and the report showed nothing.

    **This is context, never evidence, and the separation is the whole design.**
    A pack named here did not ground anything; a rule named here did not find
    anything. What grounds a finding is its ``grounds``, unchanged. This block
    answers a different question — *what was on the desk* — and a reader who
    treated an entry here as support for a threat would be reading it exactly
    backwards.

    ``instruction_sha256`` digests the *composed instruction* of every LLM node
    in the built graph, with the job-varying placeholders still unexpanded. So
    it identifies the repo-authored text — the shared prompts and every carried
    package's lane skills, exemplars, critic text and rubric — and carries no
    submitter bytes at all, which is what makes it
    publishable beside a report. The digest is also one of the seven parts of
    the **Execution Identity**, so two runs told different things cannot share
    a fingerprint, and this field is what lets a reader recompute one and see
    which instruction set stood behind it.

    ``domain_packs`` is a fact about *this job's* model rather than the
    deployment: selection is per-job (:mod:`analysis_service.domains`), so the
    same service gives two submissions different reference material, and the
    names are the only record of which.

    **``fired_rules`` and ``knowledge_docs`` are not here**, and the rule that
    moved them is the one that sorted every field of the flat schema this
    replaced: a field sits where the thing it describes sits. A candidate rule
    belongs to the package that declared it and a retrieved document to the
    package that selected it, so both name *one framework's* material and both
    sit on :class:`FrameworkAnalysis`. The two that stayed describe the built
    graph and the shared model, of which a report has one each.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain_packs: list[str] = Field(default_factory=list)


class FrameworkSelection(BaseModel):
    """One framework a job asked for, and the options it asked for it with.

    ``options`` defaults to ``{}`` **on the envelope only**. The package's own
    options model then rejects a submission that left out a value it needs, and
    names the field it wants. No package field carries a default, so no
    submission means two different things on two installs.

    Recorded on the :class:`Job` exactly as the input ladder resolved it,
    because a report that omits the options does not say what was analysed — a
    framework whose options select which requirements apply produces a different
    answer under different ones.
    """

    model_config = ConfigDict(extra="forbid")

    name: FrameworkName
    options: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    """Identity and timing of the run that produced this report.

    A report only exists for a completed job, so ``status`` admits exactly the
    ``completed`` state from the job-lifecycle contract.

    ``frameworks`` is what was *asked for*, in submission order. The envelope's
    own check reads it: the analysis blocks must answer exactly this list, in
    this order, so a framework that produced nothing cannot be dropped quietly.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    status: Literal["completed"] = "completed"
    created_at: datetime
    completed_at: datetime
    #: How many times a critic was asked again, summed across the job's
    #: frameworks. The graph re-asks a critic whose rulings do not reconcile
    #: with the drafts, once and bounded, so zero is the ordinary answer and a
    #: rate that climbs after a prompt edit says the edit made the ruling harder
    #: to give. Computed by :func:`~analysis_service.graph.revise_rounds`.
    revise_rounds: int = Field(default=0, ge=0)
    frameworks: list[FrameworkSelection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_timeline(self) -> Self:
        if self.completed_at < self.created_at:
            raise ValueError("completed_at precedes created_at")
        return self


class SourceRef(BaseModel):
    """One submitted source, identified without carrying its text.

    The label is the same key an element's ``source_label`` cites, so a reader
    holding only the report can tell which source a quote came from and verify
    that source's bytes against a digest — without the service ever storing the
    untrusted text.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _stored_system_name(value: str) -> str:
    """One stored system name, checked against the rule that wrote it.

    :func:`~analysis_service.sources.clean_system_name` is that rule and this
    is its third caller, beside the HTTP route and the in-process engine. A
    stored name has to be a name that rule *produces*, so the check is equality
    with its answer rather than a repeat of the checks inside it.

    **It refuses and never repairs.** Returning the cleaned value would make a
    report validate into a model whose ``system_name`` is not the one on disk,
    and :mod:`analysis_service.attestation` digests the report — so a read,
    a re-dump and a re-digest would move a sealed value. A record the writer
    could not have produced is malformed, and saying so is the whole job here.
    """
    cleaned = clean_system_name(value)
    if cleaned is None:
        raise ValueError("names no system, so no report can carry it as a name")
    if cleaned != value:
        raise ValueError(
            "is not the name clean_system_name writes: it carries leading or"
            f" trailing whitespace, and the stored name would be {cleaned!r}"
        )
    return value


class InputRef(BaseModel):
    """Ties the report back to the exact submitted sources."""

    model_config = ConfigDict(extra="forbid")

    #: Already clean by the time a report is built — both entry points run
    #: :func:`~analysis_service.sources.clean_system_name` before a job exists.
    #: Checked again here because a report is also *read back*: an artifact or
    #: a saved report is deserialized into this model, and a stored name is
    #: only as trustworthy as whatever wrote it.
    #:
    #: It reads **the writer's own rule**, not a restatement of two of its
    #: three checks. Stating the bound and the character rule here left the
    #: trim rule behind, so this accepted ``"   "`` and ``"  Pay  "`` where the
    #: writer answers "no system named" and ``"Pay"`` — a second reader of the
    #: one rule #794 exists to give a system name.
    system_name: Annotated[str, AfterValidator(_stored_system_name)]
    sources: list[SourceRef] = Field(min_length=1)
    # Taken **over the refs**, not over the concatenated text: the refs are in
    # the report, so this stays recomputable from the report alone — which a
    # digest of bytes nobody kept would not be.
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @staticmethod
    def aggregate_digest(refs: Sequence[SourceRef]) -> str:
        """The one way the aggregate is computed, wherever a report is built.

        Order-sensitive and separator-delimited, so two jobs whose labels and
        digests merely concatenate to the same string do not collide.
        """
        joined = "\n".join(f"{ref.kind}\x1f{ref.label}\x1f{ref.sha256}" for ref in refs)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    @classmethod
    def of(cls, *, system_name: str, sources: Sequence[Source]) -> Self:
        """Build the reference for one job's sources, digests and all."""
        refs = [
            SourceRef(
                kind=source.kind,
                label=source.label,
                sha256=hashlib.sha256(source.text.encode("utf-8")).hexdigest(),
            )
            for source in sources
        ]
        return cls(
            system_name=system_name,
            sources=refs,
            source_sha256=cls.aggregate_digest(refs),
        )


def usage_by_node(nodes: Iterable[NodeRun]) -> dict[str, TokenUsage]:
    """Total tokens per node name, summed across that node's executions.

    The question this exists to answer is "which node costs the most", and a
    bare ``nodes`` list does not answer it: a sweep hands over every case's
    executions at once, so one node contributes one record per case, and
    reading a per-node total off the list means every caller writing the same
    fold. Keyed by node name rather than by tier
    because the tier is already answerable from the node, and the interesting
    comparison — the critic against one category agent, both on ``strong`` — is
    the one a tier roll-up destroys.

    Nodes reporting no usage contribute nothing and are absent from the result,
    rather than present with a zeroed record that reads as a free call.
    """
    totals: dict[str, TokenUsage] = {}
    for node in nodes:
        if node.usage is None:
            continue
        running = totals.get(node.node)
        if running is None:
            totals[node.node] = node.usage.model_copy()
            continue
        totals[node.node] = TokenUsage(
            **{
                field: getattr(running, field) + getattr(node.usage, field)
                for field in TokenUsage.model_fields
            }
        )
    return totals


class NodeLatency(BaseModel):
    """What one node's executions cost in wall-clock, folded across a sweep.

    The counterpart to :class:`TokenUsage`'s fold, and it differs in one way
    that matters: **every** execution carries a ``duration_ms``, so a
    deterministic FunctionNode appears here where it is absent from the usage
    totals. That is the point — the deterministic derivations are the half of
    the graph nobody bills for, and the only way to know they stay cheap is to
    see them beside the nodes that do cost money.

    ``slowest_ms`` is kept alongside the total because the mean is the wrong
    number for the question latency is usually asked for: a retry budget and a
    request timeout are both set from the tail, and a node whose mean is
    comfortable can still be the one that trips them.
    """

    model_config = ConfigDict(extra="forbid")

    executions: int = Field(ge=1)
    total_ms: int = Field(ge=0)
    slowest_ms: int = Field(ge=0)

    @property
    def mean_ms(self) -> float:
        return self.total_ms / self.executions


def latency_by_node(nodes: Iterable[NodeRun]) -> dict[str, NodeLatency]:
    """Wall-clock per node name, folded across that node's executions.

    Same fold as :func:`usage_by_node`, over the measurement the report keeps
    and nothing downstream has ever read back: a sweep's node runs carry
    ``duration_ms`` per execution, and without this the whole latency record
    dies with the process that measured it.

    A node absent from the result ran zero times in the sweep, which is a
    different fact from running fast — hence ``executions`` on every row.
    """
    totals: dict[str, NodeLatency] = {}
    for node in nodes:
        running = totals.get(node.node)
        if running is None:
            totals[node.node] = NodeLatency(
                executions=1,
                total_ms=node.duration_ms,
                slowest_ms=node.duration_ms,
            )
            continue
        totals[node.node] = NodeLatency(
            executions=running.executions + 1,
            total_ms=running.total_ms + node.duration_ms,
            slowest_ms=max(running.slowest_ms, node.duration_ms),
        )
    return totals


class Report(BaseModel):
    """The complete payload the front-end retrieves for a finished job.

    One job's facts, one **Valid System Model**, and one
    :class:`FrameworkAnalysis` per framework the job selected.

    Self-containment is enforced, not assumed. On the envelope: the boundary
    crossings are exactly the ones derived from the embedded model,
    ``elements_analyzed`` is that model's own element count, and the blocks are
    the frameworks the job asked for — in order, with none dropped and none
    repeated. Inside each block, :meth:`FrameworkAnalysis.block_issues` runs the
    neutral claim checks against the shared model.

    **A list, not a map.** ``analyses`` is ordered by the job's own selection
    order. A map keyed by name gives uniqueness free, but then the key and the
    block's own ``framework`` field can disagree, and a dropped framework is
    invisible. A list plus one check catches both, and silently dropping a
    framework the caller paid for is the failure this rules out.

    **The blocks are not merged, and nothing here merges them.** The relation an
    analyst wants between two frameworks' output is *these findings touch one
    element*, and the **Element ID** is the join key both blocks already cite --
    so a reader joins on it with no model pass, which is what *deterministic
    code, models for judgement* asks for. That is why no cross-framework critic
    node exists to merge them instead.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    disclaimer: str = DEFAULT_DISCLAIMER
    job: Job
    input: InputRef
    nodes: list[NodeRun]
    # The resolved per-tier decoding params this run used, in the clear, once
    # per tier: tier name -> the tier's resolved sampling values (the
    # serialized ``TierSampling``). Recorded as plain scalars, not the
    # ``TierSampling`` model, so this low-level schema module stays free of the
    # sampling/model_tiers import (which cycles back through skills). Each
    # node's execution_fingerprint is recomputable from its two routes and its
    # tier's entry here, together with ``analysis_context`` and ``execution``. Empty only on reports with no LLM provenance at all —
    # the stub runner's. An eval report carries this block like any other: a
    # sweep's fingerprints are evidence, and evidence nobody can recompute from
    # the artifact is an assertion.
    sampling: dict[str, dict[str, SamplingValue]] = Field(default_factory=dict)
    system_model: SystemModel
    boundary_crossings: list[BoundaryCrossing]
    # Elements of different types sharing one name slug — a suspicion about the
    # model rather than a fault in it, which is why it rides here and not in
    # the validity gate. The one mark that stays on the envelope: it annotates
    # the shared model rather than any framework's claims. Recomputable from
    # ``system_model`` by design.
    shared_element_names: list[SharedElementName] = Field(default_factory=list)
    # A fact about the shared model, so it is a scalar here rather than a field
    # each block copies: N copies would be N chances to disagree about one
    # number. It left ``Summary`` in this cutover for exactly that reason.
    elements_analyzed: int = Field(default=0, ge=0)
    # How the model was repaired, where it was. ``None`` where no repair pass
    # ran; a report whose ``nodes`` name the repair node and carry ``None``
    # here predates the field. Envelope-level because it is about the shared
    # model, like ``shared_element_names``.
    model_repair: ModelRepair | None = None
    # What was in front of the agents that is *not* one framework's: the
    # instruction digest of the built graph, and the domain packs this job's
    # model earned. Context, not evidence — see :class:`AnalysisContext`. The
    # per-framework halves live in each block. ``None`` on a report built
    # without it (the stub runner's), which is the same absence an empty
    # ``coverage`` records rather than a claim that nothing informed the run.
    analysis_context: AnalysisContext | None = None
    # What ran the graph, as distinct from what was in front of it: the identity
    # schema version, how far the served builds can be trusted, and the versions
    # of the distributions between a node and its provider. Every value here is
    # inside each node's ``execution_fingerprint``, so this block is what makes
    # a fingerprint recomputable from the artifact rather than from the machine
    # that happens to be reading it. ``None`` on a report built without LLM
    # provenance at all — the stub runner's — which is the same absence an empty
    # ``sampling`` records.
    execution: ExecutionEnvelope | None = None
    # **Declared as the base and serialized as itself.** The declaration is what
    # lets this module carry an envelope a framework it has never heard of fits
    # into; ``SerializeAsAny`` is what stops that generality from *costing* the
    # narrowing on the way out. Pydantic serializes a field by its declared
    # type, so without this a ``StrideAnalysis`` in this list would come back
    # out as a ``FrameworkAnalysis``: every severity, every category and both
    # summary breakdowns dropped, with nothing raised and nothing logged — on
    # the report route, which is the only path a caller ever sees.
    #
    # It is the exact mirror of :meth:`_dispatch_blocks`. That validator makes
    # the narrowing survive the way in; this makes it survive the way out. A
    # round trip through JSON is the property both exist for, and it is a
    # property only the pair has.
    analyses: list[SerializeAsAny[FrameworkAnalysis]] = Field(default_factory=list)

    @field_validator("analyses", mode="before")
    @classmethod
    def _dispatch_blocks(cls, value: Any) -> Any:
        """Validate each block as the type its own framework registered.

        The field is declared as the neutral base, because the envelope is what
        a second framework has to fit into without this module knowing its
        name. What makes the *narrowing* survive a round trip is this: a block
        naming ``stride`` is validated as STRIDE's own block type, so its
        severities, its categories and its two breakdowns come back typed rather
        than being refused as extra fields on the base.

        A build with no packages registered reads every block as the base.
        A block naming a framework this build does not carry also reads as the
        base, which is the honest outcome rather than a refusal: the neutral
        fields are exactly what such a reader can still be held to.
        """
        if not isinstance(value, list):
            return value
        dispatched = []
        for item in value:
            if isinstance(item, FrameworkAnalysis) or not isinstance(item, dict):
                dispatched.append(item)
                continue
            block_type = block_type_for(item.get("framework", ""))
            dispatched.append(block_type.model_validate(item) if block_type else item)
        return dispatched

    @model_validator(mode="after")
    def _check_self_contained(self) -> Self:
        issues = self._envelope_issues()
        known_ids = [element.id for element in self.system_model.elements()]
        for block in self.analyses:
            issues += block.block_issues(known_ids)
        issues += self._model_issues()
        if issues:
            raise ValueError("; ".join(issues))
        return self

    def _model_issues(self) -> list[str]:
        """Every ground resolves in the embedded model's own evidence catalog.

        Asked here rather than per block because the reader needs the model
        and not its ID set, and one model serves every block. The reader is
        the one the fan-in already runs
        (:func:`~analysis_service.evidence.ground_issues`), so a loaded report
        is held to what the service that wrote it was held to — an unknown on
        an attribute the model states, or a crossing the model never derives,
        does not ride in through a file.
        """
        claims = [claim for block in self.analyses for claim in block.all_claims()]
        return ground_issues(claims, self.system_model)

    def _envelope_issues(self) -> list[str]:
        issues = []
        if self.boundary_crossings != self.system_model.boundary_crossings():
            issues.append(
                "boundary_crossings do not match the crossings derived from"
                " the embedded system model"
            )
        element_count = len(self.system_model.elements())
        if self.elements_analyzed != element_count:
            issues.append(
                f"elements_analyzed={self.elements_analyzed} does not match the"
                f" embedded system model's {element_count} elements"
            )
        return issues + self._selection_issues()

    def _selection_issues(self) -> list[str]:
        """The blocks are the job's frameworks, in order, once each.

        This is the envelope's version of "no partial report": a framework that
        produced nothing cannot be dropped quietly, because a caller who named
        two and reads one has no field that says which half is missing. Order is
        checked as well as membership, since ``analyses`` is a list precisely so
        that a dropped entry is visible.
        """
        asked = [selection.name for selection in self.job.frameworks]
        answered = [block.framework for block in self.analyses]
        if answered == asked:
            return []
        return [f"analyses answer {answered!r}, but the job selected {asked!r}"]
