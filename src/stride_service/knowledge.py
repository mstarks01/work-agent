"""The local corpus: reference notes and worked cases, retrieved by what fired.

``knowledge/`` is a version-controlled security library this service ships and
reads locally — no web access, no vector store, no embedding model, and no
retrieval that could return different text tomorrow for the same input. It
holds two kinds of document, and they are different in standing rather than in
subject:

* **notes** (``knowledge/notes/``) — security reference on one condition: what
  it means, what to ask about it, and what it is not. Analysis knowledge, of
  the same standing as a domain pack.
* **cases** (``knowledge/cases/``) — a worked judgement: a pattern, the threat
  considered, whether it was accepted or rejected, and what decided it. These
  are the reasoning the exemplars cannot carry, because an exemplar is a
  finished draft and half of these end in a rejection.

**Retrieval is by fired rule, and that is the whole mechanism.** A category
agent's leads are the deterministic candidates whose rules matched this model
(:mod:`stride_service.candidates`); the documents it is given are the ones
those same rules name. So a lane that triggered nothing receives nothing, a
lane looking at an unverified boundary gets the note about identity at a
boundary, and no job carries reference material about a technology or a
condition nobody's model exhibits. That is the progressive disclosure the
alternative — every document in every prompt — exists to avoid, and it needs no
scoring function, no index and no query: the model's own structure selected it.

**The corpus is knowledge, never evidence, and nothing here can change that.**
A note explains what to ask; a case shows how someone reasoned. Neither is a
fact about the system under review, neither is in the evidence catalog, and
neither can be cited — the prompt says so and the resolution seam has no branch
that could accept one. What grounds a finding is unchanged: the submitter's
words, an ``unknown`` attribute, or a derived crossing.

**Caller text selects nothing** (OWASP LLM01). Selection reads rule IDs, which
come from code; the tables below are closed; and a name outside them is never
loaded. As with :mod:`stride_service.domains`, no submitted byte reaches the
composed text through this path.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from stride_service.markdown_loader import MarkdownLoader

__all__ = [
    "CASES",
    "MAX_CASES",
    "MAX_NOTES",
    "NOTES",
    "compose_cases",
    "compose_notes",
    "select_cases",
    "select_notes",
]

# Per lane, per job. Six agents run in parallel and each pays its own tokens,
# so these caps are multiplied by six before they are a budget: two notes and
# one case per lane is roughly the size of the domain-pack block beside it.
# They are budgets rather than findings about how much material is relevant —
# the ranking below decides which of the matches survive them.
MAX_NOTES = 2
MAX_CASES = 1

# Document -> the candidate rules that select it. The direction is
# document-to-rules, matching :data:`~stride_service.domains.DETECTORS`, because
# what a maintainer edits is a document and its applicability, and a rule-keyed
# table would spread one document's entry across the file.
#
# Every rule ID here must exist in :data:`~stride_service.candidates.RULES` and
# every document must exist on disk; ``tests/test_knowledge_lints.py`` holds
# both true, so an edit to either side that forgets the other fails CI rather
# than silently dropping material out of an agent's context.
NOTES: dict[str, tuple[str, ...]] = {
    "identity-at-a-boundary": (
        "spoofing-unverified-boundary-auth",
        "spoofing-unverified-external-caller",
    ),
    "callback-and-webhook-trust": ("spoofing-unverified-external-caller",),
    "transport-protection": (
        "tampering-unprotected-transit-crossing",
        "information-disclosure-unprotected-sensitive-transit",
    ),
    "write-path-integrity": ("tampering-unverified-write-to-store",),
    "attribution-and-audit": (
        "repudiation-shared-authentication",
        "repudiation-unattributable-action",
    ),
    "protection-at-rest": ("information-disclosure-store-at-rest-unverified",),
    "cost-of-an-unauthenticated-request": (
        "denial-of-service-internet-exposed-process",
    ),
    "failure-coupling": ("denial-of-service-shared-dependency",),
    "privilege-transitions": ("elevation-of-privilege-privilege-zone-crossing",),
    "compromise-inheritance": ("elevation-of-privilege-inbound-from-exposed-process",),
}

# The same table for worked cases. A case may be selected by rules in several
# lanes on purpose: the judgement it demonstrates — an unknown control is not a
# missing one, a candidate the prose already answers — is not a property of one
# STRIDE category, and the agent that receives it is whichever lane's rule
# fired.
CASES: dict[str, tuple[str, ...]] = {
    "unknown-is-not-absent": (
        "spoofing-unverified-boundary-auth",
        "information-disclosure-store-at-rest-unverified",
        "tampering-unprotected-transit-crossing",
    ),
    "stated-control-outside-the-model": (
        "spoofing-unverified-boundary-auth",
        "tampering-unverified-write-to-store",
    ),
    "spoofing-or-elevation": (
        "elevation-of-privilege-privilege-zone-crossing",
        "spoofing-unverified-external-caller",
    ),
    "two-threats-one-flow": (
        "information-disclosure-unprotected-sensitive-transit",
        "tampering-unverified-write-to-store",
    ),
    "chained-benign-facts": (
        "elevation-of-privilege-inbound-from-exposed-process",
        "denial-of-service-shared-dependency",
    ),
    "shared-credential-attribution": (
        "repudiation-shared-authentication",
        "repudiation-unattributable-action",
    ),
}


def _select(
    index: dict[str, tuple[str, ...]], fired: Collection[str], limit: int
) -> tuple[str, ...]:
    """The documents these fired rules earn, most-matched first, capped.

    Ranked by how many of the lane's fired rules name a document, with
    declaration order as the tie-break — fixed in source, so two runs over one
    model select the same documents in the same order and the composed
    instruction is byte-identical. That stability is the same property
    :func:`~stride_service.domains.select_domain_packs` needs and for the same
    reason: an instruction that reordered between runs would make two otherwise
    identical jobs send different bytes.
    """
    order = list(index)
    matched = {
        name: len(set(rules) & set(fired))
        for name, rules in index.items()
        if set(rules) & set(fired)
    }
    ranked = sorted(matched, key=lambda name: (-matched[name], order.index(name)))
    return tuple(ranked[:limit])


def select_notes(fired: Collection[str]) -> tuple[str, ...]:
    """The reference notes one lane's fired rules earn."""
    return _select(NOTES, fired, MAX_NOTES)


def select_cases(fired: Collection[str]) -> tuple[str, ...]:
    """The worked cases one lane's fired rules earn."""
    return _select(CASES, fired, MAX_CASES)


def _compose(loader: MarkdownLoader, prefix: str, names: Sequence[str]) -> str:
    """The named documents' text, in selection order, or ``""`` for none.

    The empty string is what a lane earning nothing renders, deliberately
    rather than a "no documents selected" note: the prompt reads the block as
    optional material, and a sentence saying there is none is a sentence about
    nothing. It is also the common case for a lane whose rules all found
    nothing, which is not a defect.
    """
    if not names:
        return ""
    return "\n\n".join(loader.load(f"{prefix}/{name}").strip() for name in names) + "\n"


def compose_notes(loader: MarkdownLoader, names: Sequence[str]) -> str:
    """The selected reference notes, composed for one lane's prompt block."""
    return _compose(loader, "notes", names)


def compose_cases(loader: MarkdownLoader, names: Sequence[str]) -> str:
    """The selected worked cases, composed for one lane's prompt block."""
    return _compose(loader, "cases", names)
