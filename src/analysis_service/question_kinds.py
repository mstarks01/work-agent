"""The kinds of question the critic may ask about an element, as a closed table.

The critic names an open fact with no place in the model as a free-text
``subject``, and its words change from one sample to the next: 77 of 79 such
questions on one sweep were distinct texts. Their kinds do not: the same 79
fell into 11 kinds, and a table read off the first six cases covered 40 of the
46 questions after them (``QA-2026-09-26-03-E8``). So the critic picks a kind
from this table and the element it is about, and both come from closed sets. A
question with no fitting kind still falls back to a ``subject``, and how often
it does is what says whether this table is enough.

**Data only.** :mod:`analysis_service.claims` puts the IDs into the provider
schema and :mod:`analysis_service.questions` renders and answers them, so this
module imports neither.

**The maintainer reviewed the wording.** An agent drafted every row from the
typed questions, and ``reviewed_by`` names who accepted it. A new row carries
``None`` until the maintainer reads it. ``answer`` follows from the wording: a
question that opens with "Are", "Can" or "Does" asks whether something holds.
A compound question is answered in its facets, which the maintainer reviewed
with the kinds on 2026-09-29 (#1289).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

__all__ = ["ALL_FACET_IDS", "MAX_FACETS", "QUESTION_KINDS", "Facet", "QuestionKind"]


@dataclass(frozen=True)
class Facet:
    """One short part of a compound question kind, answered yes, no, not
    applicable or "I don't know"."""

    #: Stable within its kind; an answer names the facet by it.
    id: str
    #: The part as a question, with "it" for the element.
    question: str


@dataclass(frozen=True)
class QuestionKind:
    """One kind of question about one element of the model."""

    #: The question, with ``{element}`` where the element's name goes.
    template: str
    #: What the kind covers, for the critic choosing among kinds.
    covers: str
    #: Who reviewed the wording, or ``None`` where nobody has.
    reviewed_by: str | None
    #: How the question is answered: ``yes-no`` where it asks whether one
    #: thing holds, so a page offers yes, no or "I don't know"; ``facets``
    #: where it asks several things, each a :class:`Facet`; ``text`` where it
    #: asks who, what or how and no facet fits. No default, so a new row says
    #: which.
    answer: Literal["yes-no", "facets", "text"]
    #: The parts a ``facets`` kind is answered in, and empty for every other.
    facets: tuple[Facet, ...]


QUESTION_KINDS: Mapping[str, QuestionKind] = MappingProxyType(
    {
        "capacity-limits": QuestionKind(
            template="What limits bound the requests and work {element} accepts:"
            " rate, size, concurrency, quotas or queue bounds?",
            covers="flooding, exhaustion, missing quotas or load shedding",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet("rate", "Does it limit the rate of requests?"),
                Facet("size", "Does it limit the size of a request or message?"),
                Facet("concurrency", "Does it limit how much work runs at once?"),
                Facet("quota", "Does it set a quota per user or tenant?"),
            ),
        ),
        "audit-evidence": QuestionKind(
            template="What record shows who acted on {element}, and who can"
            " change or delete that record?",
            covers="attribution, audit trails, evidence that survives a dispute",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet("records-actor", "Does it record who performed each action?"),
                Facet(
                    "record-protected",
                    "Is that record kept where the people it records cannot change or delete it?",
                ),
            ),
        ),
        "authorization-scope": QuestionKind(
            template="Who may do what on {element}, and does it check each"
            " request against that?",
            covers="per-request authorization, roles, object ownership, grant scope",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet(
                    "checks-each-request",
                    "Does it check each request against what the caller may do?",
                ),
                Facet(
                    "least-privilege",
                    "Are permissions narrower than full access, such as roles or scopes?",
                ),
                Facet(
                    "checks-ownership",
                    "Does it check that the caller owns the object it names?",
                ),
            ),
        ),
        "stored-copy-integrity": QuestionKind(
            template="Are backups, snapshots and restores of {element} checked"
            " for tampering before they are used?",
            covers="integrity of backups, snapshots, restores and stored media",
            reviewed_by="mstarks01",
            answer="yes-no",
            facets=(),
        ),
        "stored-copy-access": QuestionKind(
            template="Who can obtain a disk, backup or snapshot copy of {element}?",
            covers="access to the storage layer beneath an application",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet("encrypted", "Are its backups and snapshots encrypted?"),
                Facet(
                    "restricted",
                    "Can only named operators get its disks, backups or snapshots?",
                ),
            ),
        ),
        "reachability": QuestionKind(
            template="Who can reach {element}'s interface, and from where?",
            covers="who can reach an interface where no `exposure` attribute answers it",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet("internet", "Can the internet reach it?"),
                Facet(
                    "named-callers",
                    "Can only named callers reach it, such as through an allow-list, a private network or a VPN?",
                ),
            ),
        ),
        "provenance": QuestionKind(
            template="Does {element} check where what it takes came from, and"
            " that it was not altered?",
            covers="signatures, origin checks, supply-chain provenance",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet(
                    "sender",
                    "Does it check who sent what it takes, such as by a signature or an authenticated sender?",
                ),
                Facet("integrity", "Does it check that what it takes was not altered?"),
            ),
        ),
        "code-execution": QuestionKind(
            template="Can content that {element} takes run code with its authority?",
            covers="loaders, build steps or parsers that execute supplied content",
            reviewed_by="mstarks01",
            answer="yes-no",
            facets=(),
        ),
        "physical-access": QuestionKind(
            template="Can someone with physical access to {element} read its"
            " secrets or change it?",
            covers="devices and hosts outside the operator's physical control",
            reviewed_by="mstarks01",
            answer="yes-no",
            facets=(),
        ),
        "content-validation": QuestionKind(
            template="Does {element} check the values it takes against what it"
            " expects, and encode or bind them where it uses them?",
            covers="input validation, output encoding, query construction",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet(
                    "validates",
                    "Does it check the values it takes against what it expects?",
                ),
                Facet(
                    "encodes",
                    "Does it encode or bind values where it uses them, such as in queries, commands or pages?",
                ),
            ),
        ),
        "destination": QuestionKind(
            template="Does {element} check that what it sends reaches the"
            " intended recipient?",
            covers="delivery to the right party, stale or altered destinations",
            reviewed_by="mstarks01",
            answer="yes-no",
            facets=(),
        ),
        "security-configuration": QuestionKind(
            template="Which security settings does {element} use, such as cookie"
            " attributes, response headers, cross-origin rules or secret storage?",
            covers="configuration a deployment sets rather than a design states",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet("cookies", "Are its cookies set Secure, HttpOnly and SameSite?"),
                Facet("headers", "Does it send security headers such as CSP and HSTS?"),
                Facet(
                    "cross-origin",
                    "Does it limit cross-origin requests to named origins?",
                ),
                Facet(
                    "secret-store",
                    "Are its secrets kept in a secret store, not in code or files?",
                ),
            ),
        ),
        "failure-handling": QuestionKind(
            template="What happens when {element} fails or stops, and who notices?",
            covers="failure detection, silent staleness, error handling",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet(
                    "fails-closed",
                    "When a dependency fails, does it refuse requests rather than skip its checks?",
                ),
                Facet("alerts", "Is someone alerted when it fails or stops?"),
                Facet("recovers", "Does it restart or recover on its own?"),
            ),
        ),
        "update-process": QuestionKind(
            template="How are {element}'s components kept up to date?",
            covers="patching, update windows, remediation time frames",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet("scheduled", "Are its components updated on a schedule?"),
                Facet(
                    "vulnerability-alerts",
                    "Is someone alerted about known vulnerabilities in its components?",
                ),
                Facet("tested", "Are updates tested before they reach production?"),
            ),
        ),
        "data-exposure": QuestionKind(
            template="Which data does {element} return or record, and who can see it?",
            covers="response fields, data in URLs or logs, classification",
            reviewed_by="mstarks01",
            answer="facets",
            facets=(
                Facet(
                    "minimal-fields", "Does it return only the fields the caller needs?"
                ),
                Facet(
                    "clean-logs",
                    "Does it keep secrets and personal data out of its logs?",
                ),
                Facet(
                    "restricted-readers",
                    "Can only authorised readers see what it records?",
                ),
            ),
        ),
    }
)

#: Every facet ID some kind has, which a reference may name.
ALL_FACET_IDS: frozenset[str] = frozenset(
    facet.id for kind in QUESTION_KINDS.values() for facet in kind.facets
)

#: The most facets one kind has, and so the most one reference names.
MAX_FACETS = max(len(kind.facets) for kind in QUESTION_KINDS.values())
