"""The voter roster: who may vote, hold a sitting or submit a sweep, and with what standing.

``evals/review/voters.toml`` is one checked-in table, mapping a GitHub login to
a **Standing** from the closed set ``maintainer`` and ``contributor``. It is the
only place a standing lives (#320). A standing on a vote row would freeze a
classification into history. A roster edit re-classes a voter's whole series at
once, which is what makes a promotion one diff (#326). Any past number stays
recomputable from the ledger plus the roster at that commit.

A renamed account keeps one series. Old rows stay under the old login in
``evals/review/votes/``, the roster line gains the old login in ``aliases``, and
:meth:`Roster.resolve` folds the history back onto the current name. No row is
ever rewritten.

On security: the roster decides which standing series a vote lands in, so the
loader fails closed on anything it does not recognise (A10). That covers an
unknown standing, an alias claimed twice, and a key nobody defined. A voter
absent from the roster is an error at the point of the question, and never a
silent default (A01, deny by default).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER_PATH = EVALS_ROOT / "review" / "voters.toml"

#: The file's contract version. A shape change bumps it, and an old file fails
#: the load rather than inheriting a meaning it never chose — the same hard
#: cutover every config file in this repository follows.
VERSION = 1

Standing = Literal["maintainer", "contributor"]

#: The closed set of standings. Every published number states which of these
#: its series includes; there is no third class and no per-voter weight —
#: standing selects which votes a series reads, never how much a vote counts.
STANDINGS: frozenset[str] = frozenset({"maintainer", "contributor"})

#: The same login shape the ledger enforces on ``voter``, spelled here too so
#: the roster refuses a key that could never name a vote file.
_LOGIN = re.compile(r"(?=.{1,39}\Z)[A-Za-z0-9](?:-?[A-Za-z0-9])*\Z")


class RosterError(ValueError):
    """The roster is malformed, or a voter it should name has no line."""


@dataclass(frozen=True)
class Roster:
    """The table as loaded: standings by login, and old logins by alias."""

    standings: dict[str, Standing]
    aliases: dict[str, str]
    path: Path | None = None

    def resolve(self, voter: str) -> str:
        """The current login for a voter name, following a rename."""
        return self.aliases.get(voter, voter)

    def standing_of(self, voter: str) -> Standing:
        """The voter's standing, or a refusal — never a default.

        A voter with no roster line cannot be classed, and classing them
        silently as anything would move a published series without a diff
        saying so.
        """
        login = self.resolve(voter)
        if login not in self.standings:
            raise RosterError(
                f"{voter!r} has no line in the roster; every voter the ledger"
                " names must have one (#320)"
            )
        return self.standings[login]

    def __contains__(self, voter: str) -> bool:
        return self.resolve(voter) in self.standings


def load(path: Path | str = DEFAULT_ROSTER_PATH) -> Roster:
    """Read the roster, refusing anything it does not recognise."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RosterError(f"{path}: cannot be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RosterError(f"{path}: invalid TOML: {exc}") from exc

    if unknown := set(raw) - {"version", "voters"}:
        raise RosterError(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
    if raw.get("version") != VERSION:
        raise RosterError(
            f"{path}: version {raw.get('version')!r} is not {VERSION}; an old"
            " shape fails the load rather than inheriting a meaning"
        )
    voters = raw.get("voters")
    if not isinstance(voters, dict) or not voters:
        raise RosterError(f"{path}: names no voters; a roster of nobody is a misedit")

    standings: dict[str, Standing] = {}
    aliases: dict[str, str] = {}
    for login, entry in voters.items():
        standings[login] = _standing(path, login, entry)
        for alias in _aliases(path, login, entry):
            if alias in aliases:
                raise RosterError(
                    f"{path}: alias {alias!r} is claimed by both"
                    f" {aliases[alias]!r} and {login!r}"
                )
            aliases[alias] = login

    if taken := set(aliases) & set(standings):
        raise RosterError(
            f"{path}: {', '.join(sorted(taken))} is both a login and an alias;"
            " an alias names a login that no longer exists"
        )
    return Roster(standings=standings, aliases=aliases, path=path)


def _standing(path: Path, login: str, entry: object) -> Standing:
    if not _LOGIN.match(login):
        raise RosterError(f"{path}: {login!r} is not a GitHub login")
    if not isinstance(entry, dict):
        raise RosterError(f"{path}: [voters.{login}] is not a table")
    if unknown := set(entry) - {"standing", "aliases"}:
        raise RosterError(
            f"{path}: [voters.{login}] has unknown keys: {', '.join(sorted(unknown))}"
        )
    standing = entry.get("standing")
    if standing not in STANDINGS:
        raise RosterError(
            f"{path}: [voters.{login}] standing {standing!r} is not one of"
            f" {', '.join(sorted(STANDINGS))}"
        )
    return cast(Standing, standing)


def _aliases(path: Path, login: str, entry: dict[str, object]) -> list[str]:
    raw = entry.get("aliases", [])
    if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
        raise RosterError(f"{path}: [voters.{login}] aliases is not a list of logins")
    for alias in raw:
        if not _LOGIN.match(alias):
            raise RosterError(
                f"{path}: [voters.{login}] alias {alias!r} is not a GitHub login"
            )
    return raw
