"""Layer 1 of the examples' honesty mechanism: the docs show the real code.

``examples/`` is the single source of truth for every code block in the prose.
This wraps ``examples/sync_docs.py`` the way ``tests/test_corpus_lints.py`` wraps
``evals/verify_corpus.py``: hand-runnable for authors, unbypassable in CI.

Note what this is *not*. It is a ``--write``/``--check`` pair over one-way
includes, so it covers only prose blocks generated from a named source region.
It says nothing about ``webapp/report_view.html``, which is a template rather
than prose and carries no checked-in report to keep in sync.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.sync_docs import IncludeError, doc_paths, extract, render

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_generated_block_matches_its_source():
    """The lint itself: no document has drifted from ``examples/``."""
    drifted = []
    for path in doc_paths():
        current = path.read_text(encoding="utf-8")
        if render(current) != current:
            drifted.append(str(path.relative_to(REPO_ROOT)))

    assert not drifted, (
        f"these documents have drifted from examples/: {drifted}. The code "
        f"blocks are generated — edit the file in examples/, then run "
        f"'uv run python examples/sync_docs.py --write'."
    )


def test_the_docs_actually_carry_generated_blocks():
    """Guards the guard: a lint over zero includes would pass vacuously."""
    included = [
        path for path in doc_paths() if "<!-- docs-include:" in path.read_text("utf-8")
    ]
    assert included, "no document declares an include — the lint covers nothing"


def test_the_sample_description_in_the_prose_is_the_file_the_app_loads():
    """The specific divergence #30 set out to make impossible."""
    guide = (REPO_ROOT / "docs" / "Integration-Guide.md").read_text(encoding="utf-8")
    sample = (REPO_ROOT / "examples" / "orders.md").read_text(encoding="utf-8")
    assert sample.strip() in guide


def test_the_prose_carries_no_hand_written_engine_snippet():
    """Every ``await engine.analyze`` a reader can copy comes from examples/.

    A hand-written call is exactly the shape that rots — ``README.md`` shipped a
    bare top-level ``await`` that could not run as written — so the rule is that
    such a line may only appear inside a generated block.
    """
    offenders = []
    for path in doc_paths():
        for chunk in path.read_text(encoding="utf-8").split("<!-- docs-include:"):
            body = chunk.split("<!-- /docs-include -->")[-1]
            if "engine.analyze(" in body:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"{offenders} call engine.analyze() outside a generated block. Put the "
        f"code in examples/ and include it."
    )


def test_a_region_drops_the_blank_lines_that_pad_its_end_marker():
    """A snippet ends where its code ends, not where the file's layout does.

    An end marker placed just above a top-level definition sits in the two
    blank lines the formatter requires there. They are the file's padding, not
    the snippet's, and rendering them puts dead space inside the fence.
    """
    source = (REPO_ROOT / "examples" / "embed.py").read_text(encoding="utf-8")
    assert "\n\n\n# docs-region-end: embed" in source, (
        "this test is vacuous unless the fixture's end marker is still padded"
    )

    body = extract("examples/embed.py", "embed")
    assert body == body.rstrip("\n") + "\n"


def test_a_missing_region_is_an_error_not_a_silent_empty_block():
    with pytest.raises(IncludeError, match="expected exactly one"):
        extract("examples/embed.py", "no-such-region")


def test_an_unreadable_source_is_an_error():
    with pytest.raises(IncludeError, match="cannot be read"):
        extract("examples/does-not-exist.py", None)
