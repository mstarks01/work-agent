"""The pinned quote-verification ladder.

Each rung is a policy decision, so each has a test naming what it forgives and
one naming what it still refuses. The two cases at the bottom are the real
ones: the corpus's single true rejection, and the transcript probe's — both the
same failure mode, an unmarked elision stitched into a sentence the source never
contains.
"""

import pytest

from stride_service.grounding import normalize, verify_quote

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
