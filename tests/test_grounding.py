"""The pinned quote-verification ladder.

Each rung is a policy decision, so each has a test naming what it forgives and
one naming what it still refuses. The two cases at the bottom are the real
ones: the corpus's single true rejection, and the transcript probe's — both the
same failure mode, an unmarked elision stitched into a sentence the source never
contains.
"""

import pytest

from analysis_service.grounding import (
    MAX_REPAIR_WORK,
    REPAIR_THRESHOLD,
    normalize,
    repair_quote,
    verify_quote,
)

# Hard-wrapped at ~72 characters, the way the corpus sources are. This is what
# makes the whitespace rung carry the entire result: a quote of two consecutive
# words routinely straddles a newline the submitter never sees.
SOURCE = (
    "The ledger service talks to the accounts database with a single\n"
    "shared password out of an environment variable, and that account\n"
    "has full read/write on every table. We rebuild `main` by hand when\n"
    "a deploy goes wrong.\n"
)


class TestWhatTheLadderForgives:
    def test_a_quote_straddling_a_hard_wrap(self):
        """The rung that took 78.2% false rejections to 1.0%."""
        assert verify_quote("with a single shared password", SOURCE)

    def test_smart_quotes_and_dashes_folded_to_ascii(self):
        assert verify_quote("read/write on every table", SOURCE.replace("/", "／"))

    def test_case_is_folded(self):
        assert verify_quote("FULL READ/WRITE ON EVERY TABLE", SOURCE)

    def test_a_dropped_markdown_marker(self):
        """The model dropped a marker the renderer would have dropped too."""
        assert verify_quote("We rebuild main by hand", SOURCE)

    def test_an_elision_marked_with_an_ellipsis(self):
        assert verify_quote("a single shared password … on every table", SOURCE)

    def test_the_three_dot_spelling_of_an_elision(self):
        assert verify_quote("a single shared password ... on every table", SOURCE)


class TestWhatTheLadderStillRefuses:
    def test_an_unmarked_elision_stitched_into_a_new_sentence(self):
        """The one true rejection in 206 measured excerpts.

        Every fragment is present; the *sentence* is manufactured. Requiring
        the marker is what makes the difference detectable.
        """
        assert not verify_quote("a single shared password on every table", SOURCE)

    def test_fragments_out_of_order(self):
        """`…` means "and then", so a reordered pair is not a cut."""
        assert not verify_quote("on every table … a single shared password", SOURCE)

    def test_a_word_the_source_does_not_contain(self):
        assert not verify_quote("a single rotated password", SOURCE)

    def test_punctuation_is_not_blind(self):
        """Refused deliberately: it recovers nothing the markdown rung does not."""
        assert not verify_quote("read write on every table", SOURCE)

    @pytest.mark.parametrize("quote", ["", "   ", "…", "...", " … "])
    def test_a_quote_that_normalizes_away_matches_nothing(self, quote):
        """Otherwise the empty string would be the universal citation."""
        assert not verify_quote(quote, SOURCE)


class TestTheTranscriptShape:
    """Constructed from the measured export shape: ``Speaker: text``, CRLF."""

    TRANSCRIPT = (
        "Priya: we never got round to the MFA piece.\r\n"
        "Sam: so it's just the password today?\r\n"
        "Priya: just the password, yes.\r\n"
    )

    def test_a_quote_within_one_turn(self):
        assert verify_quote("we never got round to the MFA piece", self.TRANSCRIPT)

    def test_a_quote_across_adjoining_turns_keeping_the_labels(self):
        assert verify_quote(
            "Sam: so it's just the password today? Priya: just the password, yes.",
            self.TRANSCRIPT,
        )

    def test_a_quote_that_silently_drops_a_speaker_label(self):
        """Two turns welded into one utterance nobody spoke. Correctly rejected."""
        assert not verify_quote(
            "so it's just the password today? just the password, yes.",
            self.TRANSCRIPT,
        )


def test_normalization_is_applied_to_both_sides():
    """A rung applied to one side compares two dialects of the same string."""
    assert normalize("  The   `LEDGER`  service ") == "the ledger service"


class TestTheRepairRung:
    """Runs after the ladder refused; hands back the source's own words."""

    def test_a_tidied_quote_is_replaced_by_the_span_it_came_from(self):
        """A changed preposition. The span returned is the submitter's, and the
        ladder accepts it without help."""
        repair = repair_quote(
            "a single shared password from an environment variable", SOURCE
        )

        assert repair is not None
        span, similarity = repair
        assert span == "a single shared password out of an environment variable,"
        assert similarity >= REPAIR_THRESHOLD
        assert verify_quote(span, SOURCE)

    def test_a_dropped_word_is_repaired(self):
        """Six characters on forty-six are inside the threshold. The same drop
        on a quote half as long is not — a short quote has less slack."""
        repair = repair_quote("and that account has full read/write on table", SOURCE)
        assert repair is not None
        assert repair[0] == "and that account has full read/write on every table."
        assert repair_quote("has full read/write on table", SOURCE) is None

    def test_the_stitched_sentence_is_still_refused(self):
        """The corpus's one true fabrication. Every fragment is present, and
        the span it was cut from is thirteen words longer than the quote, so
        no candidate window comes near it."""
        assert repair_quote("a single shared password on every table", SOURCE) is None

    def test_a_quote_marking_a_cut_is_not_repaired(self):
        """Each fragment is its own span; one nearest window for the whole is a
        span the quote never claimed."""
        assert repair_quote("a single shared … every tables", SOURCE) is None

    def test_a_different_sentence_is_refused(self):
        assert (
            repair_quote("the accounts database is encrypted at rest", SOURCE) is None
        )

    def test_an_empty_quote_is_refused(self):
        assert repair_quote("   ", SOURCE) is None

    def test_a_scan_over_the_bound_is_refused_rather_than_run(self):
        """Both terms come from the submitted text, so the rung's cost is the
        submitter's to set unless something bounds it. Over the bound the
        answer is the one the threshold already gives when nothing is close
        enough, and the caller leaves the quote unverified."""
        width = 40
        words = ["word"] * (MAX_REPAIR_WORK // (width * width) + 1)
        quote = " ".join(reversed(words[:width]))

        assert repair_quote(quote, " ".join(words)) is None

    def test_the_bound_leaves_a_median_quote_on_the_largest_source(self):
        """100 KiB of ordinary prose is around 15,000 words, and the corpus
        median quote is 80 characters. The bound has to clear that pair and
        still repair it, or it has turned the rung off rather than bounded it.

        Asked of the rung rather than of the arithmetic. Two predictive metrics
        in a row read the cost of a scan off its inputs and got the order wrong
        -- English prose at 288M metric ran in 0.39 s while a repetitive source
        at 112M ran in 5.95 s -- because what costs the time is how many windows
        survive `quick_ratio`, which no function of the lengths can see.
        """
        source = " ".join(f"the {n} quick brown foxes jumped" for n in range(2500))
        quote = source[9000:9080].strip()

        assert repair_quote(quote.replace("quick", "quikc"), source) is not None

    def test_the_bound_counts_the_quote_in_characters(self):
        """A source of few very long words kept the word figure near zero while
        every window stayed thousands of characters wide, so the scan ran for
        minutes inside a bound reporting thousandths of a percent of its cap.

        Two words of five hundred characters are two words and a thousand
        characters; only the second figure predicts the time.
        """
        long_words = " ".join(["x" * 500] * 200)
        quote = " ".join(["y" * 500] * 2)

        assert repair_quote(quote, long_words) is None

    def test_folding_a_window_word_by_word_is_folding_it_whole(self):
        """The scan folds each source word once instead of once per window
        covering it. That is only the same comparison because the ladder's
        rungs are per-character and the last one collapses whitespace."""
        words = ["The", "`LEDGER`", "*service*", "runs"]

        assert " ".join(w for w in map(normalize, words) if w) == normalize(
            " ".join(words)
        )
