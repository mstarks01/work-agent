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
``None`` until the maintainer reads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = ["QUESTION_KINDS", "QuestionKind"]


@dataclass(frozen=True)
class QuestionKind:
    """One kind of question about one element of the model."""

    #: The question, with ``{element}`` where the element's name goes.
    template: str
    #: What the kind covers, for the critic choosing among kinds.
    covers: str
    #: Who reviewed the wording, or ``None`` where nobody has.
    reviewed_by: str | None


QUESTION_KINDS: Mapping[str, QuestionKind] = MappingProxyType(
    {
        "capacity-limits": QuestionKind(
            template="What limits bound the requests and work {element} accepts:"
            " rate, size, concurrency, quotas or queue bounds?",
            covers="flooding, exhaustion, missing quotas or load shedding",
            reviewed_by="mstarks01",
        ),
        "audit-evidence": QuestionKind(
            template="What record shows who acted on {element}, and who can"
            " change or delete that record?",
            covers="attribution, audit trails, evidence that survives a dispute",
            reviewed_by="mstarks01",
        ),
        "authorization-scope": QuestionKind(
            template="Who may do what on {element}, and does it check each"
            " request against that?",
            covers="per-request authorization, roles, object ownership, grant scope",
            reviewed_by="mstarks01",
        ),
        "stored-copy-integrity": QuestionKind(
            template="Are backups, snapshots and restores of {element} checked"
            " for tampering before they are used?",
            covers="integrity of backups, snapshots, restores and stored media",
            reviewed_by="mstarks01",
        ),
        "stored-copy-access": QuestionKind(
            template="Who can obtain a disk, backup or snapshot copy of {element}?",
            covers="access to the storage layer beneath an application",
            reviewed_by="mstarks01",
        ),
        "reachability": QuestionKind(
            template="Who can reach {element}'s interface, and from where?",
            covers="who can reach an interface where no `exposure` attribute answers it",
            reviewed_by="mstarks01",
        ),
        "provenance": QuestionKind(
            template="Does {element} check where what it takes came from, and"
            " that it was not altered?",
            covers="signatures, origin checks, supply-chain provenance",
            reviewed_by="mstarks01",
        ),
        "code-execution": QuestionKind(
            template="Can content that {element} takes run code with its authority?",
            covers="loaders, build steps or parsers that execute supplied content",
            reviewed_by="mstarks01",
        ),
        "physical-access": QuestionKind(
            template="Can someone with physical access to {element} read its"
            " secrets or change it?",
            covers="devices and hosts outside the operator's physical control",
            reviewed_by="mstarks01",
        ),
        "content-validation": QuestionKind(
            template="Does {element} check the values it takes against what it"
            " expects, and encode or bind them where it uses them?",
            covers="input validation, output encoding, query construction",
            reviewed_by="mstarks01",
        ),
        "destination": QuestionKind(
            template="Does {element} check that what it sends reaches the"
            " intended recipient?",
            covers="delivery to the right party, stale or altered destinations",
            reviewed_by="mstarks01",
        ),
        "security-configuration": QuestionKind(
            template="Which security settings does {element} use, such as cookie"
            " attributes, response headers, cross-origin rules or secret storage?",
            covers="configuration a deployment sets rather than a design states",
            reviewed_by="mstarks01",
        ),
        "failure-handling": QuestionKind(
            template="What happens when {element} fails or stops, and who notices?",
            covers="failure detection, silent staleness, error handling",
            reviewed_by="mstarks01",
        ),
        "update-process": QuestionKind(
            template="How are {element}'s components kept up to date?",
            covers="patching, update windows, remediation time frames",
            reviewed_by="mstarks01",
        ),
        "data-exposure": QuestionKind(
            template="Which data does {element} return or record, and who can see it?",
            covers="response fields, data in URLs or logs, classification",
            reviewed_by="mstarks01",
        ),
    }
)
