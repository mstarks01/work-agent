"""The roster decides which standing series a vote lands in.

So the properties tested here are the ones a promotion or a dispute would ask
about: an unknown voter is refused rather than defaulted, a rename keeps one
series, and the loader recognises nothing it was not taught. The last tests
read the checked-in tree, because a roster that drifts from the ledger fails
as quietly as any table nobody compares to its registry.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from evals.harness import ledger
from evals.harness.roster import (
    _LOGIN,
    DEFAULT_ROSTER_PATH,
    STANDINGS,
    RosterError,
    load,
)


def write(tmp_path, body):
    path = tmp_path / "voters.toml"
    path.write_text(body, encoding="utf-8")
    return path


GOOD = """
version = 1

[voters.mstarks01]
standing = "maintainer"

[voters.ada]
standing = "contributor"
aliases = ["ada-prior"]
"""


def test_standings_by_login(tmp_path):
    roster = load(write(tmp_path, GOOD))
    assert roster.standing_of("mstarks01") == "maintainer"
    assert roster.standing_of("ada") == "contributor"
    assert "ada" in roster


def test_a_rename_keeps_one_series(tmp_path):
    """Old rows stay under the old login; the roster folds them back."""
    roster = load(write(tmp_path, GOOD))
    assert roster.resolve("ada-prior") == "ada"
    assert roster.standing_of("ada-prior") == "contributor"
    assert "ada-prior" in roster


def test_an_unrostered_voter_is_refused_not_defaulted(tmp_path):
    """Classing a stranger silently would move a published series without a diff."""
    roster = load(write(tmp_path, GOOD))
    with pytest.raises(RosterError, match="no line in the roster"):
        roster.standing_of("nobody")
    assert "nobody" not in roster


def test_an_unknown_standing_is_refused(tmp_path):
    path = write(tmp_path, 'version = 1\n[voters.ada]\nstanding = "admin"\n')
    with pytest.raises(RosterError, match="is not one of"):
        load(path)


def test_a_missing_standing_is_refused(tmp_path):
    path = write(tmp_path, "version = 1\n[voters.ada]\naliases = []\n")
    with pytest.raises(RosterError, match="is not one of"):
        load(path)


def test_a_wrong_version_is_refused(tmp_path):
    path = write(tmp_path, 'version = 2\n[voters.ada]\nstanding = "contributor"\n')
    with pytest.raises(RosterError, match="version"):
        load(path)


def test_unknown_keys_are_refused_at_both_levels(tmp_path):
    top = write(
        tmp_path, 'version = 1\nowner = "x"\n[voters.ada]\nstanding = "contributor"\n'
    )
    with pytest.raises(RosterError, match="unknown keys"):
        load(top)
    entry = write(
        tmp_path, 'version = 1\n[voters.ada]\nstanding = "contributor"\nemail = "x"\n'
    )
    with pytest.raises(RosterError, match="unknown keys"):
        load(entry)


def test_a_roster_of_nobody_is_refused(tmp_path):
    with pytest.raises(RosterError, match="names no voters"):
        load(write(tmp_path, "version = 1\n"))


def test_a_missing_roster_is_an_error_not_an_empty_table(tmp_path):
    """Deny by default: no roster means no standings, not open doors."""
    with pytest.raises(RosterError, match="cannot be read"):
        load(tmp_path / "absent.toml")


def test_an_alias_claimed_twice_is_refused(tmp_path):
    body = """
version = 1
[voters.ada]
standing = "contributor"
aliases = ["old"]
[voters.sam]
standing = "contributor"
aliases = ["old"]
"""
    with pytest.raises(RosterError, match="claimed by both"):
        load(write(tmp_path, body))


def test_an_alias_that_is_also_a_login_is_refused(tmp_path):
    body = """
version = 1
[voters.ada]
standing = "contributor"
aliases = ["sam"]
[voters.sam]
standing = "contributor"
"""
    with pytest.raises(RosterError, match="both a login and an alias"):
        load(write(tmp_path, body))


def test_a_login_or_alias_that_is_not_a_login_shape_is_refused(tmp_path):
    bad_key = write(
        tmp_path, 'version = 1\n[voters."../x"]\nstanding = "contributor"\n'
    )
    with pytest.raises(RosterError, match="is not a GitHub login"):
        load(bad_key)
    bad_alias = write(
        tmp_path,
        'version = 1\n[voters.ada]\nstanding = "contributor"\naliases = ["a b"]\n',
    )
    with pytest.raises(RosterError, match="is not a GitHub login"):
        load(bad_alias)


def test_the_standings_set_matches_the_context_glossary():
    """CONTEXT.md's Standing entry and the code carry the same closed set."""
    assert STANDINGS == {"maintainer", "contributor"}


class TestTheCheckedInTree:
    """The live roster and the live ledger, held to each other."""

    def test_the_checked_in_roster_loads(self):
        assert load(DEFAULT_ROSTER_PATH).standings

    def test_every_voter_the_ledger_names_has_a_roster_line(self):
        """#320's completeness check, over the tree a PR would merge."""
        roster = load(DEFAULT_ROSTER_PATH)
        votes = ledger.load(ledger.DEFAULT_LEDGER_PATH)
        unrostered = sorted({vote.voter for vote in votes if vote.voter not in roster})
        assert not unrostered, f"voters with no roster line: {unrostered}"


class TestTheRosterAndTheLedgerAgreeOnALogin:
    """One rule, one reader — asserted against each other, not each alone.

    A roster key names a voter, and a ledger ``voter`` names that voter's file.
    A key the roster admits and the ledger refuses is a line nobody can vote
    from; a key the ledger admits and the roster refuses is a vote from a voter
    with no standing. Both spelled the same regex, so each one's own test
    agreed with it and neither test could see a disagreement.
    """

    @pytest.mark.parametrize(
        "login",
        [
            "octocat",
            "a",
            "a-b-c",
            "0",
            "a" * 39,
            # Every shape a GitHub login is not.
            "",
            "-leading",
            "trailing-",
            "double--hyphen",
            "has space",
            "has/slash",
            "..",
            "a" * 40,
        ],
    )
    def test_the_two_readers_answer_identically(self, login):
        assert bool(_LOGIN.fullmatch(login)) is bool(
            ledger.GITHUB_LOGIN.fullmatch(login)
        )

    def test_they_are_one_object_rather_than_two_that_match(self):
        # Equal answers on a list somebody wrote is weaker than one rule: the
        # list is what a second copy drifts past.
        assert _LOGIN is ledger.GITHUB_LOGIN
