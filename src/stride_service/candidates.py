"""The candidate machinery: how a rule fires and how its hits are grouped.

A **Candidate** is a structural condition a deterministic rule found in the
validated System Model and handed to one lane agent as something to
investigate: a rule ID, the elements it is about, and the model facts that made
it fire.

**Neutral by design, and empty of rules.** The rules themselves belong to a
**Framework Package** — a rule decides which lane sees a lead, and a lane is a
framework's own unit — so STRIDE's eleven live in
:mod:`stride_service.frameworks.stride.rules`. What is here is the shape a rule
takes, the shape a hit takes, and the fold that evaluates a package's whole
table against one model.

The line this module does not cross: **a candidate is never evidence.** It
carries no severity, no attacker story, no claim that anything is wrong, and it
cannot become a :class:`~stride_service.report.Claim` — nothing downstream of
the prompt reads a candidate at all. A rule's whole contribution is *attention*.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from stride_service.system_model import SystemModel

__all__ = [
    "MAX_FACT_CHARS",
    "Candidate",
    "CandidateSet",
    "Fact",
    "Match",
    "Rule",
    "clip_fact",
    "generate_candidates",
]

# How much of a caller-authored attribute value a fact carries. Facts exist to
# say which state an attribute is in, not to re-render the model — the agent
# has the whole thing under ``{system_model}``.
MAX_FACT_CHARS = 200

Fact = str | int | bool
Match = tuple[tuple[str, ...], dict[str, Fact]]
"""One rule hit: the element IDs it is about, and the facts that made it fire."""


def clip_fact(value: str) -> str:
    """A caller-authored attribute value, cut to what a fact may carry."""
    return value[:MAX_FACT_CHARS]


class Candidate(BaseModel):
    """One fired trigger: a condition to investigate, with the facts behind it.

    ``facts`` are the model values the rule read, so the agent can see the
    trigger without taking the rule's word for it — an ``authentication`` of
    ``unknown`` is a question to ask, one of ``none`` is a control the
    submitter said is not there, and the two produce different findings.

    ``lane`` is a plain string rather than a typed enum, because the legal set
    is whatever package declared the rule. The typing that used to make a
    mis-filed rule unrepresentable is now the package gate's check that every
    rule names a lane its package declares.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1, max_length=100)
    lane: str = Field(min_length=1, max_length=100)
    element_ids: tuple[str, ...] = Field(min_length=1)
    facts: dict[str, Fact] = Field(default_factory=dict)


class CandidateSet(BaseModel):
    """One lane's candidates, plus the questions their rules put.

    The questions ride beside the candidates rather than inside each one: a
    rule that fires eight times would otherwise spend its sentence eight
    times, and the agent reads the question once per rule either way.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    lane: str = Field(min_length=1, max_length=100)
    questions: dict[str, str] = Field(default_factory=dict)
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True)
class Rule:
    """One deterministic trigger: its lane, its question, and how it fires.

    ``find`` yields :data:`Match` values rather than :class:`Candidate` ones,
    so a rule cannot file under a lane that is not its own — the ID and the
    lane are stamped by :meth:`fire`, from the rule's own fields.
    """

    rule_id: str
    lane: str
    question: str
    find: Callable[[SystemModel], Iterator[Match]]

    def fire(self, model: SystemModel) -> list[Candidate]:
        """Every candidate this rule produces for this model, in model order."""
        return [
            Candidate(
                rule_id=self.rule_id,
                lane=self.lane,
                element_ids=element_ids,
                facts=facts,
            )
            for element_ids, facts in self.find(model)
        ]


def generate_candidates(
    model: SystemModel, lanes: Sequence[str], rules: Sequence[Rule]
) -> dict[str, CandidateSet]:
    """One package's rules evaluated against the model, grouped into its lanes.

    Every lane gets an entry, including one whose rules all found nothing: an
    empty candidate set is the honest statement that deterministic analysis
    surfaced no structural lead here, and it is a different thing from a lane
    that was never offered any.
    """
    return {lane: _candidate_set(lane, model, rules) for lane in lanes}


def _candidate_set(
    lane: str, model: SystemModel, rules: Sequence[Rule]
) -> CandidateSet:
    in_lane = [rule for rule in rules if rule.lane == lane]
    fired = [candidate for rule in in_lane for candidate in rule.fire(model)]
    return CandidateSet(
        lane=lane,
        questions={
            rule.rule_id: rule.question
            for rule in in_lane
            if any(candidate.rule_id == rule.rule_id for candidate in fired)
        },
        candidates=tuple(fired),
    )
