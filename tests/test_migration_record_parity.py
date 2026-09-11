"""A migration writes what the record accepts, and reads the record to know it.

A migration is the one script that runs over the archive rather than over a job,
so a value it writes that the model then refuses leaves the tree unreadable by
the commands that exist to read it. Both migrations say they are idempotent and
safe to re-run, which is a promise about the future: the bounds and placeholders
they write have to be the ones the record carries **then**, not the ones
somebody copied into the script when it was written.

``2026-09-10-unreconciled-ruling-kinds.py`` held three copies — the claim-ID
bound, the message bound and ``"(unnamed)"`` — while its sibling imported
``SystemModel`` and ``_file_digests`` from the tree. This holds the script
against :class:`~analysis_service.claims.UnreconciledRuling` itself, so a bound
that moves fails here rather than on the next re-run.

Loaded by path because a migration's filename carries hyphens and dates, which
is right for a script nobody imports and means a test has to say so.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from analysis_service.claims import UnreconciledRuling

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "evals" / "migrations"


def load_migration(name: str) -> ModuleType:
    """One migration script as a module, by the filename it actually has.

    Named ``migration_<stem>`` rather than ``<stem>``: a migration's filename
    opens with a date, and a module whose name opens with a digit is not an
    identifier — ``dataclasses`` looks its owner up in :data:`sys.modules` and
    cannot resolve an annotation without it. Registered there for the same
    reason.
    """
    path = MIGRATIONS / name
    module_name = "migration_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kinds_migration() -> ModuleType:
    return load_migration("2026-09-10-unreconciled-ruling-kinds.py")


#: One archived sentence per pattern the script recognises, in the wording
#: ``review_issues`` and ``merge_retry`` wrote them. A pattern added without a
#: sentence here fails ``test_every_pattern_is_exercised``.
SENTENCES = (
    "critic dropped draft 'S-01'",
    "critic returned claim 'S-02', which no lane agent drafted",
    "claim ID 'S-03' is used by 2 drafts",
    ("re-ask changed ruling 'S-04', which no problem named; the first ruling was kept"),
    (
        "claim 'S-05' is ruled confirmed but its own grounds cite"
        " 'process:api.authentication' as never stated, so it cannot be"
        " confirmed: rule it needs-info, or reject it with a reason"
    ),
    (
        "claim 'S-06' rules on 'V1.2.3', and a duplicate of a unit-bearing"
        " draft is decided by its identifier before you see it, so rejecting"
        " it as a duplicate of another requirement names no check: rule on"
        " this requirement, or reject it with a reason"
    ),
    (
        "claim 'S-07' hangs its needs-info verdict on element 'process:ghost',"
        " which is not in the system model"
    ),
    (
        "claim 'S-08' is ruled needs-info and its related_unknowns entry names"
        " neither an element attribute nor a subject, so nothing says what has"
        " to be answered"
    ),
    (
        "claim 'S-09' is rejected but names no check in rejected_because, so"
        " nothing says which of evidence, reasoning, lane or duplicate ended it"
    ),
)


def test_every_pattern_is_exercised(kinds_migration: ModuleType) -> None:
    """A pattern with no sentence here is a pattern nothing below validates."""
    matched = {
        kind
        for sentence in SENTENCES
        if (record := kinds_migration._typed(sentence)) is not None
        for kind in [record["kind"]]
    }

    assert matched == {kind for _, kind in kinds_migration.PATTERNS}


@pytest.mark.parametrize("sentence", SENTENCES)
def test_what_the_migration_writes_is_what_the_record_accepts(
    kinds_migration: ModuleType, sentence: str
) -> None:
    """The two readers, against each other rather than each against itself."""
    record = kinds_migration._typed(sentence)

    assert record is not None, sentence
    assert UnreconciledRuling.model_validate(record).message == sentence


def test_an_empty_claim_id_takes_the_record_s_own_placeholder(
    kinds_migration: ModuleType,
) -> None:
    """``Ruling.id`` carries no ``min_length``, so the empty string arrives.

    The script spelled ``"(unnamed)"`` itself. A rename of ``UNNAMED_CLAIM``
    would then have made a re-run write a placeholder nothing reads, which is
    worse than a failure because the archive would still load.
    """
    from analysis_service.claims import UNNAMED_CLAIM

    record = kinds_migration._typed("critic dropped draft ''")

    assert record is not None
    assert record["claim_id"] == UNNAMED_CLAIM
    assert UnreconciledRuling.model_validate(record)


def test_a_sentence_at_the_bound_is_cut_where_the_record_cuts(
    kinds_migration: ModuleType,
) -> None:
    """The cut is a fail-safe, so it has to land inside what the field takes."""
    from analysis_service.claims import UNRECONCILED_MESSAGE_MAX_CHARS

    long_id = "S-" + "x" * (UNRECONCILED_MESSAGE_MAX_CHARS + 500)
    record = kinds_migration._typed(f"critic dropped draft '{long_id}'")

    assert record is not None
    assert UnreconciledRuling.model_validate(record)


def test_a_sentence_no_pattern_matches_is_not_rewritten(
    kinds_migration: ModuleType,
) -> None:
    """A kind this cannot name is left alone: a wrong kind outlives a failed load."""
    assert kinds_migration._typed("something nobody has written") is None
