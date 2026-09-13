"""Run one critic over the fixture set and score it, without the rest of the graph.

**Replay is what isolates the review change.** A finding that appears in one run
and not another may have moved because extraction named an element differently,
because a lane agent sampled a different draft, or because the critic ruled
differently — and an end-to-end comparison cannot tell those apart. Here the
drafts are fixed bytes a person signed, so the only thing that varies between
two runs of this module is the critic.

It calls the seams the graph calls. :func:`~analysis_service.critic.critic_view`
builds what a critic reads and :func:`~analysis_service.graph.render_fenced`
renders it, which is the same pair the ``merge`` node writes to state; the
prompt is :func:`~analysis_service.prompts.compose_critic_prompt` over the
package's own critic skills. What this module does that the node does not is
fill the prompt's placeholders itself: in the graph those become state key
names and ADK templates them at run time, and there is no session here to hold
one. The text a model receives is the same either way, and
``tests/test_critic_review_replay.py`` holds the placeholder set to the prompt
file's own so a new one fails here rather than arriving unfilled.

The model call is injected. A scripted one makes the mechanical half of this
deterministic and free, so it gates on every PR; a real one is what measures
whether the critic reads an argument, and costs one call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from analysis_service.claims import Ruling
from analysis_service.critic import complete_rulings, critic_view, review_issues
from analysis_service.frameworks import FrameworkPackage, schemas_for
from analysis_service.graph import render_fenced, rulings_of
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import compose_critic_prompt
from analysis_service.skills import compose_critic_skills
from analysis_service.system_model import SystemModel
from evals.critic_review.model import CriticFixture
from evals.harness.modes import singular

#: The placeholders this module fills, and the whole of what ``critic.md``
#: declares. Held to the prompt file by a test rather than trusted: an unfilled
#: placeholder reaches a model as a literal brace, and a critic reading
#: ``{boundary_crossings}`` where the crossings should be rules on a model it
#: cannot see and says nothing about why.
PLACEHOLDERS: tuple[str, ...] = ("system_model", "boundary_crossings", "drafts")

#: An async callable taking one composed instruction and returning the model's
#: raw text. Injected so the mechanical half runs scripted and free.
CriticCall = Callable[[str], Awaitable[str]]


def compose(
    fixtures: list[CriticFixture],
    model: SystemModel,
    package: FrameworkPackage,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
) -> str:
    """The instruction a critic reads over this fixture set.

    One prompt over every fixture rather than one per fixture, because that is
    how a critic reads a real job: the whole set at once, with the duplicate
    step able to see across it. Scoring one draft at a time would also change
    what the model is asked, which is the thing under test.
    """
    drafts = [_draft_of(fixture, package) for fixture in fixtures]
    filled = {
        "system_model": render_fenced(model.model_dump(mode="json")),
        "boundary_crossings": render_fenced(
            [
                crossing.model_dump(mode="json")
                for crossing in model.boundary_crossings()
            ]
        ),
        "drafts": render_fenced(critic_view(drafts, model)),
    }
    prompt = compose_critic_prompt(prompt_loader)
    for name, block in filled.items():
        prompt = prompt.replace("{" + name + "}", block)
    skills = compose_critic_skills(package_loader, package)
    return f"{skills.strip()}\n\n{prompt.strip()}\n"


def _draft_of(fixture: CriticFixture, package: FrameworkPackage) -> Any:
    """One fixture's draft in the package's own record shape."""
    return package.record.model_validate(fixture.draft)


def parse_rulings(raw: str, package: FrameworkPackage) -> list[Ruling]:
    """The rulings a critic returned, in the package's own shape.

    Fails loudly on output that is not the contract. A replay that quietly read
    zero rulings would report every fixture as surviving, which is the most
    flattering wrong answer available.

    **The graph's own reader, never a second one.**
    :func:`~analysis_service.graph.rulings_of` is what the ``review`` node
    parses a critic's emission with, and this module exists to run that critic
    outside the graph. The neutral :class:`~analysis_service.claims.Ruling`
    refuses every real STRIDE ruling here: ``frameworks/stride/critic.md`` —
    which :func:`compose` loads — asks for a ``confidence`` on every ruling,
    and the neutral model is ``extra="forbid"``. A scripted ruling carries no
    package field, so two readers disagree only on the paid call.
    """
    payload = json.loads(raw)
    if not isinstance(payload, dict) or "claims" not in payload:
        raise ValueError("critic output carries no 'claims' array")
    return rulings_of(payload["claims"], schemas_for(package.name))


@dataclass(frozen=True)
class FixtureOutcome:
    """What the critic did with one fixture, against what a reader ruled."""

    fixture_id: str
    kind: str
    ruled: bool
    survived: bool | None
    status: str | None
    reason: str
    #: Did the critic's answer match the signed expectation on the headline
    #: question — did this draft reach the report?
    fate_agrees: bool
    #: For a fixture that must die, did the reason engage with the inference or
    #: the contradiction the reader identified? ``None`` where nothing is owed.
    reason_engages: bool | None
    #: Did the **reader** say this draft reaches the report? Carried rather
    #: than derived from :attr:`survived` and :attr:`fate_agrees`, which cannot
    #: answer it at all where the critic returned no ruling.
    survival_expected: bool
    #: Does this draft's own evidence cite an unknown attribute? What makes
    #: :attr:`judged_the_unknown` a question worth asking of the row.
    cites_unknown: bool
    #: What the critic made of the recommendation: ``True``/``False`` as it
    #: ruled, ``None`` where it made no reading. Read off the package's own
    #: ruling field, so a package whose claims recommend nothing answers
    #: ``None`` on every fixture and scores no third half.
    recommendation_read: bool | None = None

    @property
    def passes(self) -> bool:
        """A fixture passes on its fate *and* on its reason, never on fate alone."""
        return self.fate_agrees and self.reason_engages is not False

    #: What the reader ruled the recommendation was, carried so the row says
    #: what it was compared against.
    recommendation_expected: bool | None = None
    #: Why the critic ruled the advice as it did, in its own words. Empty where
    #: it made no reading, and empty on a sound one, which is the ordinary
    #: value. Carried because ``recommendation_read`` alone is a verdict with
    #: no argument behind it, and a reader asking whether the critic found the
    #: *right* flaw has nothing else to read.
    recommendation_note: str = ""
    #: The unknown pairs the critic said this claim does not rest on. Empty
    #: where it dismissed none, which on a draft citing one is a critic that
    #: left the question alone — the fact #894 asks for, in a field.
    dismissed_unknowns: tuple[tuple[str, str], ...] = ()

    @property
    def judged_the_unknown(self) -> bool:
        """Did the critic rule on whether this claim rests on what it cites?

        A ``confirmed`` on a draft citing an unknown is only reachable by
        naming every such pair, so it answers yes by construction; a ruling
        that names a pair answers yes whatever its verdict. What this
        separates is the critic that read the question from the one that
        produced a verdict without touching it.
        """
        return bool(self.dismissed_unknowns) or self.status == "confirmed"

    @property
    def recommendation_agrees(self) -> bool | None:
        """Did the critic read the advice the way the reader ruled it?

        ``None`` where the critic made no reading, which is the third answer
        and not a failure of this comparison: a surviving draft with no reading
        is a critic that did not look, and
        :attr:`ReplayScore.recommendation_unread` counts those apart. A
        rejected draft owes no reading.
        """
        if self.recommendation_read is None:
            return None
        return self.recommendation_read == self.recommendation_expected

    def to_json(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture_id,
            "kind": self.kind,
            "ruled": self.ruled,
            "survived": self.survived,
            "status": self.status,
            "fate_agrees": self.fate_agrees,
            "reason_engages": self.reason_engages,
            "cites_unknown": self.cites_unknown,
            "dismissed_unknowns": [list(pair) for pair in self.dismissed_unknowns],
            "judged_the_unknown": self.judged_the_unknown,
            "recommendation_read": self.recommendation_read,
            "recommendation_note": self.recommendation_note,
            "recommendation_expected": self.recommendation_expected,
            "recommendation_agrees": self.recommendation_agrees,
            "passes": self.passes,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReplayScore:
    """Both halves of the measurement, never one.

    ``unsupported_removed`` counts the fixtures a reader said must die that the
    critic killed. ``valid_preserved`` counts the ones a reader said must live
    that it kept. A critic rejecting everything scores full marks on the first
    and zero on the second, which is why neither is reported alone.
    """

    outcomes: tuple[FixtureOutcome, ...]

    @property
    def negatives(self) -> tuple[FixtureOutcome, ...]:
        return tuple(o for o in self.outcomes if o.reason_engages is not None)

    @property
    def positives(self) -> tuple[FixtureOutcome, ...]:
        return tuple(o for o in self.outcomes if o.reason_engages is None)

    @property
    def unsupported_removed(self) -> tuple[int, int]:
        got = sum(1 for o in self.negatives if o.passes)
        return got, len(self.negatives)

    @property
    def valid_preserved(self) -> tuple[int, int]:
        got = sum(1 for o in self.positives if o.passes)
        return got, len(self.positives)

    @property
    def recommendation_agreed(self) -> tuple[int, int]:
        """Of the readings the critic made, the ones that match the reader's ruling.

        The third half, and the one the fixture set could not carry until a
        ruling observed a recommendation. Reported beside the other two and
        never in place of either: a critic that calls every recommendation
        unsound scores well here on a set whose recommendations are mostly
        unsound, which is why :attr:`recommendation_unread` sits next to it and
        the per-row values are printed.
        """
        read = [o for o in self.outcomes if o.recommendation_agrees is not None]
        return sum(1 for o in read if o.recommendation_agrees), len(read)

    @property
    def recommendation_informative(self) -> bool:
        """Can this set tell a real reading from a constant answer?

        **Only where the drafts this critic kept disagree about the right
        answer.** A reading is emitted on the drafts a critic lets survive, so
        those rows are the whole denominator of
        :attr:`recommendation_agreed`. Where every one of them expects the same
        ``sound``, a critic answering with that constant and never opening the
        block scores full marks — the failure :attr:`unsupported_removed` and
        :attr:`valid_preserved` were split apart to avoid.

        **It is a fact about the run, not about the set.** A set carrying both
        answers still reports false here when the critic rejected the row that
        would have discriminated, and that is the honest reading: the number
        this run produced cannot be told from a constant, whatever the set
        could have measured. :attr:`set_carries_both_answers` is the other
        half, so the warning can say which of the two happened.
        """
        return len({o.recommendation_expected for o in self.outcomes if o.survived}) > 1

    @property
    def set_carries_both_answers(self) -> bool:
        """Could this set discriminate at all, whatever the critic did with it?

        Read against the **reader's** ruling on which drafts survive, not the
        critic's, so a critic that killed the discriminating fixture is not
        reported as a deficient fixture set. Reading every row instead scored
        true on any set holding one negative fixture, because a negative row
        carries unsound advice by construction — which sent a reader to blame
        a critic that had done nothing wrong.

        :func:`carries_both_answers` is the rule, shared with the lint over the
        fixture file.
        """
        return carries_both_answers(
            (o.survival_expected, o.recommendation_expected) for o in self.outcomes
        )

    @property
    def unknown_unjudged(self) -> tuple[FixtureOutcome, ...]:
        """Surviving drafts citing an unknown that the ruling never ruled on.

        The other half of what ``immaterial_unknowns`` made observable. A
        ``needs-info`` that dismisses no pair is a critic that produced a
        verdict without touching the question ``prompts/critic.md`` asks it —
        does this claim depend on the unknown it names — and before the field
        existed that state could not be told from a critic that weighed
        relevance and said no. Counted apart for the reason
        :attr:`recommendation_unread` is: a question left alone is not a wrong
        answer, and averaging the two hides both.
        """
        return tuple(
            o
            for o in self.outcomes
            if o.survived and o.cites_unknown and not o.judged_the_unknown
        )

    @property
    def recommendation_unread(self) -> tuple[FixtureOutcome, ...]:
        """Surviving drafts the critic ruled without reading the advice on them.

        The state that had no observable: a critic that weighed the
        recommendation and one that never looked emitted the same ruling. A
        rejected draft owes no reading and is not counted here.
        """
        return tuple(
            o
            for o in self.outcomes
            if o.survived and o.recommendation_read is None and o.ruled
        )

    @property
    def rejected_without_engaging(self) -> tuple[FixtureOutcome, ...]:
        """Killed the right draft, said nothing the reader asked it to say.

        Reported apart because it is the answer a critic reaches by rejecting
        every conditional draft, and on fate alone it is indistinguishable from
        having read the argument.
        """
        return tuple(
            o for o in self.negatives if o.fate_agrees and o.reason_engages is False
        )

    def to_json(self) -> dict[str, Any]:
        removed, of_removed = self.unsupported_removed
        preserved, of_preserved = self.valid_preserved
        agreed, of_read = self.recommendation_agreed
        return {
            "unsupported_removed": {"got": removed, "of": of_removed},
            "valid_preserved": {"got": preserved, "of": of_preserved},
            "recommendation_agreed": {
                "got": agreed,
                "of": of_read,
                # A number nobody may read as a score while every draft the
                # critic kept expects one answer.
                "informative": self.recommendation_informative,
                "set_carries_both_answers": self.set_carries_both_answers,
            },
            "recommendation_unread": [o.fixture_id for o in self.recommendation_unread],
            "unknown_unjudged": [o.fixture_id for o in self.unknown_unjudged],
            "rejected_without_engaging": [
                o.fixture_id for o in self.rejected_without_engaging
            ],
            "outcomes": [o.to_json() for o in self.outcomes],
        }


def carries_both_answers(rows: Iterable[tuple[bool, bool | None]]) -> bool:
    """Do the drafts a **reader** says survive disagree about the advice?

    One rule with two callers — :attr:`ReplayScore.set_carries_both_answers`
    over a run's rows, and the lint over the fixture file — so the instrument
    and the gate cannot answer differently about the same set. Each row is
    ``(the reader's ``survives``, the reader's ``recommendation_sound``)``.

    Only a surviving draft carries a reading, so the survivors are the whole
    denominator of :attr:`ReplayScore.recommendation_agreed`. Where they all
    expect the same ``sound``, a critic answering with that constant and never
    opening the block scores full marks, which is the failure
    :attr:`ReplayScore.unsupported_removed` and
    :attr:`ReplayScore.valid_preserved` were split apart to avoid. A row the
    reader says must die is not in the question: its advice is unsound by
    construction, so counting it makes every set look discriminating.
    """
    return len({sound for survives, sound in rows if survives}) > 1


def _words(text: str) -> list[str]:
    """One text as its words, singular and lowercased.

    Through :func:`~evals.harness.modes.singular`, which is the rule this
    repository already measured for exactly this: a plural on one side and a
    singular on the other are one word, and two readers of that question
    disagree. On the first live run the critic rejected a draft for the right
    reason — "the model does not state that process:order-service **queues**
    failed database writes ... or **replays** archive objects" — against
    anchors spelled ``queue`` and ``replay``, and a word-boundary match scored
    it as engaging with nothing.
    """
    return [singular(word) for word in re.split(r"[^a-z0-9]+", text.lower()) if word]


def _engages(reason: str, anchors: tuple[str, ...]) -> bool:
    """Does this reason name any of the facts the reader asked it to address?

    Crude on purpose: the reason is prose and a reader cannot predict its
    wording, only the fact it has to engage with. What this refuses is the
    reason that engages with none of them — "the claim rests on an unknown
    control" — which is the same sentence for every conditional draft in the
    corpus.

    An anchor matches where its words appear in order and together, so a
    multi-word anchor is a phrase rather than a bag, and a word inside a longer
    word is not a match — the property the word-boundary form had and the one
    worth keeping.
    """
    words = _words(reason)
    for anchor in anchors:
        wanted = _words(anchor)
        if not wanted:
            continue
        if any(
            words[i : i + len(wanted)] == wanted
            for i in range(len(words) - len(wanted) + 1)
        ):
            return True
    return False


def score(
    fixtures: list[CriticFixture], rulings: list[Ruling], package: FrameworkPackage
) -> ReplayScore:
    """Compare what the critic ruled to what a reader signed.

    Runs the drafts and the rulings through
    :func:`~analysis_service.critic.complete_rulings` first, so a fixture is
    scored on the ruling that would have reached the report rather than on the
    critic's raw emission — the service fills ``related_unknowns`` and overrides
    a misfiled lane, and a measurement reading past that would grade a claim
    nobody would have seen.
    """
    drafts = [_draft_of(fixture, package) for fixture in fixtures]
    completed = {ruling.id: ruling for ruling in complete_rulings(drafts, rulings)}
    outcomes = []
    for fixture, draft in zip(fixtures, drafts, strict=True):
        ruling = completed.get(draft.id)
        reading = _reading(ruling)
        survived = None if ruling is None else ruling.verdict.status != "rejected"
        reason = "" if ruling is None else ruling.verdict.reason
        anchors = fixture.expect.reason_must_name
        outcomes.append(
            FixtureOutcome(
                fixture_id=fixture.id,
                kind=fixture.kind,
                ruled=ruling is not None,
                survived=survived,
                status=None if ruling is None else ruling.verdict.status,
                reason=reason,
                fate_agrees=survived == fixture.expect.survives,
                reason_engages=_engages(reason, anchors) if anchors else None,
                survival_expected=fixture.expect.survives,
                cites_unknown=bool(draft.unknown_grounds()),
                recommendation_read=None if reading is None else bool(reading.sound),
                recommendation_note="" if reading is None else reading.note,
                recommendation_expected=fixture.expect.recommendation_sound,
                dismissed_unknowns=(
                    ()
                    if ruling is None
                    else tuple(sorted(ruling.verdict.dismissed_pairs()))
                ),
            )
        )
    return ReplayScore(outcomes=tuple(outcomes))


class RecommendationReadingLike(Protocol):
    """The two fields this module reads off a package's recommendation reading.

    Structural rather than an import of STRIDE's
    :class:`~analysis_service.frameworks.stride.record.RecommendationReading`,
    because a package declares its own and this module must not name one. What
    it replaces is ``Any``, which typed nothing: ``score`` reads both fields,
    so a package declaring a reading with a verdict and no words would have
    raised ``AttributeError`` on a paid run with ``mypy`` clean beforehand.
    """

    sound: bool
    note: str


def _reading(ruling: Ruling | None) -> RecommendationReadingLike | None:
    """The critic's reading of the advice, or ``None`` where it made none.

    The one reader of that field, so the verdict on the advice and the words
    behind it cannot come from two places. Read off whatever the package's
    ruling declares, never off a package's name: a package whose claims
    recommend nothing carries no such field, so every row answers ``None`` and
    the third half has no denominator.
    """
    reading: RecommendationReadingLike | None = getattr(ruling, "recommendation", None)
    return reading


async def replay(
    fixtures: list[CriticFixture],
    model: SystemModel,
    package: FrameworkPackage,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    call: CriticCall,
) -> tuple[ReplayScore, list[str]]:
    """Compose, call, parse and score. Returns the score and any review problems.

    The review problems are :func:`~analysis_service.critic.review_issues`'
    own, run over the same drafts and rulings the graph would: a critic that
    drops a fixture or invents an ID fails the same mechanical check here as in
    a job, and the replay reports it rather than scoring around it.
    """
    drafts = [_draft_of(fixture, package) for fixture in fixtures]
    raw = await call(compose(fixtures, model, package, package_loader, prompt_loader))
    rulings = parse_rulings(raw, package)
    problems = review_issues(drafts, rulings, model)
    return score(fixtures, rulings, package), list(problems.messages)
