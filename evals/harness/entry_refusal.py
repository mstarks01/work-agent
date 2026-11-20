"""The one-line refusal of a command line that loads the corpus or a
configuration.

Each entry point that loads the golden corpus catches :data:`ENTRY_ERRORS`
and returns :func:`refuse_entry`, so its refusal is written once.
"""

from __future__ import annotations

import sys

from analysis_service.errors import ConfigError, config_refusal
from evals.harness.reference import CorpusError


def corpus_refusal(error: CorpusError) -> str:
    """The one line a command line prints when the corpus does not load.

    Every entry point that reads the corpus refuses the same way, so the
    sentence is written here once rather than in each ``main``. The error
    already names the case, the file and the field it stopped on. A traceback
    in its place names a pydantic frame, which is nothing the reader whose own
    case carries the bad entry can act on.
    """
    return f"cannot read the corpus: {error}"


#: What an entry point refuses in one line through :func:`refuse_entry`: a
#: corpus that does not load, and a configuration this process cannot run on.
ENTRY_ERRORS = (CorpusError, ConfigError)


def refuse_entry(error: CorpusError | ConfigError) -> int:
    """Print the one line for ``error`` on stderr, and give exit status 1.

    **The one refusal of an entry point that loads the corpus or a
    configuration.** Each one catches :data:`ENTRY_ERRORS` and returns this.
    """
    if isinstance(error, CorpusError):
        print(corpus_refusal(error), file=sys.stderr)
    else:
        print(config_refusal(error), file=sys.stderr)
    return 1
