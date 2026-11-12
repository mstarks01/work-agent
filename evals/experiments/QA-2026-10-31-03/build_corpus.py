"""The diagram arm's corpus of QA-2026-10-31-03-E1: a copy of the corpus in
which each case's one source is its ``evals/diagrams/<case>.mmd`` file, as a
``diagram`` source labelled ``System diagram``. Every other file is copied
unchanged, so each case is scored against the same blessed model. Run it as
``python build_corpus.py <out dir>``."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

CORPUS = Path("evals/corpus")
DIAGRAMS = Path("evals/diagrams")


def main(out: Path) -> None:
    """Write the diagram corpus into ``out``, replacing what is there."""
    shutil.rmtree(out, ignore_errors=True)
    for case in sorted(p for p in CORPUS.iterdir() if (p / "case.json").is_file()):
        copy = out / case.name
        shutil.copytree(case, copy)
        text = (DIAGRAMS / f"{case.name}.mmd").read_bytes()
        (copy / "diagram.mmd").write_bytes(text)
        meta = json.loads((copy / "case.json").read_text())
        meta["sources"] = [
            {
                "kind": "diagram",
                "label": "System diagram",
                "file": "diagram.mmd",
                "sha256": hashlib.sha256(text).hexdigest(),
            }
        ]
        (copy / "case.json").write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
