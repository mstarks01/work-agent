"""Generate a Mermaid diagram of each corpus case, as extraction input.

A ``diagram`` Source holds Mermaid text. To measure how extraction reads one,
a case needs a diagram of the same system its description states. This writes
``diagrams/<case>.mmd`` from each case's blessed ``model.json`` through
:func:`~analysis_service.mermaid.model_mermaid`, the writer the report's
**Download as Mermaid** link uses. So the diagram states what the reference
model states and nothing more, and an extraction of it is scored against that
same model.

**A clean diagram.** The text uses the export's conventions: a trust zone is a
subgraph labelled as one, and each arrow names its flow and its protocol. A
person draws more loosely, so a score on these files is an upper bound on what
extraction reads from a diagram.

**Only the cases a diagnosis may read.** A holdout case gets no diagram, because
the diagram is its reference model in another form (:func:`development_cases`).

The files live outside ``corpus/``, because the corpus digest reads every file
under it. ``tests/test_corpus_diagrams.py`` holds each file against what this
writes. Run ``python evals/build_diagrams.py``. It is offline.
"""

from __future__ import annotations

from pathlib import Path

from analysis_service.mermaid import model_mermaid
from evals.harness.reference import development_cases, load_corpus

EVALS = Path(__file__).resolve().parent
CORPUS = EVALS / "corpus"
DIAGRAMS = EVALS / "diagrams"


def diagrams(corpus_dir: Path = CORPUS) -> dict[str, str]:
    """The Mermaid text of each case a diagnosis may read, by case ID."""
    return {
        case.meta.id: model_mermaid(case.model)
        for case in development_cases(load_corpus(corpus_dir))
    }


def main() -> None:
    """Write each diagram, and remove a file whose case no longer has one."""
    written = diagrams()
    DIAGRAMS.mkdir(exist_ok=True)
    for stale in DIAGRAMS.glob("*.mmd"):
        if stale.stem not in written:
            stale.unlink()
    for case_id, text in written.items():
        (DIAGRAMS / f"{case_id}.mmd").write_text(text, encoding="utf-8")
    print(f"wrote {len(written)} diagrams to {DIAGRAMS}")


if __name__ == "__main__":
    main()
