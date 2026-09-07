"""Every package's rules, against the corpus that has to exercise them.

A **Candidate** rule that fires on no corpus case is reading a shape the corpus
does not contain, or a shape the extraction never produces. The two need
different fixes — a case for the first, a rule edit for the second — and this
module does not tell them apart. What it does is make the question visible at
all: every other check in the repo passes a dead rule silently.

``evals/harness/triggers.py`` scores per *reference threat* and so never names a
rule; ``test_knowledge_lints`` checks a rule has reference material, which a
dead rule has too. So this is the one place a rule's own firing is asserted.

The second half asks the mirror question one layer up: which knowledge
**documents** does any lane of any blessed model actually receive? A document
registered against rules that fire can still reach nobody, because selection
ranks by match count and keeps one case and two notes per lane — so a broader
document wins every lane they share and a narrower one is unreachable at full
maintenance cost. The package gate holds that a document exists and its rule IDs
resolve, and neither it nor ``test_knowledge_lints`` asks whether the material
is ever selected.

Deterministic over the blessed ``model.json`` and free of provider calls, which
is why it gates on every PR rather than waiting for a sweep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.candidates import generate_candidates
from analysis_service.frameworks import PACKAGES
from analysis_service.knowledge import MAX_CASES, MAX_NOTES, select_per_lane
from analysis_service.system_model import SystemModel
from analysis_service.validation import parse_and_validate

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"

#: Rules the corpus does not exercise, each with the reason it is acceptable.
#: Every entry here is the *first* reading — the rule is right and no case
#: describes the shape — so every one is a gap in the corpus rather than in the
#: rule. A rule whose predicate cannot match what extraction produces does not
#: belong here; it belongs fixed.
UNEXERCISED: dict[str, str] = {
    "webrtc-real-time-media": (
        "No corpus case carries real-time media. Checked with synonyms wider"
        " than the rule's own terms — webrtc, stun, turn, sdp, peer, media,"
        " video, voice, audio, call, conference, stream, screen, rtc, sip,"
        " rtp — and the only hits are API callbacks ('calls back on"
        " settlement', 'calling service') and a display ('job status on"
        " screen'). Neither is this chapter's subject, so the shape is"
        " genuinely absent rather than mis-worded. This is the first reading:"
        " the rule is right and the corpus lacks the shape, so the remedy is a"
        " Golden Case carrying a call or screen-share feature, which is a"
        " reviewed human step under evals/BLESSING.md."
    ),
}


@pytest.fixture(scope="module")
def models() -> list[SystemModel]:
    """Every blessed model, through the shipped validity gate.

    Rules only ever see valid models in production, so a case whose blessed
    model would be rejected there cannot say anything about a rule's firing.
    """
    loaded = []
    for case_dir in sorted(path for path in CORPUS_DIR.iterdir() if path.is_dir()):
        model, issues = parse_and_validate(
            json.loads((case_dir / "model.json").read_text())
        )
        assert model is not None and not issues, f"{case_dir.name}: {issues}"
        loaded.append(model)
    return loaded


def dead_rules(models: list[SystemModel]) -> set[str]:
    """Every registered rule that fires on none of the models."""
    return {
        rule.rule_id
        for package in PACKAGES.values()
        for rule in package.rules
        if not any(rule.fire(model) for model in models)
    }


def test_no_rule_fires_nowhere_without_a_stated_reason(models):
    undeclared = sorted(dead_rules(models) - set(UNEXERCISED))
    assert not undeclared, (
        "these rules fire on no corpus case, and nothing says why:"
        f" {undeclared}. Either the corpus lacks the shape — add the rule to"
        " UNEXERCISED with the reason — or the rule reads a shape extraction"
        " does not produce, which is a defect in the rule."
    )


def test_the_exemption_list_does_not_rot(models):
    """A rule that starts firing has to leave the list, or it excuses nothing."""
    revived = sorted(set(UNEXERCISED) - dead_rules(models))
    assert not revived, (
        f"these rules now fire and are still exempted: {revived}. Remove them"
        " from UNEXERCISED."
    )


def test_every_exempted_rule_is_a_rule_some_package_declares(models):
    del models
    known = {rule.rule_id for package in PACKAGES.values() for rule in package.rules}
    assert set(UNEXERCISED) <= known, (
        f"UNEXERCISED names rules no package declares: "
        f"{sorted(set(UNEXERCISED) - known)}"
    )


#: Registered knowledge documents no corpus case's selection ever reaches, each
#: with the reason. **The mirror of :data:`UNEXERCISED`, one layer up.** That
#: table catches a rule nothing fires; this catches a document whose rules fire
#: and which the per-lane cap never keeps, so an agent never receives it.
#:
#: The two failures look identical from every other check in the repo — the
#: package gate holds that the document exists and its rule IDs resolve, and
#: ``test_knowledge_lints`` holds that a rule has material — and neither asks
#: whether the material is ever *selected*.
#: The two STRIDE worked cases that used to sit here are reachable now. They
#: were never less relevant — over the whole corpus every selection was a tie at
#: one matched rule, and declaration order sent all 39 to the first-declared
#: document. The tie-break reads what the job has already sent, so a lane that
#: ties spends its slot on material the job has not.
UNSELECTED: dict[str, str] = {
    "asvs:real-time-media-and-signalling": (
        "The other reading, and a corpus gap rather than a selection one: its"
        " only rule is webrtc-real-time-media, which UNEXERCISED already records"
        " as firing on no corpus case. A document selected by a rule that never"
        " fires is unreachable for a reason that is fixed one layer down, so"
        " the Golden Case carrying a call or screen-share feature closes both"
        " entries at once."
    ),
}


def selected_documents(models: list[SystemModel]) -> set[str]:
    """Every ``package:document`` some lane of some blessed model would receive.

    Drives the shipped selector at the shipped caps, over the real candidate
    sets, which is the only way to answer what an agent is handed. Counting the
    documents a rule *could* select answers a different question, and it is the
    question the package gate already answers.

    Through :func:`~analysis_service.knowledge.select_per_lane`, because a
    lane's tie-break reads what earlier lanes were sent — so calling
    ``select_documents`` per lane here would measure a selection the graph does
    not make, and this lint would go on reporting a document as unreachable
    after it stopped being so.
    """
    reached: set[str] = set()
    for name, package in PACKAGES.items():
        for model in models:
            fired = generate_candidates(model, package.lanes, package.rules)
            fired_by_lane = [
                {candidate.rule_id for candidate in fired[lane].candidates}
                for lane in package.lanes
            ]
            for table, limit in (
                (package.knowledge.cases, MAX_CASES),
                (package.knowledge.notes, MAX_NOTES),
            ):
                reached.update(
                    f"{name}:{document}"
                    for chosen in select_per_lane(table, fired_by_lane, limit)
                    for document in chosen
                )
    return reached


def registered_documents() -> set[str]:
    return {
        f"{name}:{document}"
        for name, package in PACKAGES.items()
        for table in (package.knowledge.cases, package.knowledge.notes)
        for document in table
    }


def test_no_document_is_unreachable_without_a_stated_reason(models):
    """Material nobody receives is material that cannot help, at full cost.

    A document registered against rules that fire still reaches no agent when a
    broader document outranks it in every lane they share — the ranking is
    match count then declaration order, and the caps are one case and two notes
    per lane. Nothing else in the repo can see that, because every other check
    stops at the registration.
    """
    unreachable = sorted(registered_documents() - selected_documents(models))
    undeclared = sorted(set(unreachable) - set(UNSELECTED))

    assert not undeclared, (
        f"these knowledge documents reach no lane of any corpus case:"
        f" {undeclared}. Either a case that would select them is missing, or a"
        " broader document takes their slot in every lane — say which in"
        " UNSELECTED. A document nobody receives is prompt material paying"
        " maintenance and buying nothing."
    )


def test_the_unselected_list_does_not_rot(models):
    """A document that starts being selected has to leave, or it excuses nothing."""
    stale = sorted(set(UNSELECTED) & selected_documents(models))
    assert not stale, (
        f"these documents are selected now and still listed as unreachable:"
        f" {stale}. Remove them from UNSELECTED."
    )


def test_every_unselected_entry_names_a_registered_document():
    """A renamed document must not leave its excuse behind, silently covering nothing."""
    unknown = sorted(set(UNSELECTED) - registered_documents())
    assert not unknown, f"UNSELECTED names documents no package registers: {unknown}"
