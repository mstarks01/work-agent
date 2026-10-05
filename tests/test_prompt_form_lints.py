"""A prompt that lists the forms of an open fact lists every one.

``UnknownRef`` holds one open fact in one of several forms in one flat shape,
so the provider schema cannot say that an entry uses one. The prompts say it.
Three prompts list the forms, and two of them drifted: the ASVS critic addendum
named two of four, and its critic wrote two forms at once on 29 of 203 rulings
(#1476). The forms come from the provider schema, so a form added tomorrow is
asked of every list.
"""

from __future__ import annotations

from pathlib import Path

from analysis_service.claims import UnknownRef

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Each form names its own field; ``element_id`` goes with two of them.
FORMS = sorted(set(UnknownRef.model_json_schema()["properties"]) - {"element_id"})


def _lists() -> list[tuple[Path, str]]:
    """Every prompt paragraph that names ``element_id`` and two or more forms."""
    prompts = [*REPO_ROOT.glob("prompts/*.md"), *REPO_ROOT.glob("frameworks/**/*.md")]
    return [
        (path, paragraph)
        for path in sorted(prompts)
        for paragraph in path.read_text(encoding="utf-8").split("\n\n")
        if "`element_id`" in paragraph
        and sum(f"`{form}`" in paragraph for form in FORMS) >= 2
    ]


def test_every_list_of_the_forms_names_them_all():
    short = {
        f"{path.relative_to(REPO_ROOT)}: {paragraph[:60]!r}": [
            form for form in FORMS if f"`{form}`" not in paragraph
        ]
        for path, paragraph in _lists()
        if not all(f"`{form}`" in paragraph for form in FORMS)
    }
    assert not short, f"these lists of the forms omit some: {short}"


def test_the_shared_critic_prompt_lists_the_forms():
    """The positive control: the lint reads the list it exists for."""
    assert any(
        path.name == "critic.md" and path.parent.name == "prompts"
        for path, _ in _lists()
    )
