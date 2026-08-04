"""Can a model-emitted quote be mechanically verified against the source it names?

Throwaway prototype for issue #80. The matcher is disposable; the measurement is
the deliverable.

The corpus is the only evidence available that costs nothing to collect: all 12
``model.json`` files carry ``source_excerpt`` + ``source_label`` on every element,
and each ``case.json`` names the source file the excerpt was drawn from. Element
excerpts are not the same field as a finding's ``grounds`` quote, but they are
produced by the same instruction (``prompts/extract.md`` rule 5, "Excerpts stay
verbatim") against the same kind of input, so their false-rejection rate is the
best available proxy for the one #80 needs.

Run: ``python prototypes/quote_verification_prototype.py``
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

CORPUS = Path(__file__).resolve().parent.parent / "evals" / "corpus"

ELEMENT_COLLECTIONS = (
    "external_entities",
    "processes",
    "data_stores",
    "data_flows",
    "trust_boundaries",
)

#: Characters a model routinely substitutes for their ASCII originals when it
#: believes it is quoting verbatim.
TYPOGRAPHIC_FOLDS = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    " ": " ",
}

ELLIPSIS = re.compile(r"…|\.\.\.")


@dataclass(frozen=True)
class Excerpt:
    """One element's claim that it quoted the source verbatim."""

    case_id: str
    element_id: str
    label: str
    quote: str
    source: str


def _elements(model: dict) -> Iterator[dict]:
    for collection in ELEMENT_COLLECTIONS:
        yield from model.get(collection, [])


def load_corpus(root: Path = CORPUS) -> list[Excerpt]:
    """Every element excerpt in the corpus, paired with the text it names."""
    excerpts: list[Excerpt] = []
    for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        case = json.loads((case_dir / "case.json").read_text())
        sources = {
            source["label"]: (case_dir / source["file"]).read_text()
            for source in case["sources"]
        }
        model = json.loads((case_dir / "model.json").read_text())
        for element in _elements(model):
            quote = element.get("source_excerpt")
            if not quote:
                continue
            label = element.get("source_label", "")
            excerpts.append(
                Excerpt(
                    case_id=case_dir.name,
                    element_id=element["id"],
                    label=label,
                    quote=quote,
                    source=sources.get(label, ""),
                )
            )
    return excerpts


# --- normalization ladder ---------------------------------------------------
#
# Each rung is one policy decision the gate would have to make, applied on top
# of every rung above it. Measuring them cumulatively is the point: it shows
# what each concession buys, so a rung that recovers nothing can be refused.

Normalizer = Callable[[str], str]


def collapse_whitespace(text: str) -> str:
    """Every run of whitespace becomes one space.

    Not a concession to sloppy models: a source is hard-wrapped, so a quote of
    two consecutive words routinely straddles a newline the submitter cannot
    see and the model did not invent.
    """
    return " ".join(text.split())


def fold_typography(text: str) -> str:
    """NFKC, then the smart-quote and dash substitutions on top of it."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(TYPOGRAPHIC_FOLDS.get(char, char) for char in folded)


def fold_case(text: str) -> str:
    return text.casefold()


def strip_markdown_emphasis(text: str) -> str:
    """Drop inline markdown markers: backtick, asterisk, underscore.

    A source is submitted as prose that is frequently markdown. A model quoting
    ``` `main` ``` as ``main`` has not altered a word — it has dropped a marker
    the renderer would have dropped too.
    """
    return re.sub(r"[`*_]", "", text)


def strip_punctuation(text: str) -> str:
    """Reduce to alphanumeric words. Deliberately the far end of the ladder."""
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _compose(*normalizers: Normalizer) -> Normalizer:
    def apply(text: str) -> str:
        for normalizer in normalizers:
            text = normalizer(text)
        return text

    return apply


def contains(quote: str, source: str) -> bool:
    return bool(quote) and quote in source


def contains_fragments(quote: str, source: str) -> bool:
    """Each ellipsis-separated fragment appears, in order, after the last.

    ``extract.md`` rule 5 lets a quote mark a cut with ``…``, so a quote with an
    ellipsis is a *sequence* of verbatim spans, not one.
    """
    cursor = 0
    for fragment in ELLIPSIS.split(quote):
        fragment = fragment.strip()
        if not fragment:
            continue
        found = source.find(fragment, cursor)
        if found < 0:
            return False
        cursor = found + len(fragment)
    return True


@dataclass(frozen=True)
class Policy:
    """One rung: how both strings are normalized, and how they are compared."""

    name: str
    concession: str
    normalize: Normalizer
    compare: Callable[[str, str], bool] = contains

    def verifies(self, excerpt: Excerpt) -> bool:
        return self.compare(
            self.normalize(excerpt.quote), self.normalize(excerpt.source)
        )


_WS = _compose(collapse_whitespace)
_TYPO = _compose(fold_typography, collapse_whitespace)
_CASE = _compose(fold_typography, fold_case, collapse_whitespace)
_MD = _compose(fold_typography, fold_case, strip_markdown_emphasis, collapse_whitespace)
_PUNCT = _compose(fold_typography, fold_case, strip_punctuation)

LADDER = (
    Policy("exact", "none — byte-identical substring", str),
    Policy("+whitespace", "whitespace runs collapse", _WS),
    Policy("+typography", "NFKC, smart quotes and dashes fold", _TYPO),
    Policy("+case", "case-insensitive", _CASE),
    Policy(
        "+fragments",
        "… marks a cut; fragments match in order",
        _CASE,
        contains_fragments,
    ),
    Policy("+markdown", "inline ` * _ ignored", _MD, contains_fragments),
    Policy("+punctuation", "all punctuation ignored", _PUNCT, contains_fragments),
)


def best_window_ratio(quote: str, source: str) -> float:
    """How near a failing quote came, as a 0–1 similarity.

    Separates "the model paraphrased one word" from "the model invented the
    span" — the two failures a gate would want to treat differently.
    """
    quote = _CASE(quote)
    source = _CASE(source)
    if not quote or not source:
        return 0.0
    matcher = SequenceMatcher(None, quote, source, autojunk=False)
    return max(
        (
            SequenceMatcher(
                None, quote, source[start : start + len(quote)], autojunk=False
            ).ratio()
            for start in range(max(1, len(source) - len(quote) + 1))
        ),
        default=matcher.ratio(),
    )


#: Not measured — constructed. The corpus carries zero transcript sources, so
#: the shapes ``extract.md`` rule 5 explicitly permits (a quote across adjoining
#: turns, speaker labels kept as they appear, ``…`` marking a cut) have no
#: evidence behind them. This stands in: the shape is taken from #51's measured
#: exports — ``Speaker: text`` after same-speaker cue merging, CRLF endings.
TRANSCRIPT_PROBE = (
    "Nicolas Blank: So the storefront sits behind Cloudflare, and everything\r\n"
    "else is internal.\r\n"
    "kadowaki-tch: And the callback from the processor? Is that behind the WAF\r\n"
    "too?\r\n"
    "Nicolas Blank: I'd have to check. I don't think it is, honestly.\r\n"
)

PROBE_QUOTES = {
    "single turn, wrapped": "the storefront sits behind Cloudflare, and everything else is internal",
    "across adjoining turns, labels kept": (
        "kadowaki-tch: And the callback from the processor? Is that behind the "
        "WAF too? Nicolas Blank: I'd have to check."
    ),
    "cut marked with …": "So the storefront sits behind Cloudflare … everything else is internal",
    "label dropped mid-quote": (
        "And the callback from the processor? Is that behind the WAF too? "
        "I'd have to check."
    ),
    "apostrophe typed straight": "I'd have to check. I don't think it is",
}


def run_transcript_probe(policy: Policy) -> None:
    print(
        f"\n--- transcript probe (constructed, not measured) under '{policy.name}' ---"
    )
    for description, quote in PROBE_QUOTES.items():
        verdict = policy.compare(
            policy.normalize(quote), policy.normalize(TRANSCRIPT_PROBE)
        )
        print(f"  {'PASS' if verdict else 'FAIL'}  {description}")


#: Threats per corpus case, so a per-quote rate can be turned into a per-*job*
#: one — the number a fail-closed consequence actually lives or dies on.
THREATS_PER_CASE = 224 / 12


def report_job_risk(excerpts: list[Excerpt], quotes_per_threat: float = 1.0) -> None:
    """What a per-quote false-rejection rate costs at the scale of one job.

    The observed rate is the wrong input on its own: zero failures in 206 is not
    evidence of zero. The Rule of Three gives the 95% upper bound for a
    zero-failure sample — 3/n — and that is the number a decision to kill a job
    has to survive.
    """
    print("\n--- job-level risk ---")
    quotes = THREATS_PER_CASE * quotes_per_threat
    observed = 0 / len(excerpts)
    bound = 3 / len(excerpts)
    print(f"quotes per job (1 per threat, corpus mean): {quotes:.1f}")
    for label, rate in (("observed", observed), ("95% upper bound", bound)):
        print(
            f"  per-quote {label:<16} {rate:.2%}"
            f"  ->  per-job {1 - (1 - rate) ** quotes:.1%} chance of >=1 rejection"
        )


def main() -> None:
    excerpts = load_corpus()
    cases = {excerpt.case_id for excerpt in excerpts}
    print(f"{len(excerpts)} excerpts across {len(cases)} cases\n")

    unresolved = [excerpt for excerpt in excerpts if not excerpt.source]
    if unresolved:
        print(f"!! {len(unresolved)} excerpts name a source the case does not carry")

    print(
        f"{'policy':<14}{'verified':>10}{'failed':>8}{'false-reject':>14}   concession"
    )
    for policy in LADDER:
        failed = [excerpt for excerpt in excerpts if not policy.verifies(excerpt)]
        verified = len(excerpts) - len(failed)
        rate = len(failed) / len(excerpts)
        print(
            f"{policy.name:<14}{verified:>10}{len(failed):>8}{rate:>13.1%}   "
            f"{policy.concession}"
        )

    for rung in (LADDER[4], LADDER[5]):
        residue = [excerpt for excerpt in excerpts if not rung.verifies(excerpt)]
        print(f"\n--- {len(residue)} failures under '{rung.name}' ---")
        for excerpt in sorted(
            residue, key=lambda e: -best_window_ratio(e.quote, e.source)
        ):
            print(
                f"[{best_window_ratio(excerpt.quote, excerpt.source):.2f}] "
                f"{excerpt.case_id} / {excerpt.element_id}\n"
                f"       {excerpt.quote!r}"
            )

    # Could a similarity threshold stand in for the whole ladder? Only if the
    # quotes it should reject score below the ones it should accept.
    print("\n--- similarity separation ---")
    accepted = [e for e in excerpts if LADDER[5].verifies(e)]
    rejected = [e for e in excerpts if not LADDER[5].verifies(e)]
    if rejected:
        worst_ok = min(best_window_ratio(e.quote, e.source) for e in accepted)
        best_bad = max(best_window_ratio(e.quote, e.source) for e in rejected)
        print(f"lowest score among quotes the ladder accepts: {worst_ok:.3f}")
        print(f"highest score among quotes the ladder rejects: {best_bad:.3f}")
        print(f"separable by a threshold: {best_bad < worst_ok}")

    run_transcript_probe(LADDER[5])
    report_job_risk(excerpts)

    lengths = sorted(len(excerpt.quote) for excerpt in excerpts)
    midpoint = lengths[len(lengths) // 2]
    print(
        f"\nquote length: min {lengths[0]}, median {midpoint}, max {lengths[-1]} chars;"
        f" {sum(1 for n in lengths if n < 30)} under 30"
    )


if __name__ == "__main__":
    main()
