"""What one critic-review fixture declares, and who established its answer.

The fixture set is the ground truth for one question: **does the critic read a
draft's argument, or only its references?** A draft whose grounds resolve can
still argue something the cited facts do not support, and until a person has
said which of these eight drafts is which, no number about that question means
anything.

**The expected ruling is a human judgement and the field says whose.** An
``expect`` block signed by nobody is one agent grading another agent's change,
which is the failure :mod:`evals.adversarial` avoids by grading in code and the
corpus avoids by holding sittings. Here neither trick is available — whether an
argument follows from a fact is exactly the judgement no rule can make — so the
provenance is carried in a field the lint reads rather than in a sentence in a
guide.

A fixture is grounded in a real corpus model. The element IDs, the attribute
values and the quoted spans are that case's, so a draft here is one an agent
could have written on a real run, and a fixture whose ground stops resolving
fails the lint rather than drifting into a test of nothing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.frameworks import FrameworkName

#: What each fixture is built to catch, and the four the review change turns on.
#:
#: ``credible-conditional`` — a sound argument that genuinely depends on an
#: unknown. It must survive, because a critic that answers the review change by
#: rejecting everything conditional has destroyed the report rather than
#: reviewed it. This is the "valid findings preserved" half, and without it the
#: other three measure nothing.
#:
#: ``nonsense-argument`` — grounds that resolve and a conclusion they do not
#: support. This is the failure the change exists to catch.
#:
#: ``irrelevant-unknown`` — an unknown the claim does not depend on. Appending
#: one is how a draft reaches the report unread today, so one fixture of this
#: kind carries a sound argument and one carries an unsound argument behind the
#: same shield: the unknown must change neither answer.
#:
#: ``contradicted-by-source`` — a claim whose own cited quote says the opposite.
#:
#: ``sound-claim-flawed-advice`` — a claim the model's stated facts support,
#: carrying a recommendation that does not close it. The only kind about the
#: *advice* rather than the claim, and the one that proves the second half of
#: the rule: a plausible recommendation must not rescue an unsupported finding,
#: and a flawed one must not erase a valid threat. Without a row of this kind
#: every fixture that can carry a reading expects the same answer, and a critic
#: replying ``sound`` to everything scores full marks.
FixtureKind = Literal[
    "credible-conditional",
    "nonsense-argument",
    "irrelevant-unknown",
    "contradicted-by-source",
    "sound-claim-flawed-advice",
]


class Expectation(BaseModel):
    """The ruling a reader says this draft deserves.

    Two fields rather than one, because the two halves of the measurement are
    different questions and only one of them is always decidable.

    ``survives`` is the headline and is decidable for every fixture: did the
    draft reach the report at all. "Unsupported findings removed" counts the
    ``False`` rows the critic rejected; "valid findings preserved" counts the
    ``True`` rows it did not.

    ``status`` is the exact verdict, and it is **optional on purpose**, for two
    different reasons.

    ``status`` is optional first because code decides it from the ruling's
    fields. A draft citing an unknown reads ``confirmed`` only where the critic
    names every such pair in ``immaterial_unknowns``, and
    :func:`~analysis_service.critic.complete_rulings` turns any pair it leaves
    out into a ``needs-info``. So the ruling states whether the critic judged
    the unknown irrelevant, and ``judged_the_unknown`` on the replay's outcome
    reads it. Which surviving verdict a fixture deserves is a separate question
    from whether the critic engaged with the unknown.

    ``rejected_because`` is optional for the second reason: which step kills a
    contradicted draft is a live question — the model showing what the draft
    calls missing reads as ``evidence``, and the draft asserting what the model
    contradicts reads as ``reasoning`` — and a fixture set proving the
    contradiction is caught should not also be litigating the taxonomy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    survives: bool
    status: Literal["confirmed", "needs-info", "rejected"] | None = None
    rejected_because: Literal["evidence", "reasoning", "lane", "duplicate"] | None = (
        None
    )
    #: Is this draft's recommendation sound advice for this claim?
    #:
    #: Read where the package's own critic text asks for a recommendation
    #: judgement. STRIDE's does; a package whose claims recommend nothing
    #: never reaches this.
    #:
    #: **Claim validity and recommendation quality are separate outcomes.** A
    #: plausible recommendation must not rescue an unsupported finding, and a
    #: flawed one must not erase a valid threat — so a negative fixture carries
    #: a recommendation that reads well and does not hold, and the critic has
    #: to reject the claim for its own argument rather than for the advice
    #: under it.
    #:
    #: ``False`` on six fixtures by the reader's ruling: two propose a control
    #: the model already states (``store:receipt-archive`` carries CMEK; the
    #: append flow carries the order service's own service account), one turns
    #: on a mechanism the source never establishes, two repair the claim's
    #: starting point while leaving the step that does not follow, and one —
    #: the only row of the six the reader says survives — moves a secret
    #: without narrowing the grant its claim is about. Each is a deliberate
    #: distractor, so nobody repairs it later as though it were a defect in
    #: the fixture.
    #:
    #: **Whether the critic noticed is observable.** A ruling carries its own
    #: reading of the advice, so a critic that opened the block and one that
    #: never did no longer emit the same ruling:
    #: ``FixtureOutcome.recommendation_read`` reads it, and
    #: ``ReplayScore.recommendation_unread`` counts the drafts that survived
    #: with no reading apart from the ones that disagree.
    recommendation_sound: bool = True
    #: Anchors the ruling's ``reason`` must name, for a fixture that must die.
    #:
    #: **A rejection is only right for the right reason.** "The control is
    #: unknown" rejects every conditional draft in the corpus and would score
    #: full marks on every negative fixture here while destroying the report.
    #: So each negative row names the inference or the contradiction the reader
    #: identified, and a reason that engages with none of them fails even though
    #: the verdict matches.
    #:
    #: Matched case-insensitively, and any one anchor is enough: the reason is
    #: prose and a reader cannot predict its wording, only the fact it has to
    #: engage with. Empty on a surviving fixture, which has nothing to justify.
    reason_must_name: tuple[str, ...] = ()


class CriticFixture(BaseModel):
    """One draft, the model it is written against, and the ruling it deserves."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    kind: FixtureKind
    #: Which package's drafts this fixture grades, and **no default**: a draft
    #: is one package's record, so a fixture that did not say would be read
    #: against whichever package a caller happened to pass. The field is what
    #: keeps the lints and the harness package-neutral — both look the package
    #: up rather than importing one — so a second package needs a second
    #: fixture file and no code change.
    framework: FrameworkName
    #: The corpus case whose blessed model this draft is written against. The
    #: model is read from the corpus rather than copied here, so a fixture
    #: cannot quietly describe a system the corpus no longer holds.
    case: str = Field(min_length=1)
    #: The draft as a lane agent would have emitted it, in the package's own
    #: record shape.
    draft: dict
    expect: Expectation
    #: Why the reader ruled that way, in their words. Read by a person, never by
    #: code, and required because an expectation nobody can argue with is an
    #: expectation nobody checked.
    rationale: str = Field(min_length=1)
    #: Who established ``expect``. ``None`` until a person signs it.
    #:
    #: **The gate reads this field.** A set no human has signed may be run and
    #: read, and may not be quoted as evidence that the review change works:
    #: ``tests/test_critic_review_lints.py`` refuses to let an unsigned set gate
    #: anything. ``bootstrap`` says who drafted it, which is never who signs it.
    reviewed_by: str | None = None
    #: Who *drafted* the fixture and its proposed answer, which is never who
    #: signs it. ``tests/test_critic_review_lints.py`` reads this beside
    #: ``reviewed_by`` and refuses a set the drafting agent signed for itself:
    #: that is the whole failure the signature exists to prevent, and a field
    #: nobody compares would let it back in silently.
    #:
    #: Named apart from ``case.json``'s ``bootstrap``, which says how a *corpus
    #: case* was made. The two facts are different and the field guard matches
    #: on a field's name, so one spelling for both would let a reader of this
    #: one stand in as a reader of that one.
    drafted_by: Literal["agent-stand-in"] = "agent-stand-in"
