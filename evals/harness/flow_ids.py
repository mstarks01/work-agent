"""Move a corpus case's **Data Flow** IDs to a newer flow identity version (#989).

ADR 0037 changes what a flow ID is built from: version 1 dropped the endpoints'
type prefixes, so an entity and a process sharing one name derived one flow ID.
Version 2 carries both endpoints' full **Element ID**s. Every blessed model,
reference claim file, reference fact and case alias spells flow IDs, so the
derivation change is a schema change and this is its migration.

## The mapping comes from the graph, never from the recorded ID

Rule 5 of the ADR, and it is the whole reason this module reads ``model.json``
rather than walking strings: an old flow ID cannot say what types its endpoints
had, because that is exactly the fact a version 1 ID omits. So
:func:`mapping_for` re-derives each flow's *old* ID from the endpoints the graph
records, checks it against the ID the graph carries, and computes the new ID
from the same endpoints.

Four conditions stop a case rather than being guessed at, and each is a
different kind of wrong:

* An endpoint names no element in the graph. The flow's identity cannot be
  computed under either rule.
* Two flows already share an ID under the version being left. A reference to
  that ID names neither of them, so nothing can say which flow it meant.
* A flow's recorded ID is not what its own version derives from its endpoints
  and label. The graph is not self-consistent, so no mapping over it is either.
* Two flows land on one ID under the new rule. That is the duplicate the gate
  refuses, and a migration that merged them silently is what ADR 0037 rule 3
  forbids.

## What a rename reaches, and why it is a text edit

Every dependent reference is a string somewhere in a case's JSON, and the sites
are of two kinds. A field holds an ID alone — a claim's
``affected_element_ids``, an alias's ``element``, a fact's ``subject`` — or it
holds the ID inside something longer: a claim's ``notes``, a rationale, an
evidence reference such as ``crossing:flow:...``. :func:`rewrite_text` is the
one reader of "where does an old ID occur", so no caller has to know which
fields are which.

It edits the file's **text** rather than re-serializing its JSON. The corpus is
not uniformly spelled — some files escape non-ASCII and some carry it raw — so a
round trip through ``json.dumps`` would rewrite bytes the migration never
touched, and every re-spelled file would read as a corpus edit. A flow ID is
ASCII and needs no JSON escape, so the text edit and the structural edit are the
same edit here. Each written file is parsed before it lands, so a substitution
that broke the JSON fails closed instead of shipping.

## Votes

Rule 6. A vote stores its :class:`~evals.harness.fingerprint.Components`, and
``targets`` arrives endpoint-resolved, so a flow a case records is already
spelled as its two endpoints and a rename does not move it. A flow ID survives
into ``targets`` only where the claim cited a flow the case's map does not hold.
:func:`migrate_votes` maps those through the rename table and recomputes the
fingerprint; a target the mapping cannot answer one-to-one is **marked for
review** and its row is left where it is, rather than carried across a change
whose meaning nobody has checked.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from analysis_service.system_model import (
    FLOW_ID_VERSION,
    FlowIdError,
    flow_id_rule,
    flow_id_version,
)
from evals.harness import ledger
from evals.harness.fingerprint import FingerprintError, fingerprint, version_for
from evals.harness.provenance import REPO_ROOT

CORPUS_DIR = REPO_ROOT / "evals" / "corpus"
VOTES_DIR = REPO_ROOT / "evals" / "review" / "votes"

#: Which collections of a ``model.json`` hold the elements a flow's endpoints
#: may name. Read off the file's own keys rather than the schema, because this
#: reads a graph as recorded and must keep working on one the current schema
#: would refuse — a half-migrated case is exactly when this runs.
_ENDPOINT_COLLECTIONS = ("external_entities", "processes", "data_stores")

#: The files inside one case directory whose strings a rename reaches. A path
#: that matches nothing is not an error: a case declaring one framework has no
#: claim file for the other, and a case with no signed facts has no
#: ``facts.json``.
#:
#: ``corrections.md`` and the hand-written reading document are here beside the
#: JSON because they cite flow IDs in prose, and a citation nobody can resolve
#: is as wrong in a reading document as in a claim. ``REVIEW.md`` is **not**:
#: a generator owns it, and :data:`REGENERATED` names the command instead.
CASE_FILES = (
    "model.json",
    "case.json",
    "facts.json",
    "claims/*.json",
    "corrections.md",
    "REVIEW-*.md",
)

#: Every file outside a case directory that spells a case's flow IDs, relative
#: to the repository root. A rename reaches them under the **union** of every
#: case's table, which is sound only while that union is one-to-one —
#: :func:`union_of` refuses otherwise rather than rewriting one case's reference
#: with another case's ID.
#:
#: Declared rather than discovered. A path added here is a reviewed statement
#: that the file cites corpus element IDs; a search for the ID shape would also
#: find the archived reports, which ADR 0037 says keep their IDs and their
#: version.
DEPENDENT_FILES = (
    "evals/calibration_labels/build_pairs.py",
    "evals/critic_review/cases.json",
)

#: Files a generator owns, against the command that rewrites them. The
#: migration prints these rather than editing them: a generated file edited by
#: hand disagrees with its generator the next time anybody runs it, and the
#: corpus lints fail on exactly that.
REGENERATED = (
    ("evals/calibration_labels/pairs.json", "evals/calibration_labels/build_pairs.py"),
    ("evals/corpus/*/REVIEW.md", "evals/build_review_docs.py"),
)


class MigrationError(Exception):
    """A case cannot be migrated, and the message says which condition stopped it."""


@dataclass(frozen=True)
class FlowRenames:
    """One case's old-to-new flow ID mapping, with what stopped it.

    ``renames`` holds only the IDs that move. A flow whose ID is unchanged is
    absent rather than mapped to itself, so a caller counting the mapping counts
    real changes. ``flags`` is non-empty exactly when the mapping must not be
    applied: :func:`migrate_case` refuses on it rather than writing a partial
    rename, which is the state nobody can recover from.
    """

    case: str
    from_version: int
    to_version: int
    renames: Mapping[str, str]
    flags: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.flags


def _elements_by_id(raw: Mapping[str, Any]) -> dict[str, str]:
    """Every endpoint-eligible element's ID, against the collection it sits in."""
    found: dict[str, str] = {}
    for collection in _ENDPOINT_COLLECTIONS:
        for element in raw.get(collection) or ():
            if isinstance(element, Mapping) and isinstance(element.get("id"), str):
                found[element["id"]] = collection
    return found


def flow_id_version_of_graph(raw: Mapping[str, Any]) -> int:
    """Which flow identity version one recorded graph's IDs are at.

    Read off the IDs by shape, and a graph whose flows disagree raises: a
    mixture is a half-migrated tree, which is the state a lift must not paper
    over and a migration must fail on as a whole. A graph with no flows is at
    the version this code writes, because there is nothing in it that another
    version would spell differently.
    """
    versions = {
        flow_id_version(flow["id"])
        for flow in raw.get("data_flows") or ()
        if isinstance(flow.get("id"), str)
    }
    if len(versions) > 1:
        raise MigrationError(
            f"this graph's flow IDs sit at versions {sorted(versions)}; a"
            " mixture names no one rule, so nothing here reads it"
        )
    return versions.pop() if versions else FLOW_ID_VERSION


def table_between(
    raw: Mapping[str, Any], from_version: int, to_version: int = FLOW_ID_VERSION
) -> dict[str, str]:
    """Each flow's ID under one version against its ID under another.

    Derived from the endpoints and the label the graph records, so it never asks
    which version the graph's *own* IDs are at. That is what makes it the lift
    for an archived artifact read against a graph at any version: an archived
    assertion proposal names a flow the way the version it ran under spelled it,
    and this says what the same flow is called now.

    Only the entries that differ, and only the flows whose endpoints the graph
    resolves. It carries none of the checks :func:`mapping_for` makes, because a
    lift reads one artifact's references while a migration rewrites the graph
    itself, and only the second can be half-done.
    """
    old_rule, new_rule = flow_id_rule(from_version), flow_id_rule(to_version)
    known = _elements_by_id(raw)
    table: dict[str, str] = {}
    for flow in raw.get("data_flows") or ():
        source, destination, name = (
            flow.get("source"),
            flow.get("destination"),
            flow.get("name"),
        )
        if not all(isinstance(part, str) for part in (source, destination, name)):
            continue
        if source not in known or destination not in known:
            continue
        try:
            before = old_rule.build(source, destination, name)
            after = new_rule.build(source, destination, name)
        except (ValueError, FlowIdError):
            continue
        if before != after:
            table[before] = after
    return table


def lift_value(value: Any, table: Mapping[str, str]) -> Any:
    """One JSON value with every old flow ID inside it replaced.

    The in-memory counterpart of :func:`rewrite_text`, over the same bounded
    match: an archived emission is a parsed payload rather than a file the
    migration owns. Dict keys move too, because a table keyed by element ID is
    how an artifact records per-element data.
    """
    if isinstance(value, str):
        return rewrite_text(value, table)
    if isinstance(value, Mapping):
        return {
            lift_value(key, table): lift_value(item, table)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [lift_value(item, table) for item in value]
    return value


def mapping_for(
    case: str,
    raw: Mapping[str, Any],
    from_version: int,
    to_version: int = FLOW_ID_VERSION,
) -> FlowRenames:
    """The old-to-new flow ID mapping one recorded graph implies.

    Computed from the endpoints the graph records, which is the only place the
    endpoint types survive. Every condition this cannot decide lands in
    :attr:`FlowRenames.flags` rather than in a guess.
    """
    old_rule = flow_id_rule(from_version)
    new_rule = flow_id_rule(to_version)
    known = _elements_by_id(raw)
    renames: dict[str, str] = {}
    flags: list[str] = []
    old_ids: dict[str, str] = {}
    new_ids: dict[str, str] = {}

    for flow in raw.get("data_flows") or ():
        recorded = flow.get("id")
        source, destination, name = (
            flow.get("source"),
            flow.get("destination"),
            flow.get("name"),
        )
        if not all(
            isinstance(part, str) for part in (recorded, source, destination, name)
        ):
            flags.append(
                f"a data flow entry ({recorded!r}) is missing an id, an endpoint"
                " or a name"
            )
            continue
        missing = [end for end in (source, destination) if end not in known]
        if missing:
            flags.append(
                f"{recorded}: endpoint {', '.join(missing)} names no element in"
                " the graph, so the flow has an identity under neither rule"
            )
            continue
        try:
            derived_old = old_rule.build(source, destination, name)
            derived_new = new_rule.build(source, destination, name)
        except (ValueError, FlowIdError) as exc:
            flags.append(f"{recorded}: {exc}")
            continue
        if derived_old != recorded:
            flags.append(
                f"{recorded}: version {from_version} derives"
                f" {derived_old!r} from this flow's own endpoints and label, so"
                " the graph does not derive its own IDs and no mapping over it"
                " would either"
            )
            continue
        if recorded in old_ids:
            flags.append(
                f"{recorded}: two flows already share this ID under version"
                f" {from_version}, so a reference to it names neither"
            )
            continue
        if derived_new in new_ids:
            flags.append(
                f"{derived_new}: two flows land on this ID under version"
                f" {to_version} — {old_ids[new_ids[derived_new]]!r} is the other."
                " ADR 0037 rule 3 leaves that duplicate to the gate; nothing"
                " here merges them"
            )
            continue
        old_ids[recorded] = recorded
        new_ids[derived_new] = recorded
        if derived_new != recorded:
            renames[recorded] = derived_new

    return FlowRenames(
        case=case,
        from_version=from_version,
        to_version=to_version,
        renames=renames,
        flags=tuple(flags),
    )


def _bounded(old: str) -> re.Pattern[str]:
    """``old`` where the character after it cannot extend a slug.

    Every element ID ends in a slug, so one ID is routinely a prefix of
    another: ``flow:a-to-b:read`` of ``flow:a-to-b:read-more``, and
    ``entity:shopper`` of ``entity:shopper-group``. Without the boundary a
    rename of the first corrupts the second into a mixture of both, and the
    corpus holds label pairs exactly that shape (``read-orders`` beside
    ``read-write-orders``).

    The rule is a flow's by origin and every ID's by use: a version 2 flow ID
    carries its endpoints, so renaming an endpoint moves every flow through it,
    and ``tests/test_metamorphic.py`` drives this over element renames for that
    reason.

    **Bounded on both sides.** The left guard has nothing to catch today: no
    prefix in use ends with another, so no ID is a *suffix* of a longer one.
    It is here because ``_ID_PREFIX`` admits ``[a-z_]+``, which a prefix ending
    in an existing one would satisfy — and a fence sized to one side is safe
    only while its neighbours are fenced too. The intended sites still match:
    an ID inside ``crossing:flow:...`` or a JSON string is preceded by ``:``
    or ``"``, neither of which is a slug character.
    """
    return re.compile(r"(?<![a-z0-9_-])" + re.escape(old) + r"(?![a-z0-9-])")


def rewrite_text(text: str, renames: Mapping[str, str]) -> str:
    """Every old flow ID occurring in ``text`` replaced by its new one.

    The one reader of "where does an old ID occur". A field holding an ID alone
    and a sentence mentioning one are the same substitution, so nothing here
    holds a list of which fields are which — a list that would go stale the day
    a case gains a field.

    Longest old ID first, so a table holding one ID and a longer ID starting
    with it cannot have the short one applied inside the long one. The
    replacement goes through :func:`_constant`, never a template.
    """
    for old, new in sorted(renames.items(), key=lambda pair: -len(pair[0])):
        text = _bounded(old).sub(_constant(new), text)
    return text


def _constant(value: str) -> Callable[[re.Match[str]], str]:
    """``value``, as the replacement function :meth:`re.Pattern.sub` takes.

    A function rather than a template string, so a character ``re`` reads in a
    template — a backslash, a group reference — cannot be interpreted. Bound
    here rather than in a closure over the loop variable, which is the defect
    the ``B023`` lint names.
    """
    return lambda _match: value


def case_paths(case_dir: Path) -> list[Path]:
    """Every file in one case directory a rename reaches, in a stable order."""
    found: list[Path] = []
    for pattern in CASE_FILES:
        found.extend(sorted(case_dir.glob(pattern)))
    return found


def union_of(tables: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """Every case's table as one, or raise on an old ID two cases spell apart.

    A file outside a case directory cites element IDs without saying which case
    they belong to, so it can only be rewritten under a union. An **Element ID**
    is unique inside one **System Model** and says nothing across two — the
    argument that gave the claim fingerprint its ``scope`` — so the union is
    sound only where it is one-to-one, and this refuses rather than rewriting
    one case's reference with another case's ID.
    """
    union: dict[str, str] = {}
    origin: dict[str, str] = {}
    for case, table in sorted(tables.items()):
        for old, new in table.items():
            if union.get(old, new) != new:
                raise MigrationError(
                    f"{old} maps to {union[old]} in {origin[old]} and to {new} in"
                    f" {case}; no file outside a case directory can be rewritten"
                    " under a mapping that is not one-to-one"
                )
            union[old] = new
            origin[old] = case
    return union


def migrate_dependents(
    root: Path, tables: Mapping[str, Mapping[str, str]]
) -> dict[Path, str]:
    """The new text of every :data:`DEPENDENT_FILES` entry the rename moves.

    A declared path that does not exist is skipped rather than raising: the
    table says which files cite corpus IDs, and a checkout without one of them
    is a smaller tree rather than a broken migration.
    """
    union = union_of(tables)
    written: dict[Path, str] = {}
    for relative in DEPENDENT_FILES:
        path = root / relative
        if not path.exists():
            continue
        before = path.read_text(encoding="utf-8")
        after = rewrite_text(before, union)
        if after == before:
            continue
        if path.suffix == ".json":
            try:
                json.loads(after)
            except json.JSONDecodeError as exc:
                raise MigrationError(
                    f"{path}: the rename left the file unparseable: {exc}"
                ) from exc
        written[path] = after
    return written


def migrate_case(
    case_dir: Path, from_version: int, to_version: int = FLOW_ID_VERSION
) -> tuple[FlowRenames, dict[Path, str]]:
    """One case's mapping, and the new text of every file the rename moves.

    Returns the text rather than writing it, so a caller previews the whole
    corpus before any byte lands. A flagged mapping raises: a case whose
    identity cannot be computed must not be half-rewritten.
    """
    model_path = case_dir / "model.json"
    raw = json.loads(model_path.read_text(encoding="utf-8"))
    renames = mapping_for(case_dir.name, raw, from_version, to_version)
    if not renames.clean:
        raise MigrationError(
            f"{case_dir.name}: the flow identity mapping is not decidable:\n  "
            + "\n  ".join(renames.flags)
        )
    written: dict[Path, str] = {}
    if not renames.renames:
        return renames, written
    for path in case_paths(case_dir):
        before = path.read_text(encoding="utf-8")
        after = rewrite_text(before, renames.renames)
        if after == before:
            continue
        if path.suffix == ".json":
            try:
                json.loads(after)
            except json.JSONDecodeError as exc:
                raise MigrationError(
                    f"{path}: the rename left the file unparseable: {exc}"
                ) from exc
        written[path] = after
    return renames, written


def migrate_votes(
    votes: Iterable[ledger.Vote], renames: Mapping[str, Mapping[str, str]]
) -> tuple[list[ledger.Vote], list[str]]:
    """Rule 6: carry a vote across the rename only where the change is one-to-one.

    A flow ID reaches ``targets`` only where the claim cited a flow its case's
    map does not hold — everything else is already endpoint-resolved, so a
    rename does not move it. Such a target is mapped through its own case's
    table and the fingerprint recomputed from the mapped components, which keeps
    the row's provenance and its reason code untouched.

    Two things are marked for review instead of carried: a target that is
    flow-shaped and absent from its case's table, which is a reference the
    mapping cannot answer, and a row whose case has no table at all. The row is
    returned unchanged so the ledger still loads, and its name is returned
    beside it so a maintainer rules on it.
    """
    carried: list[ledger.Vote] = []
    review: list[str] = []
    for vote in votes:
        table = renames.get(vote.case)
        flows = [
            target for target in vote.components.targets if target.startswith("flow:")
        ]
        if table is None and flows:
            review.append(
                f"{vote.voter}'s vote on {vote.case} ({vote.fingerprint}) cites"
                f" {', '.join(flows)} and that case has no mapping"
            )
            carried.append(vote)
            continue
        unmapped = [target for target in flows if table and target not in table]
        if unmapped:
            review.append(
                f"{vote.voter}'s vote on {vote.case} ({vote.fingerprint}) cites"
                f" {', '.join(unmapped)}, which the case's mapping does not name"
            )
            carried.append(vote)
            continue
        if not flows or table is None:
            carried.append(vote)
            continue
        components = replace(
            vote.components,
            targets=tuple(
                sorted(table.get(target, target) for target in vote.components.targets)
            ),
        )
        try:
            key = fingerprint(components, version=version_for(components.framework))
        except FingerprintError as exc:
            review.append(
                f"{vote.voter}'s vote on {vote.case} ({vote.fingerprint}) cannot"
                f" be re-keyed after the rename: {exc}"
            )
            carried.append(vote)
            continue
        carried.append(replace(vote, components=components, fingerprint=key))
    return carried, review


def arguments(parser: argparse.ArgumentParser) -> None:
    # Passed rather than sniffed. A flow ID's shape is readable, but "which
    # rule wrote this" is a fact about the tree and not about one string: a
    # case whose every flow ID happens to fit both shapes would read as either,
    # and the answer would turn on which case was looked at first.
    parser.add_argument(
        "--from-version",
        required=True,
        type=int,
        help="the flow identity version the corpus on disk was written under",
    )
    parser.add_argument(
        "--to-version",
        default=FLOW_ID_VERSION,
        type=int,
        help=f"the version to write (default {FLOW_ID_VERSION})",
    )
    parser.add_argument(
        "--corpus", default=str(CORPUS_DIR), help="the corpus directory to migrate"
    )
    parser.add_argument(
        "--ledger", default=str(VOTES_DIR), help="the vote ledger directory"
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="the repository root DEPENDENT_FILES are relative to",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="write the migration; without it nothing is written",
    )


def command_migrate_flow_ids(args: argparse.Namespace) -> int:
    """Migrate the corpus and the vote ledger to a newer flow identity version.

    Previews by default, for the reason ``rekey`` and ``promote`` do: this
    rewrites the blessed reference sets, and a preview that also edited would be
    a preview nobody could trust. Every case is mapped before any byte lands, so
    one undecidable case stops the whole migration rather than leaving the corpus
    half-renamed.
    """
    corpus = Path(args.corpus)
    from_version, to_version = args.from_version, args.to_version
    cases = sorted(path for path in corpus.iterdir() if (path / "model.json").exists())
    if not cases:
        print(f"{corpus}: no cases to migrate")
        return 1

    tables: dict[str, Mapping[str, str]] = {}
    writes: dict[Path, str] = {}
    try:
        for case_dir in cases:
            renames, written = migrate_case(case_dir, from_version, to_version)
            tables[case_dir.name] = renames.renames
            writes.update(written)
        writes.update(migrate_dependents(Path(args.root), tables))
    except (MigrationError, FlowIdError) as exc:
        print(f"the migration stops: {exc}")
        return 1

    moved = sum(len(table) for table in tables.values())
    print(
        f"{len(cases)} cases, {moved} flow IDs move from version"
        f" {from_version} to {to_version}, {len(writes)} files change"
    )
    for case, table in sorted(tables.items()):
        for old, new in sorted(table.items()):
            print(f"  {case}: {old} -> {new}")

    votes = ledger.load(Path(args.ledger))
    carried, review = migrate_votes(votes.votes, tables)
    keyed = sum(
        1
        for before, after in zip(votes.votes, carried, strict=True)
        if before.fingerprint != after.fingerprint
    )
    print(f"{len(carried)} votes, {keyed} re-keyed, {len(review)} marked for review")
    for line in review:
        print(f"  review: {line}")

    # Only where something moved: a generator named over an unchanged corpus is
    # an instruction to do nothing.
    if writes:
        for pattern, generator in REGENERATED:
            print(f"  re-run: uv run python {generator}  # rewrites {pattern}")

    if not args.yes:
        print("nothing written; pass --yes to apply")
        return 0
    for path, text in sorted(writes.items()):
        path.write_text(text, encoding="utf-8")
    if keyed:
        ledger.write_all(carried, Path(args.ledger))
    print(f"wrote {len(writes)} files")
    return 0
