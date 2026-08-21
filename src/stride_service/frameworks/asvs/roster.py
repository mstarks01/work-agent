"""The chapter roster inside a lane skill, composed from the catalog.

**The roster is derived text that lives in a hand-written file.** Each lane
skill states every requirement of its chapter — the identifier, the level and
the published description — because that is how the requirements reach a
``strong``-tier prompt. Written by hand, that is a second copy of the standard
sitting beside the one in ``catalog.json``, and the copy nobody generates is
the copy that drifts.

So it is generated. :func:`roster_block` composes the block and
:func:`write_rosters` writes it into each skill in place, between the heading
it owns and the next ``##``. Everything else in a skill — the scope, the
applicability reasoning, the threat patterns, the guardrails — stays
hand-written, because none of it is derivable and all of it is judgement.

``tests/test_asvs.py`` asserts the file on disk equals what this composes, so
a hand edit to the roster fails the suite rather than shipping. That is the
whole point: the earlier checks caught a *wrong* roster, and this makes a
divergent one unrepresentable.

Regenerate after a catalog change::

    python -m stride_service.frameworks.asvs.roster

Build-time only. Nothing in a run imports this; the graph reads the finished
skill off disk like every other prompt document.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from stride_service.frameworks.asvs.catalog import CHAPTERS, REQUIREMENTS

__all__ = ["ROSTER_HEADING", "replace_roster", "roster_block", "write_rosters"]

#: The H3 the roster owns. An H3 rather than one of the skill's five fixed H2
#: sections because it sits *inside* ``Applicability``: which requirements a
#: chapter carries is part of what decides whether the chapter reaches a system.
ROSTER_HEADING = "### The requirements of this chapter"

#: How to read the block, stated once at the top of every chapter's roster. The
#: counts are computed rather than written, which is the third copy of the
#: catalog this module removes: they were correct in all 17 skills and nothing
#: checked them.
_PREAMBLE = (
    "{total} requirements across {sections} sections: {level_1} at level 1,"
    " {level_2} at level 2, {level_3} at level 3. Rule on every one at or below"
    " the level the scope line names, and on no other. The `(L…)` tag is the"
    " requirement's own level, and the pair after the chapter number is what you"
    " put in `requirement`."
)


def roster_block(lane: str) -> str:
    """One chapter's roster, from the heading to the last requirement line.

    Sections appear in catalog order, which is the standard's own, and a
    requirement's text is the catalog's verbatim. No wrapping and no
    paraphrase: what the agent rules against has to be what the standard
    published, and the only way to be sure of that is to copy it whole.
    """
    requirements = [entry for entry in REQUIREMENTS if entry.lane == lane]
    levels = Counter(entry.level for entry in requirements)
    sections = list(
        dict.fromkeys((entry.section, entry.section_name) for entry in requirements)
    )

    lines = [
        ROSTER_HEADING,
        "",
        _PREAMBLE.format(
            total=len(requirements),
            sections=len(sections),
            level_1=levels[1],
            level_2=levels[2],
            level_3=levels[3],
        ),
        "",
    ]
    for section_id, section_name in sections:
        lines += [f"#### {section_id} {section_name}", ""]
        lines += [
            f"- **{entry.id}** (L{entry.level}) — {entry.text}"
            for entry in requirements
            if entry.section == section_id
        ]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def replace_roster(skill: str, block: str) -> str:
    """``skill`` with its roster replaced, or raise if it carries none.

    **Pure**, and public for that reason: it is what lets a test ask whether the
    tree is already generated without writing to the tree to find out. A check
    that answered by calling :func:`write_rosters` would repair what it was
    meant to report, and pass on the re-run.

    Bounded by the heading and the next ``##`` rather than by a line count, so
    the hand-written sections after it are untouched however they grow. Raising
    on a missing heading is the same fail-loud
    :func:`~webapp.main.render_report` takes for its injection point: a skill
    this cannot find its way into is a skill that would silently keep a stale
    roster.
    """
    start = skill.find(ROSTER_HEADING)
    if start < 0:
        raise ValueError(f"no {ROSTER_HEADING!r} heading to write the roster into")
    end = skill.find("\n## ", start)
    if end < 0:
        raise ValueError(f"no section follows {ROSTER_HEADING!r}")
    # ``block`` ends in exactly one newline and the next section wants a blank
    # line before it, so the separator is written here rather than carried on
    # the block -- which keeps ``roster_block`` the roster and nothing else.
    return skill[:start] + block + "\n" + skill[end + 1 :]


def write_rosters(lanes_dir: Path) -> list[str]:
    """Rewrite every chapter's roster in place. Returns the lanes that changed."""
    changed = []
    for chapter in CHAPTERS:
        path = lanes_dir / chapter.lane / "skill.md"
        before = path.read_text(encoding="utf-8")
        after = replace_roster(before, roster_block(chapter.lane))
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed.append(chapter.lane)
    return changed


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[4] / "frameworks" / "asvs" / "lanes"
    written = write_rosters(root)
    print(f"rewrote {len(written)} roster(s): {', '.join(written) or 'none'}")
