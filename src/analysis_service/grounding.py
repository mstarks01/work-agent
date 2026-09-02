"""Is a quoted span actually in the source it names?

This module answers one question mechanically. A ``quote`` **Ground** claims a
verbatim span from a named **Source**, and this module decides whether that span
is there. That is deliberately the whole of the check. It proves a quote is
present, and never that it supports the finding. Nothing here can tell a
verbatim but irrelevant quote from a load-bearing one. That judgement is the
critic's, and the prompt's rule about stating rather than mentioning is the only
thing that addresses it.

Model output is untrusted input (OWASP LLM05). A quote is bytes a model chose,
matched against bytes a caller submitted, and this module interprets neither as
anything but text. It compiles no regular expression from either side, so the
ladder proper cannot cost more than the quote's length times the source's.

The repair rung is where cost has to be bounded on purpose, because it is the
one place both lengths multiply into a search. :data:`MAX_REPAIR_WORK` is that
bound, and the constant carries the measurements behind it. Without it a caller
sizes the rung's work directly — both terms come from the submitted text — and
one refused quote runs for minutes.

The ladder is pinned, and each rung is a policy decision rather than a
convenience. The figures below are measured over the 12 corpus cases' 206
element excerpts, which the same "quote verbatim" instruction produced against
the same kind of input, and are the closest proxy available:

===============  ========  =============  ===================================
policy           verified  false-reject   concession
===============  ========  =============  ===================================
exact                  45         78.2%   none — byte-identical substring
+whitespace           204          1.0%   whitespace runs collapse
+typography           204          1.0%   NFKC, smart quotes and dashes fold
+case                 204          1.0%   case-insensitive
+fragments            204          1.0%   ``…`` marks a cut
+markdown             205          0.5%   inline ``` ` ``` ``*`` ``_`` ignored
+punctuation          205          0.5%   all punctuation ignored
===============  ========  =============  ===================================

Two facts govern the shape. Exact equality is catastrophically wrong, and not
because models are sloppy. Sources are hard-wrapped, so a quote of two
consecutive words routinely straddles a newline the submitter never sees and the
model did not invent. Collapsing whitespace runs takes 78.2% to 1.0%, and it
carries the entire result. Typography, case and fragment handling each recovered
nothing. They stay only because each is a real permission the prompt grants or a
real substitution models make, and nobody should believe they are load-bearing.

Punctuation-blindness is refused. It recovers nothing the markdown rung does
not, so it is a concession that buys no precision and spends real precision. The
ladder stops at ``+markdown``, where the measured false-rejection rate is 0 in
206, with one true rejection. That rejection was a quote that excised a span and
stitched a subject onto a predicate, unmarked, producing a sentence the source
never contains. That is the manufactured citation this check exists to catch.

This is not a similarity threshold. Best-window similarity puts the lowest
accepted quote at 0.986 and the highest rejected one at 0.963. They are
separable in principle, by 2.3 points, fitted to a single negative example. Any
threshold a person would pick by intuition, such as 0.90 or 0.95, accepts the
fabricated stitch. A deterministic ladder is also explainable to a submitter —
"this word is not in your document" — and a threshold is not.

The repair rung is a different thing. :func:`repair_quote` runs only after the
ladder refused a quote, and it does not accept the quote. It finds the source's
own span nearest to it and hands that span back, so the report carries the
submitter's words and never the model's. The agent's words survive beside it as
a mark. Its candidates are windows of the source whose word count is the
quote's, or up to two words more, which is what keeps the fabricated stitch out:
a stitch is short and the span it was cut from is long, so the two are never
compared. The threshold, :data:`REPAIR_THRESHOLD`, decides only how far a window
may differ from the quote before the rung gives up and the quote stays
unverified.

False acceptance is not a problem at these lengths. Every corpus quote matched
against all 11 sources it did not come from gave 0 spurious matches in 2,266
wrong-source pairs. Quotes run from 29 to 229 characters, with a median of 80.
That is long enough for the text check to discriminate the source on its own,
rather than leaning on the label check to do it.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

#: How alike a source window and a refused quote must be, as a
#: :class:`difflib.SequenceMatcher` ratio over their normalized forms, for the
#: rung to hand the window back. At the corpus median of 80 characters this is
#: at most eight characters of difference: a dropped article, a changed
#: preposition, a tidied plural. It is not fitted to a measurement yet. The
#: eval sweep's repaired count is the number that moves it.
REPAIR_THRESHOLD = 0.9

#: How many words longer than the quote a candidate window may be. Never
#: shorter: a tidy drops or swaps words, so the span it came from is as long
#: or longer, and a shorter window that wins on ratio has cut a word the quote
#: carried.
_REPAIR_WIDTH_SLACK = 2

#: The largest candidate scan :func:`repair_quote` will run, as the source's
#: word count times the square of the quote's. Above it the rung gives up and
#: the quote stays unverified, which is an outcome the report already carries.
#:
#: The square is the measured shape, not a guess. The scan walks one window per
#: source word, and each window that :meth:`~difflib.SequenceMatcher.quick_ratio`
#: does not prune costs a :meth:`~difflib.SequenceMatcher.ratio` quadratic in
#: the quote's length. A quote whose characters are the window's — which is what
#: a reordering of the source's own words produces — prunes nothing, so the
#: worst case is every window paying it:
#:
#: =============  ===========  =========
#: source words   quote words  seconds
#: =============  ===========  =========
#: 2,000                   20      0.11
#: 2,000                   60      4.23
#: 1,000                  140     45.76
#: 2,000                  140     92.30
#: =============  ===========  =========
#:
#: Those hold ``words x width^2 / seconds`` near 426,000, and this bound is
#: roughly nine seconds of it. The submitted text is what sets both terms, so
#: without a bound a caller sizes this rung's cost directly: 100 KiB of short
#: words is 51,200 of them, and a 500-word quote against them ran for minutes.
#: A quote of the corpus median, 13 words, still repairs against the largest
#: source the service accepts.
MAX_REPAIR_WORK = 4_000_000

#: Characters a model routinely substitutes for their ASCII originals when it
#: believes it is quoting verbatim. NFKC folds most of the width and ligature
#: cases; these are the ones it leaves alone.
_TYPOGRAPHIC_FOLDS = {
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

# A cut, however the model spelled it. Both forms are in play: the prompt asks
# for `…` and models emit the three-dot form regardless.
_ELLIPSIS = re.compile(r"…|\.\.\.")

# Inline markdown markers. A source is submitted as prose that is frequently
# markdown, so a model quoting ``` `main` ``` as ``main`` has not altered a
# word — it dropped a marker the renderer would have dropped too.
_INLINE_MARKUP = re.compile(r"[`*_]")


def normalize(text: str) -> str:
    """Apply the pinned ladder's four normalization rungs, in order.

    Run over the quote and the source alike — a rung applied to one side only
    would compare two different dialects of the same string.
    """
    folded = unicodedata.normalize("NFKC", text)
    folded = "".join(_TYPOGRAPHIC_FOLDS.get(char, char) for char in folded)
    folded = _INLINE_MARKUP.sub("", folded.casefold())
    return " ".join(folded.split())


def verify_quote(quote: str, source: str) -> bool:
    """Does ``quote`` appear in ``source`` under the pinned ladder?

    The single-source form, for a caller holding raw text. A caller checking
    many quotes against the same few sources should normalize each source once
    and call :func:`verify_normalized` instead — the ladder folds every
    character of the haystack, so re-folding a whole submission per quote is
    the same answer paid for repeatedly.
    """
    return verify_normalized(quote, normalize(source))


def verify_normalized(quote: str, haystack: str) -> bool:
    """The same question, against a source :func:`normalize` already folded.

    The fifth rung: ``…`` splits the quote into fragments, each of which must
    appear **in order, after the last**. A quote marking a cut is a *sequence*
    of verbatim spans rather than one, so searching from a cursor is what makes
    the marker mean something — an unmarked elision, which is the failure mode
    both measured true rejections shared, still fails.

    A quote with no fragment left after normalization verifies against nothing.
    The schema requires a ``quote`` ground to carry text, so a blank one has
    already failed validation — but ``"…"`` is non-blank and normalizes away to
    nothing, and letting it through would make it the universal citation.

    ``haystack`` must be normalized already: a rung applied to one side only
    would compare two different dialects of the same string.
    """
    fragments = [normalize(raw) for raw in _ELLIPSIS.split(quote)]
    matched = False
    cursor = 0
    for fragment in fragments:
        if not fragment:
            continue
        found = haystack.find(fragment, cursor)
        if found < 0:
            return False
        cursor = found + len(fragment)
        matched = True
    return matched


def repair_quote(quote: str, source: str) -> tuple[str, float] | None:
    """The source's own span nearest a refused quote, or ``None``.

    Run only after :func:`verify_quote` said no. The answer is a span cut from
    ``source`` as written — the submitter's words, whitespace collapsed — that
    :func:`verify_quote` accepts by construction, plus the similarity that
    earned it. A caller replaces the quote with it and records the replacement.

    Candidates are every run of whole words whose count is the quote's, up to
    ``_REPAIR_WIDTH_SLACK`` more, compared under :func:`normalize` on
    both sides so the rungs the ladder already forgives cost nothing here. The
    best candidate wins if it reaches :data:`REPAIR_THRESHOLD`.

    Each source word is normalized once rather than once per window that covers
    it, which is the same comparison for a fraction of the folding: a window's
    folded form is its folded words joined, because the ladder's rungs are
    per-character and the last one collapses whitespace.

    A scan over :data:`MAX_REPAIR_WORK` is refused outright rather than run.
    Answering ``None`` here is the same answer the threshold gives when no
    window is close enough, and the caller already handles it: the quote stays
    unverified and the report says so.

    A quote marking a cut with ``…`` is not repaired: each fragment is a span of
    its own, and one nearest window for the whole is a span the quote never
    claimed.
    """
    if _ELLIPSIS.search(quote):
        return None
    needle = normalize(quote)
    if not needle:
        return None
    words = source.split()
    width = len(quote.split())
    if len(words) * width * width > MAX_REPAIR_WORK:
        return None
    folded = [normalize(word) for word in words]
    best: tuple[str, float] | None = None
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(needle)
    for count in range(width, width + _REPAIR_WIDTH_SLACK + 1):
        for start in range(len(words) - count + 1):
            matcher.set_seq1(" ".join(w for w in folded[start : start + count] if w))
            if matcher.quick_ratio() < REPAIR_THRESHOLD:
                continue
            ratio = matcher.ratio()
            if ratio >= REPAIR_THRESHOLD and (best is None or ratio > best[1]):
                best = (" ".join(words[start : start + count]), ratio)
    return best
