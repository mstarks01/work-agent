"""Is a quoted span actually in the source it names?

One question, answered mechanically: a ``quote`` **Ground** claims a verbatim
span from a named **Source**, and this module decides whether that span is
there. It is deliberately the whole of the check — it proves a quote is
*present*, never that it *supports* the finding. Nothing here can tell a
verbatim, irrelevant quote from a load-bearing one; that judgement is the
critic's, and the prompt's states-it-not-mentions-it rule is the only thing
that ever addresses it.

Model output is untrusted input (OWASP LLM05): a quote is bytes a model chose,
matched against bytes a caller submitted, and neither is interpreted as
anything but text here — no regular expression is compiled from either side, so
a hostile quote cannot cost more than its length.

THE LADDER IS PINNED, and each rung is a policy decision rather than a
convenience. Measured over the 12 corpus cases' 206 element excerpts — produced
by the same "quote verbatim" instruction against the same kind of input, so the
closest proxy available:

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

Two facts govern the shape. **Exact equality is catastrophically wrong, and not
because models are sloppy**: sources are hard-wrapped, so a quote of two
consecutive words routinely straddles a newline the submitter never sees and
the model did not invent. Collapsing whitespace runs takes 78.2% to 1.0% and is
carrying the entire result — typography, case and fragment handling each
recovered *nothing*, and stay in only because each is a real permission the
prompt grants or a real substitution models make. Nobody should believe they
are load-bearing.

And **punctuation-blindness is refused**: it recovers nothing the markdown rung
does not, so it is a concession that buys zero precision and spends real
precision. The ladder stops at ``+markdown``, where the measured false-rejection
rate is 0 in 206 with one true rejection — a quote that excised a span and
stitched a subject onto a predicate, unmarked, producing a sentence the source
never contains. That is exactly the manufactured citation this check exists to
catch.

NOT A SIMILARITY THRESHOLD. Best-window similarity puts the lowest accepted
quote at 0.986 and the highest rejected one at 0.963 — separable in principle,
by 2.3 points, fitted to a single negative example. Any threshold a human would
pick by intuition (0.90, 0.95) accepts the fabricated stitch. A deterministic
ladder is also explainable to a submitter — "this word is not in your document"
— and a threshold is not.

False acceptance is not a problem at these lengths: every corpus quote matched
against all 11 sources it did *not* come from gave 0 spurious matches in 2,266
wrong-source pairs. Quotes run 29-229 characters, median 80 — long enough that
the text check independently discriminates the source rather than leaning on
the label check to do it.
"""

from __future__ import annotations

import re
import unicodedata

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

    The fifth rung: ``…`` splits the quote into fragments, each of which must
    appear **in order, after the last**. A quote marking a cut is a *sequence*
    of verbatim spans rather than one, so searching from a cursor is what makes
    the marker mean something — an unmarked elision, which is the failure mode
    both measured true rejections shared, still fails.

    A quote with no fragment left after normalization verifies against nothing.
    The schema requires a ``quote`` ground to carry text, so a blank one has
    already failed validation — but ``"…"`` is non-blank and normalizes away to
    nothing, and letting it through would make it the universal citation.
    """
    haystack = normalize(source)
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
