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
FixtureKind = Literal[
    "credible-conditional",
    "nonsense-argument",
    "irrelevant-unknown",
    "contradicted-by-source",
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

    A supported draft carrying an unknown it does not depend on ought to be
    ``confirmed``, and today it cannot be: ``_confirmed_on_unknown_issues``
    refuses a ``confirmed`` on any draft with an unknown ground, so
    ``needs-info`` is the only surviving verdict available. Pinning an exact
    status there would pin that limitation as though it were the answer.

    **What no expectation here can see is whether the critic judged the unknown
    irrelevant.** Either surviving verdict passes such a fixture, and
    ``complete_rulings`` fills ``related_unknowns`` from the draft's own grounds
    afterwards, so a critic that weighed relevance and one that never looked
    produce the same ruling. That question needs an observable the ruling does
    not currently carry, and it is tracked apart rather than faked here.

    ``rejected_because`` is optional for the second reason: which step kills a
    contradicted draft is a live question — the model showing what the draft
    calls missing reads as ``evidence``, and the draft asserting what the model
    contradicts reads as ``reasoning`` — and a fixture set proving the
    contradiction is caught should not also be litigating the taxonomy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    survives: bool
    status: Literal["confirmed", "needs-info", "rejected"] | None = None
    rejected_because: Literal["evidence", "reasoning", "lane", "duplicate"] | None = None
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
    bootstrap: Literal["agent-stand-in"] = "agent-stand-in"
