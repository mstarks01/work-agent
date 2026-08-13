"""Framework packages: what a security framework declares, and what checks it.

**A package declares what its framework judges and what selects its text. The
service keeps what it constructs and what it checks.** That one line decided
every row of this contract. Building a **Ground** from an agent's selection is
construction, so it stays service code; deciding which lane sees a lead is
judgement about a framework's own method, so the **Candidate** rules moved into
the package. The **Evidence Catalog** enumerates the **Valid System Model** and
not the framework, so it stayed.

A package is **a catalog it does not own plus a profile it does**. Every member
below is profile — the tailoring this service applies. No member names a
catalog: a framework that publishes a machine-readable requirement set carries
it as its own private data and checks it in its own module, because a contract
that *required* one would exclude the frameworks that publish none.

**Registration is a table edit and an import**, exactly like the vendor
registry. There is no entry point, no import by name from config, and no path in
a config file: a package writes into a ``strong``-tier prompt, and external
plugin loading is out of scope for this architecture.

Three sets must agree. :data:`~stride_service.report.FrameworkName` names what
this repo can spell, :data:`PACKAGES` names what this repo carries, and
``config/frameworks.toml`` names what this install runs. The first two agree at
import; the third agrees at :func:`validate_package`, which a **Deployment**
runs **before an adapter binds and before an instruction composes**.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel

from stride_service.candidates import Rule
from stride_service.errors import ConfigError
from stride_service.report import Claim, FrameworkName
from stride_service.system_model import SystemModel

__all__ = [
    "PACKAGES",
    "FrameworkPackage",
    "FrameworkPackageError",
    "KnowledgeTables",
    "PreconditionResult",
    "package_for",
    "validate_package",
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
#: Per package: ``critic.md``, ``disclaimer.md``, and ``severity_rubric.md``
#: exactly when the record carries a ``severity`` field.
CRITIC_DOC = "critic"
DISCLAIMER_DOC = "disclaimer"
SEVERITY_RUBRIC_DOC = "severity_rubric"


class FrameworkPackageError(ConfigError):
    """A package this deployment carries is ill-formed or incomplete on disk.

    A ``ConfigError`` rather than anything softer, because this fails a
    **Deployment**'s construction: a package that declares a lane with no prompt
    must not reach a model call, and the only way to guarantee that is to refuse
    to start.
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
    """

    template: str
    prefix: Mapping[str, str]
    lane_field: str | None

    def compose(self, lane: str, key: object) -> str:
        return self.template.format(prefix=self.prefix[lane], key=key)


@dataclass(frozen=True)
class FrameworkPackage:
    """One security framework as an object the service can run.

    Nine members, plus text under one root by convention.

    ``name``
        The closed :data:`~stride_service.report.FrameworkName`. A package
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


def _stride_package() -> FrameworkPackage:
    # Imported inside the function so this module can be imported for the
    # contract alone -- the STRIDE package pulls in its rules, which pull in the
    # structural query layer.
    from stride_service.frameworks.stride import STRIDE

    return STRIDE


def _stride_block() -> type:
    from stride_service.frameworks.stride.record import StrideAnalysis

    return StrideAnalysis


#: What this repo carries, keyed by the name it is spelled with. A table, and a
#: table alone: registering a package is an import and an entry here.
PACKAGES: Mapping[FrameworkName, FrameworkPackage] = MappingProxyType(
    {"stride": _stride_package()}
)

#: How each package's analysis block is shaped in the report. Beside
#: :data:`PACKAGES` and filled in the same edit, because it is the same
#: registration: a package narrows ``claims`` to its own record type and its
#: summary to whatever its record can be counted by, and the envelope dispatches
#: on ``framework`` to read the right one back.
BLOCK_TYPES: Mapping[FrameworkName, type] = MappingProxyType(
    {"stride": _stride_block()}
)


def block_type_for(name: str) -> type | None:
    """The analysis block type this framework's output validates as.

    ``None`` for a name no registered package carries, which is what lets the
    envelope fall back to the neutral base rather than raise on a payload
    written by a build that carried a framework this one does not.
    """
    return BLOCK_TYPES.get(name)


def package_for(name: FrameworkName) -> FrameworkPackage:
    """The package registered under this name.

    A ``KeyError`` here is a defect rather than a configuration problem: an
    unknown name is refused on the input ladder, before a job record exists, and
    a name in ``config/frameworks.toml`` that this repo does not carry is
    refused by :func:`validate_package` before anything binds.
    """
    return PACKAGES[name]


def validate_package(package: FrameworkPackage, root: Path) -> None:
    """Refuse an ill-formed package, before any model call.

    Twelve checks in three families, run for every name a **Deployment**
    carries. Family A is the declaration, family B is what must exist on disk,
    and family C is whether this deployment can actually run it — that last one
    needs the tier config and so is checked by the caller, which holds it.

    **Why this is not only a CI lint.** A deployment redirects
    ``STRIDE_FRAMEWORKS_DIR``, so a lint over this repo's tree says nothing
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


def _disk_issues(package: FrameworkPackage, root: Path) -> list[str]:
    """Family B: every declared thing exists under this package's text root."""
    issues: list[str] = []
    expected: set[Path] = set()

    for lane in package.lanes:
        for doc in ("skill", "exemplars"):
            path = root / "lanes" / lane / f"{doc}.md"
            expected.add(path)
            if not path.is_file():
                issues.append(f"lane {lane!r} has no {doc}.md")
            elif doc == "skill":
                issues += _heading_issues(lane, path)

    for doc in (CRITIC_DOC, DISCLAIMER_DOC):
        path = root / f"{doc}.md"
        expected.add(path)
        if not path.is_file():
            issues.append(f"the package carries no {doc}.md")

    rubric = root / f"{SEVERITY_RUBRIC_DOC}.md"
    if package.carries_severity():
        expected.add(rubric)
        if not rubric.is_file():
            issues.append(
                "the record carries a severity field but the package carries no"
                f" {SEVERITY_RUBRIC_DOC}.md"
            )
    elif rubric.is_file():
        issues.append(
            f"the package carries {SEVERITY_RUBRIC_DOC}.md but its record grades"
            " nothing, so nothing would read it"
        )

    for entry in package.knowledge.documents():
        path = root / f"{entry}.md"
        expected.add(path)
        if not path.is_file():
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
    :meth:`~stride_service.deployment.Deployment.from_env`.
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
