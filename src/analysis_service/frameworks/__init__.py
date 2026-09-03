"""Framework packages: what a security framework declares, and what checks it.

A package declares what its framework judges and what selects its text. The
service keeps what it constructs and what it checks. That one line decided every
row of this contract. Building a **Ground** from an agent's selection is
construction, so it stays in service code. Deciding which lane sees a lead is
judgement about a framework's own method, so the **Candidate** rules moved into
the package. The **Evidence Catalog** enumerates the **Valid System Model**
rather than the framework, so it stayed.

A package is a catalog it does not own plus a profile it does. Every member
below is profile, which is the tailoring this service applies. No member names a
catalog. A framework that publishes a machine-readable requirement set carries
it as its own private data and checks it in its own module, because a contract
that required one would exclude the frameworks that publish none.

Registration is a table edit and an import, exactly as it is for the vendor
registry. There is no entry point, no import by name from config, and no path in
a config file, because a package writes into a ``strong``-tier prompt. External
plugin loading is out of scope for this architecture.

Three sets must agree. :data:`~analysis_service.report.FrameworkName` names what
this repository can spell. :data:`PACKAGES` names what this repository carries.
``config/frameworks.toml`` names what this install runs. The first two agree at
import. The third agrees at :func:`validate_package`, which a **Deployment**
runs before an adapter binds and before an instruction composes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, get_args

from pydantic import BaseModel

from analysis_service.candidates import Rule
from analysis_service.errors import ConfigError
from analysis_service.markdown_loader import _inside
from analysis_service.report import (
    Claim,
    FrameworkAnalysis,
    FrameworkName,
    ProposalBatch,
    RuledClaim,
    RulingBatch,
)
from analysis_service.system_model import SystemModel

__all__ = [
    "CONTENT_LICENSE",
    "OUTPUT_DOC",
    "PACKAGES",
    "PRECONDITION_RESULTS",
    "SCHEMAS",
    "FrameworkPackage",
    "FrameworkPackageError",
    "FrameworkSchemas",
    "KnowledgeTables",
    "PreconditionError",
    "PreconditionResult",
    "package_for",
    "run_precondition",
    "schemas_for",
    "selectable_without_options",
    "validate_package",
    "widest_fan_out",
]

#: What a package's **Precondition** can answer about a **Valid System Model**.
#:
#: **Three results, not two**, and the third is not a rounding of the second.
#: This repo already refuses to collapse "no" into "nothing said" for an
#: **Unknown** attribute, for a **Provider Capability** and for a **Provider
#: Smoke** answer, and the reason is the same here: the caller's remedy differs.
#: ``refuted`` means do not name this framework for this system; ``undecidable``
#: means the input never said, and answering it is a matter of submitting more.
#:
#: Fails closed — only ``satisfied`` runs the framework.
PreconditionResult = Literal["satisfied", "refuted", "undecidable"]

#: The three states, as a value the run-time gate can check a return against.
PRECONDITION_RESULTS: tuple[PreconditionResult, ...] = get_args(PreconditionResult)

Precondition = Callable[[SystemModel], PreconditionResult]

#: The five fixed H2 sections of a lane skill, in order. ``Scope`` must be first
#: and must be present in every lane: the critic's lane digest is assembled from
#: those sections, so a lane without one gives its framework's critic nothing to
#: dedupe against.
LANE_SECTION_HEADINGS: tuple[str, ...] = (
    "Scope",
    "Applicability",
    "Threat Patterns",
    "Guardrails",
    "Mitigations",
)

#: The text a package carries under its root, by **convention rather than
#: declaration**. A declared path would let a package name a file freely and
#: buys nothing; a convention plus a checked rule gives the same guarantee with
#: fewer members to get wrong.
#:
#: Per lane: ``lanes/<lane>/skill.md`` and ``lanes/<lane>/exemplars.md``.
#: Per package: ``critic.md``, ``disclaimer.md``, ``output.md``, and
#: ``severity_rubric.md`` exactly when the record carries a ``severity`` field.
#:
#: ``output.md`` is what makes the shared ``analyze.md`` genuinely neutral. That
#: prompt says what to do with a job's input, which every framework's lane agent
#: does alike; what one *claim* is and which fields carry it are the package's,
#: and a second framework whose record grades nothing cannot read a field list
#: naming ``severity``. So the field list travels with the record it describes.
CRITIC_DOC = "critic"
DISCLAIMER_DOC = "disclaimer"
OUTPUT_DOC = "output"
SEVERITY_RUBRIC_DOC = "severity_rubric"


class FrameworkPackageError(ConfigError):
    """A package this deployment carries is ill-formed or incomplete on disk.

    A ``ConfigError`` rather than anything softer, because this fails a
    **Deployment**'s construction: a package that declares a lane with no prompt
    must not reach a model call, and the only way to guarantee that is to refuse
    to start.
    """


class PreconditionError(FrameworkPackageError):
    """A package's precondition raised, or answered outside the three states.

    **A defect in the build, not a fact about the input.** The member is typed
    and mypy covers its construction, so reaching this means package code that
    the type checker did not see — and the only safe reading of an answer the
    contract does not define is no reading at all.

    Never read as ``refuted``, which is the whole reason this is an error rather
    than a fallback: refusing a framework on a defect drops a whole analysis the
    caller asked for, and the caller reads no sign of it.

    A :class:`FrameworkPackageError` because the subject is the same — a package
    this deployment carries is ill-formed — even though this one fires at a call
    site rather than at construction. :func:`run_precondition` says why it cannot
    fire earlier.
    """


@dataclass(frozen=True)
class KnowledgeTables:
    """One package's local corpus, as document name -> the rules that select it.

    The direction is **document-to-rules** deliberately. A case is selected by
    rules across several lanes, so a rule-keyed table would spread one
    document's applicability over several entries, and what a maintainer edits
    is a document and its applicability.

    Required on every package, and a package shipping no corpus writes two empty
    tables. That follows the rule that no package field carries a default, and
    it makes "this package ships no corpus" a written statement rather than an
    omission; the gate passes it vacuously.

    Frontmatter inside each document was rejected as the alternative: those raw
    bytes go straight into a ``strong``-tier prompt, and an unstripped header
    would leak rule IDs into the instruction.
    """

    notes: Mapping[str, tuple[str, ...]]
    cases: Mapping[str, tuple[str, ...]]

    def documents(self) -> tuple[str, ...]:
        """Every document this package declares, as ``<dir>/<name>`` paths."""
        return (
            *(f"notes/{name}" for name in self.notes),
            *(f"cases/{name}" for name in self.cases),
        )

    def rule_ids(self) -> frozenset[str]:
        """Every rule ID either table names."""
        return frozenset(
            rule
            for table in (self.notes, self.cases)
            for rules in table.values()
            for rule in rules
        )


@dataclass(frozen=True)
class IdRule:
    """How one package composes a claim ID, **as data rather than code**.

    Three parts, one member. Keeping them together is what lets the package
    contract stay at nine members while the ID rule carries everything the one
    neutral resolver needs: without the lane field the resolver could not stamp
    the lane it is resolving, and stamping it is what makes a draft's lane, its
    ID's prefix and the node that produced it agree by construction.

    ``template``
        A :meth:`str.format` template over ``prefix`` and ``key``.
        Repo-authored and never caller-supplied, so an agent's value cannot walk
        an attribute through ``format``.
    ``prefix``
        One prefix per lane. STRIDE's is the category letter; a framework whose
        IDs carry no lane marker declares the empty string for each.
    ``lane_field``
        The record field the lane is stamped into, or ``None`` where the record
        carries no lane. The agent never spells it: the lane is the graph's
        fact, filled in before any model runs.
    ``known``
        Whether a ``(lane, key)`` pair names something this framework actually
        has, or ``None`` where every well-formed key does. **The rule that
        composes an identifier is the one thing that can say whether a
        composition is real**, which is why this is a fourth part here rather
        than a tenth member on the package: it asks about the framework's
        identifiers, and it never names a catalog. A package that mints its own
        identifiers — STRIDE's ``S-01`` is a lane letter and a counter —
        declares ``None``, because there is nothing for a key to fail to be. A
        package restating a catalog it did not author answers from that catalog,
        privately, in its own module.

        The predicate does not raise and does not explain. A false answer costs
        the claim its place and earns a
        :class:`~analysis_service.report.UnknownClaimIdentity` mark; see
        :func:`~analysis_service.evidence.resolve_proposals`.
    """

    template: str
    prefix: Mapping[str, str]
    lane_field: str | None
    known: Callable[[str, object], bool] | None = None

    def compose(self, lane: str, key: object) -> str:
        return self.template.format(prefix=self.prefix[lane], key=key)

    def knows(self, lane: str, key: object) -> bool:
        """Whether this pair names something the framework has.

        ``True`` when no predicate is declared, which is the honest answer for a
        package whose identifiers are its own to mint: there is no roster for a
        key to be absent from, so absence is not a state its keys can be in.
        """
        return True if self.known is None else self.known(lane, key)


@dataclass(frozen=True)
class FrameworkPackage:
    """One security framework as an object the service can run.

    Nine members, plus text under one root by convention.

    ``name``
        The closed :data:`~analysis_service.report.FrameworkName`. A package
        cannot invent a name.
    ``version``
        Required and non-empty. A framework identifier with no version is
        uninterpretable one release later, which is why every :class:`Claim`
        carries the pair.
    ``lanes``
        Ordered, non-empty, unique slugs. **One lane is one Model Tier call.**
    ``rules``
        The deterministic **Candidate** table. Each rule names a lane this
        package declares.
    ``record``
        The Pydantic subclass of :class:`Claim` this package's agents produce.
    ``id_rule``
        The ID rule, as **data rather than code**, so that *the agent selects
        and the service constructs* stays a construction: one neutral resolver
        composes every claim ID and stamps every lane from it. See
        :class:`IdRule`.
    ``options``
        The job-level values this framework needs, as a Pydantic model. **No
        field may carry a default**, so no submission means two different things
        on two installs.
    ``precondition``
        What this framework asks of a **Valid System Model** before any of its
        lanes runs. STRIDE's is total — every valid model satisfies it — and it
        is a declared predicate rather than an absence, so no package is exempt.
        The graph runs it once per selected framework, after ``extract`` and
        before the fan-out, through :func:`run_precondition`. Only ``satisfied``
        runs the lanes; a refused framework still produces a block that states
        the reason.
    ``knowledge``
        This package's **Reference Note** and **Worked Case** tables. They live
        here because their retrieval key does: selection is a set intersection
        over *this package's* fired rules, so a document the service stored
        would have no service-side caller.
    """

    name: FrameworkName
    version: str
    lanes: tuple[str, ...]
    rules: tuple[Rule, ...]
    record: type[Claim]
    id_rule: IdRule
    options: type[BaseModel]
    precondition: Precondition
    knowledge: KnowledgeTables

    def rules_for(self, lane: str) -> tuple[Rule, ...]:
        """This package's rules in one lane, in declaration order."""
        return tuple(rule for rule in self.rules if rule.lane == lane)

    def compose_id(self, lane: str, key: object) -> str:
        """One claim ID, composed from this package's own :class:`IdRule`."""
        return self.id_rule.compose(lane, key)

    def lane_fields(self, lane: str) -> dict[str, str]:
        """What the resolver stamps the lane into, or nothing if it carries none."""
        field = self.id_rule.lane_field
        return {field: lane} if field else {}

    def carries_severity(self) -> bool:
        """Whether this package's record grades harm.

        Reads the record's own fields rather than a declared flag, so the rubric
        the gate demands and the field that would read it cannot disagree.
        """
        return "severity" in self.record.model_fields


@dataclass(frozen=True)
class FrameworkSchemas:
    """The five shapes one framework's *model calls* speak in.

    Beside :class:`FrameworkPackage` rather than inside it, and the split is the
    nine-member contract's own: a package member says what this framework judges
    and what selects its text, which is what the deployment gate can check and
    what a maintainer edits. These five are the wire between the graph and a
    provider — the types an ``output_schema`` compiles to and the types a node's
    emission validates as — and none of them is a declaration about the
    framework's method.

    Registering a package fills both tables in one edit, exactly as the vendor
    registry is one table edit and an import.

    ``proposals`` / ``rulings``
        The wrappers a lane agent and a critic emit, narrowing
        :class:`~analysis_service.report.ProposalBatch` and
        :class:`~analysis_service.report.RulingBatch` to this framework's own
        element types. The graph never names the field inside; it unwraps
        ``claims``, which is neutral because the prompt that asks for it is
        shared.
    ``ruled_record``
        What a draft becomes once its ruling is merged on: the package's own
        :class:`~analysis_service.report.RuledClaim`. The draft side is the
        package's ``record`` member, because that is what the gate checks and
        what the resolver builds; this is the shape the report carries.
    ``block``
        The :class:`~analysis_service.report.FrameworkAnalysis` subclass this
        framework's output validates as, narrowing the claim arrays and the
        summary. The envelope dispatches on ``framework`` to read the right one
        back.
    ``key_field``
        The one field of ``proposals``' element type that is the agent's **ID
        key** — STRIDE's ``sequence``, and a requirement-shaped framework's
        requirement. Named rather than derived because ``id`` has no shared
        grammar: the neutral resolver reads this field, hands it to the
        package's own :class:`IdRule`, and composes an ID no agent spelled.
    """

    proposals: type[ProposalBatch]
    rulings: type[RulingBatch]
    ruled_record: type[RuledClaim]
    block: type[FrameworkAnalysis]
    key_field: str


def _asvs_package() -> FrameworkPackage:
    # Imported inside the function for the reason the STRIDE one is: this module
    # is the contract, and the packages that implement it pull in rules, records
    # and — for ASVS — a 345-requirement catalog.
    from analysis_service.frameworks.asvs import ASVS

    return ASVS


def _asvs_schemas() -> FrameworkSchemas:
    from analysis_service.frameworks.asvs.record import (
        AsvsAnalysis,
        RequirementProposals,
        RequirementRuling,
        RequirementRulings,
    )

    return FrameworkSchemas(
        proposals=RequirementProposals,
        rulings=RequirementRulings,
        ruled_record=RequirementRuling,
        block=AsvsAnalysis,
        key_field="requirement",
    )


def _stride_package() -> FrameworkPackage:
    # Imported inside the function so this module can be imported for the
    # contract alone -- the STRIDE package pulls in its rules, which pull in the
    # structural query layer.
    from analysis_service.frameworks.stride import STRIDE

    return STRIDE


def _stride_schemas() -> FrameworkSchemas:
    from analysis_service.frameworks.stride.record import (
        StrideAnalysis,
        Threat,
        ThreatProposals,
        ThreatRulings,
    )

    return FrameworkSchemas(
        proposals=ThreatProposals,
        rulings=ThreatRulings,
        ruled_record=Threat,
        block=StrideAnalysis,
        key_field="sequence",
    )


#: What this repo carries, keyed by the name it is spelled with. A table, and a
#: table alone: registering a package is an import and an entry here.
PACKAGES: Mapping[FrameworkName, FrameworkPackage] = MappingProxyType(
    {"asvs": _asvs_package(), "stride": _stride_package()}
)

#: The model-facing shapes, keyed the same way and filled in the same edit. See
#: :class:`FrameworkSchemas` for why these sit beside the package rather than on
#: it.
SCHEMAS: Mapping[FrameworkName, FrameworkSchemas] = MappingProxyType(
    {"asvs": _asvs_schemas(), "stride": _stride_schemas()}
)

#: The licence governing each package's *text*, as an SPDX identifier, keyed the
#: same way and filled in the same edit.
#:
#: A package that reproduces a published catalog inherits that catalog's licence,
#: whatever the repo's own licence says; a package whose text is written here
#: carries the repo licence. That is a property of the package rather than a fact
#: about any one framework, so it answers for packages nobody has written yet: an
#: author who copies requirement sentences out of a standard must say so here,
#: and ``tests/test_framework_neutrality.py`` fails until the entry exists and
#: ``NOTICE`` names it.
#:
#: This is a table for the reason every other framework table is one. A default
#: of ``"Apache-2.0"`` would be silently right for STRIDE and silently wrong for
#: the next package that quotes a standard, and nothing would raise.
CONTENT_LICENSE: Mapping[FrameworkName, str] = MappingProxyType(
    {"asvs": "CC-BY-SA-4.0", "stride": "Apache-2.0"}
)


def block_type_for(name: object) -> type[FrameworkAnalysis] | None:
    """The analysis block type this framework's output validates as.

    ``None`` for a name no registered package carries, which is what lets the
    envelope fall back to the neutral base rather than raise on a payload
    written by a build that carried a framework this one does not. The parameter
    is ``object`` rather than :data:`FrameworkName` for the same reason: the
    callers are the two validators, which are reading a name off an unvalidated
    payload and asking exactly this question about it.
    """
    for registered, schemas in SCHEMAS.items():
        if registered == name:
            return schemas.block
    return None


def schemas_for(name: FrameworkName) -> FrameworkSchemas:
    """The model-facing shapes registered under this name.

    A ``KeyError`` here is a defect for the reason :func:`package_for` gives:
    both tables are filled in one edit, and every name that reaches the graph
    has already passed :func:`validate_packages`.
    """
    return SCHEMAS[name]


def package_for(name: FrameworkName) -> FrameworkPackage:
    """The package registered under this name.

    A ``KeyError`` here is a defect rather than a configuration problem: an
    unknown name is refused on the input ladder, before a job record exists, and
    a name in ``config/frameworks.toml`` that this repo does not carry is
    refused by :func:`validate_package` before anything binds.
    """
    return PACKAGES[name]


def widest_fan_out() -> int:
    """Lane agents one job fires at once when it names every carried framework.

    **The burst a provider quota actually sees**, and the number every bound in
    ``config/resilience.toml`` is sized against: one ``strong``-tier request per
    lane of every framework a job selects, all fired together at the barrier.

    Derived rather than written down, because it was written down once and went
    wrong. Six was the whole fan-out while STRIDE was the only package, and
    stayed in the prose behind the concurrency ceiling, the retry budget and the
    jitter policy after ASVS made it 23
    ([#199](https://github.com/mstarks01/work-agent/issues/199) fixed the
    ceiling's own comment and not the four modules reasoning from the same
    number). A function over ``PACKAGES`` cannot go stale that way: a package
    registered tomorrow moves it with no edit anywhere.

    Every framework, not the widest single one, because a job may name them all
    and the ceiling has to hold for the job a caller is allowed to submit.
    """
    return sum(len(package.lanes) for package in PACKAGES.values())


def selectable_without_options(
    names: Sequence[FrameworkName],
) -> tuple[FrameworkName, ...]:
    """Those of ``names`` a caller with nobody to ask can select, in order.

    **A package whose options carry a required field is left out.** No package
    field carries a default, so there is no value to fall back on and no honest
    way to invent one: an ASVS level is a choice an organization makes, and a
    caller that supplied one on the operator's behalf would put a decision in the
    report that nobody made.

    One caller asks this, and it is unattended by construction: the **Provider
    Smoke** run, which asks whether the application works here and has nobody
    to put the question to. It states what it left out rather than hiding it.

    An attended caller has somebody to ask, so it asks rather than calling this:
    the first-run app offers every carried framework and a control for each
    required option, and refuses a submission that leaves one out.
    """
    return tuple(
        name
        for name in names
        if not any(
            field.is_required()
            for field in PACKAGES[name].options.model_fields.values()
        )
    )


def run_precondition(
    package: FrameworkPackage, model: SystemModel
) -> PreconditionResult:
    """One package's precondition over one **Valid System Model**, checked.

    **The check cannot run at declaration time.** A precondition reads a model,
    so nothing knows what it returns until a model exists;
    :func:`validate_package` checks only that the member is callable. Probing it
    at construction with a synthetic model would run package code at startup and
    prove nothing about a real input.

    So this validates what it receives, and fails closed both ways. An
    unrecognised return names the package and the value it gave. An exception out
    of package code is wrapped, because an unwrapped one says nothing about whose
    code it came from.

    The honest cost: this runs after extraction, so a defective package costs one
    extraction. That is the right side to be wrong on — the check exists for a
    build defect that should never reach a deployment, and the alternative loses
    a framework's whole output quietly.

    Every caller runs it through here: the graph's run-time gate, and the eval
    corpus verifier that checks a case's declaration against the predicate.
    """
    try:
        result = package.precondition(model)
    except Exception as exc:
        raise PreconditionError(
            f"framework package {package.name!r} precondition raised {exc!r}"
        ) from exc
    if result not in PRECONDITION_RESULTS:
        raise PreconditionError(
            f"framework package {package.name!r} precondition returned {result!r},"
            f" which is not one of {list(PRECONDITION_RESULTS)}"
        )
    return result


def validate_package(package: FrameworkPackage, root: Path) -> None:
    """Refuse an ill-formed package, before any model call.

    Three families of checks, run for every name a **Deployment** carries.
    Family A is the declaration — including that this package's entry in
    :data:`SCHEMAS` describes the record it declares — family B is what must
    exist on disk, and family C is whether this deployment can actually run it —
    that last one needs the tier config and so is checked by the caller, which
    holds it.

    **Why this is not only a CI lint.** A deployment redirects
    ``ANALYSIS_FRAMEWORKS_DIR``, so a lint over this repo's tree says nothing
    about that install. The lints stay, because a pull request is a cheaper
    place to learn than a deploy — and the split is that *the gate checks what
    the code reads, CI checks what the budget allows*: heading order is a
    correctness rule and is here, while token caps are budgets and stay in CI.
    """
    issues = [
        *_declaration_issues(package),
        *_disk_issues(package, root),
    ]
    if issues:
        raise FrameworkPackageError(
            f"framework package {package.name!r} is not well-formed: "
            + "; ".join(issues)
        )


def _declaration_issues(package: FrameworkPackage) -> list[str]:
    """Family A: the declaration is well-formed, on its own terms."""
    issues: list[str] = []
    if not package.version.strip():
        issues.append("version is empty")
    if not package.lanes:
        issues.append("lanes is empty")
    duplicates = sorted(
        {lane for lane in package.lanes if package.lanes.count(lane) > 1}
    )
    if duplicates:
        issues.append(f"lanes repeats {', '.join(duplicates)}")
    malformed = sorted(
        lane
        for lane in package.lanes
        if not lane or not lane.replace("-", "").isalnum()
    )
    if malformed:
        issues.append(f"lanes are not slugs: {', '.join(map(repr, malformed))}")

    declared = set(package.lanes)
    # Under packages a rule's lane is a plain string, so this check replaces the
    # typed field that used to make a mis-filed rule unrepresentable.
    stray = sorted({rule.lane for rule in package.rules} - declared)
    if stray:
        issues.append(f"rules name undeclared lanes: {', '.join(stray)}")
    missing_prefix = sorted(declared - set(package.id_rule.prefix))
    if missing_prefix:
        issues.append(f"id_prefix omits lanes: {', '.join(missing_prefix)}")

    if not issubclass(package.record, Claim):
        issues.append(f"record {package.record.__name__} does not subclass Claim")
    # The narrow half of the precondition's two checks, and the value is stated
    # rather than oversold: the member is typed and mypy covers the
    # construction. It earns its place on the gate's own terms — the gate
    # re-checks ``record`` with ``issubclass`` under the same reasoning, because
    # its job is to refuse an ill-formed package before any model call rather
    # than to trust the type checker. What the member *returns* cannot be
    # checked here at all; :func:`run_precondition` says why.
    if not callable(package.precondition):
        issues.append(
            f"precondition is {package.precondition!r}, which is not callable,"
            " so nothing can ask this framework whether it applies"
        )
    lane_field = package.id_rule.lane_field
    if lane_field and lane_field not in package.record.model_fields:
        issues.append(
            f"id_rule stamps the lane into {lane_field!r}, which"
            f" {package.record.__name__} does not declare"
        )
    defaulted = sorted(
        name
        for name, field in package.options.model_fields.items()
        if not field.is_required()
    )
    if defaulted:
        issues.append(
            f"options fields carry defaults: {', '.join(defaulted)};"
            " a package field with a default makes one submission mean two"
            " things on two installs"
        )

    issues += _id_format_issues(package)
    issues += _knowledge_issues(package)
    issues += _schema_issues(package)
    return issues


def _batch_element(batch: type[BaseModel]) -> type[BaseModel] | None:
    """The element type inside a proposal or ruling wrapper, or ``None``.

    The wrapper's single ``claims`` field is declared ``list[<element>]``, so the
    element is the annotation's one argument. ``None`` where it is not shaped
    that way at all, which is a finding rather than something to guess through.
    """
    field = batch.model_fields.get("claims")
    if field is None:
        return None
    args = get_args(field.annotation)
    if len(args) != 1 or not isinstance(args[0], type):
        return None
    return args[0] if issubclass(args[0], BaseModel) else None


def _schema_issues(package: FrameworkPackage) -> list[str]:
    """The registration's two halves agree with each other.

    :data:`PACKAGES` and :data:`SCHEMAS` are filled in one edit, and this is what
    holds that true: a package registered without its shapes, or with shapes
    describing a different record, would fail at the first model call with a
    ``KeyError`` naming nothing a maintainer could act on.

    The ``key_field`` rules are the interesting pair, and they are the resolver's
    own contract read back. It must be a field the *agent* fills, so it is on the
    proposal; it must not be a field the *claim* carries, because the service
    composes the ID from it and a record spelling the key too would give an agent
    a second place to put a value the service already holds.
    """
    schemas = SCHEMAS.get(package.name)
    if schemas is None:
        return [
            (
                "no schemas are registered for this package; PACKAGES and"
                " SCHEMAS are filled in one edit"
            )
        ]

    issues: list[str] = []
    proposal = _batch_element(schemas.proposals)
    if proposal is None:
        issues.append(
            f"{schemas.proposals.__name__} does not declare claims as a list of"
            " one proposal type"
        )
    elif schemas.key_field not in proposal.model_fields:
        issues.append(
            f"key_field {schemas.key_field!r} is not a field of"
            f" {proposal.__name__}, so no agent supplies it"
        )
    if schemas.key_field in package.record.model_fields:
        issues.append(
            f"key_field {schemas.key_field!r} is also a field of"
            f" {package.record.__name__}; the service composes the ID from the"
            " key, so a claim carrying it would restate what it was built from"
        )
    if _batch_element(schemas.rulings) is None:
        issues.append(
            f"{schemas.rulings.__name__} does not declare claims as a list of"
            " one ruling type"
        )
    if not issubclass(schemas.ruled_record, package.record):
        issues.append(
            f"ruled_record {schemas.ruled_record.__name__} does not subclass this"
            f" package's own record {package.record.__name__}"
        )
    if not issubclass(schemas.ruled_record, RuledClaim):
        issues.append(
            f"ruled_record {schemas.ruled_record.__name__} does not subclass"
            " RuledClaim, so it carries no verdict"
        )
    if not issubclass(schemas.block, FrameworkAnalysis):
        issues.append(
            f"block {schemas.block.__name__} does not subclass FrameworkAnalysis"
        )
    return issues


def _id_format_issues(package: FrameworkPackage) -> list[str]:
    """The ID template renders with specimen values and yields something."""
    for lane in package.lanes:
        if lane not in package.id_rule.prefix:
            continue  # already reported
        try:
            rendered = package.compose_id(lane, 1)
        except (KeyError, IndexError, ValueError) as exc:
            return [
                f"id_rule template {package.id_rule.template!r} does not render: {exc}"
            ]
        if not rendered:
            return [
                (
                    f"id_rule template {package.id_rule.template!r} renders"
                    f" empty for lane {lane!r}"
                )
            ]
    return []


def _knowledge_issues(package: FrameworkPackage) -> list[str]:
    """Every rule the corpus tables name is a rule this package declares."""
    known = {rule.rule_id for rule in package.rules}
    unknown = sorted(package.knowledge.rule_ids() - known)
    if unknown:
        return [
            "knowledge tables name rules this package does not declare: "
            + ", ".join(unknown)
        ]
    return []


def _readable(path: Path) -> bool:
    """The loader's question, asked by the gate that runs before it.

    `is_file()` follows a symlink out of the package root; `MarkdownLoader.load`
    resolves and refuses one. So a symlinked lane skill passed startup
    validation and failed on the first job of that selection. One rule, and the
    loader owns it.
    """
    return _inside(path.parent, path)


def _disk_issues(package: FrameworkPackage, root: Path) -> list[str]:
    """Family B: every declared thing exists under this package's text root."""
    issues: list[str] = []
    expected: set[Path] = set()

    for lane in package.lanes:
        for doc in ("skill", "exemplars"):
            path = root / "lanes" / lane / f"{doc}.md"
            expected.add(path)
            if not _readable(path):
                issues.append(f"lane {lane!r} has no {doc}.md")
            elif doc == "skill":
                issues += _heading_issues(lane, path)

    for doc in (CRITIC_DOC, DISCLAIMER_DOC, OUTPUT_DOC):
        path = root / f"{doc}.md"
        expected.add(path)
        if not _readable(path):
            issues.append(f"the package carries no {doc}.md")

    rubric = root / f"{SEVERITY_RUBRIC_DOC}.md"
    if package.carries_severity():
        expected.add(rubric)
        if not _readable(rubric):
            issues.append(
                "the record carries a severity field but the package carries no"
                f" {SEVERITY_RUBRIC_DOC}.md"
            )
    elif _readable(rubric):
        issues.append(
            f"the package carries {SEVERITY_RUBRIC_DOC}.md but its record grades"
            " nothing, so nothing would read it"
        )

    for entry in package.knowledge.documents():
        path = root / f"{entry}.md"
        expected.add(path)
        if not _readable(path):
            issues.append(f"the knowledge tables name {entry}, which is not on disk")

    # Both directions, as the corpus lints already check: unread Markdown under
    # a package root is either a document nobody selected or a file someone
    # meant to declare, and neither should ship quietly.
    if root.is_dir():
        unread = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*.md")
            if path not in expected
        )
        if unread:
            issues.append(
                f"unread Markdown under the package root: {', '.join(unread)}"
            )
    else:
        issues.append(f"the package text root {root} does not exist")

    return issues


def _heading_issues(lane: str, path: Path) -> list[str]:
    """A lane skill carries the five fixed H2 headings, in order.

    A correctness rule rather than a style one: the critic's lane digest is
    extracted from ``## Scope``, so a lane whose headings drifted silently stops
    contributing to the digest its own framework's critic dedupes against.
    """
    found = [
        line[3:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    if found[: len(LANE_SECTION_HEADINGS)] != list(LANE_SECTION_HEADINGS):
        return [
            (
                f"lane {lane!r} skill.md headings are {found!r}, expected"
                f" {list(LANE_SECTION_HEADINGS)!r} in order"
            )
        ]
    return []


def validate_packages(
    names: Sequence[FrameworkName], root: Path, tier_nodes: Sequence[str]
) -> None:
    """Every carried package: registered, well-formed, and runnable here.

    Check 1 of family A and the whole of family C, which need the carried set
    and the tier config rather than one package. Called by
    :meth:`~analysis_service.deployment.Deployment.from_env`.
    """
    unknown = [name for name in names if name not in PACKAGES]
    if unknown:
        raise FrameworkPackageError(
            f"config names frameworks this build does not carry: {unknown};"
            f" it carries {sorted(PACKAGES)}"
        )
    tiers = set(tier_nodes)
    for name in names:
        package = PACKAGES[name]
        validate_package(package, root / name)
        missing = [
            key
            for key in (f"analyze/{name}", f"critic/{name}", f"recritic/{name}")
            if key not in tiers
        ]
        if missing:
            raise FrameworkPackageError(
                f"model_tiers.toml carries no {', '.join(missing)} for framework"
                f" {name!r}"
            )
