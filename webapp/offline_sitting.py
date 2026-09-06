"""The whole corpus as one file a reader opens in a browser.

``webapp/sitting.py`` is the sitting a maintainer holds, with a local server, a
clone and a command line. This writes the same sitting as a single standalone
HTML file, so a reader who has none of those can still do the one thing no lint
can do: read a case, and say whether its reference sets describe what could
actually go wrong.

The browser is the runtime, which is what makes this independent of the
operating system. Nothing is installed, nothing is signed, no per-platform build
exists, and the page opens from ``file://``. It is also the whole way out: the
publish button carries the reader to GitHub's editor with their submission
already filled in, so a reader with no clone opens their own pull request and
needs nobody to carry it for them. The payload is the whole corpus, at
about 440 KB of sources, models and reference sets. That is one ordinary email
attachment, so there is no reason to split it per case and every reason not to:
the rail, and with it the reader's own choice of what to sit, exists only over
the whole set.

It lays out the same blocks the other two surfaces do.
``evals.build_review_docs`` describes a case as blocks precisely so that a page
and a document cannot describe two different systems. This is the third renderer
over that description rather than a third description.

The rules it carries, in the words the app carries them:

* The own list comes first, per case. A case's recorded sets are not in the
  page's reach until that case's own list is written, and the list has to say at
  least ``MIN_OWN_LIST`` characters. This surface cannot enforce that the way
  the app does, because a static file holds its own payload, so a reader who
  opens the source can read the sets. #373 already ruled what the gate is for:
  it protects the evidence in the filled document rather than the reader. The
  import re-checks the same rule against the same constant.
* A case takes one own list. Once written, that case's list is fixed for the
  session. A list typed after the sets opened would be evidence of an order that
  did not happen, and days of reading is exactly when that becomes tempting.
* Marks, the missing list and the notes stay editable to the last minute.
  Nothing is a record until the operator imports it, so a reader who changes
  their mind on day four simply changes it.
* The page counts the own list and says nothing about the count, because a
  running total sets the length somebody writes to.

The envelope is the save file. Download it to stop, and load it to carry on.
That is one format for two jobs, and it means the page depends on no browser
storage: ``localStorage`` has no dependable origin on ``file://``.

Run ``uv run python webapp/offline_sitting.py --submitted-for anonymous``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from evals.harness.envelope import VERSION
from evals.harness.reference import (
    ANONYMOUS,
    CorpusError,
    corpus_refusal,
    is_submitted_for,
)
from webapp.page import client_script, script_json

REPO_ROOT = Path(__file__).resolve().parents[1]


def payload(corpus_dir: Path, submitted_by: str, submitted_for: str) -> dict:
    """Everything the page needs, taken from the corpus once.

    The digests are taken here, so they say what each required file held when
    the page was built — which is what the import compares against, and what
    tells the operator that a reader spent a week on words that have since
    changed.
    """
    cases = []
    for case in sittings.load_corpus(corpus_dir):
        case_dir = corpus_dir / case.meta.id
        prepared = sittings.prepare(case_dir)
        cases.append(
            {
                "case": prepared.case_id,
                "number": case.meta.id.split("-")[0],
                "title": prepared.title,
                "part_one": prepared.part_one_blocks,
                "part_two": prepared.part_two_blocks,
                "targets": [
                    {"fingerprint": target.fingerprint, "claims": list(target.claims)}
                    for target in prepared.mark_targets
                ],
                "digests": sittings.digests(case_dir, prepared.files),
            }
        )
    return {
        "envelope": VERSION,
        "submitted_by": submitted_by,
        "submitted_for": submitted_for,
        "generated": datetime.now(UTC).date().isoformat(),
        # Where the reader's pull request goes. Baked in when the page is
        # built, because the page is opened from a `file://` URL by somebody
        # who has no clone to read a remote from.
        "repo": submit_spine.repo_slug(REPO_ROOT),
        "branch": submit_spine.BASE_BRANCH,
        "submissions_dir": envelopes.SUBMISSIONS_DIR.as_posix(),
        "min_own_list": sittings.MIN_OWN_LIST,
        "marks": list(sittings.MARKS),
        "cases": cases,
    }


def build(corpus_dir: Path, submitted_by: str, submitted_for: str) -> str:
    """One standalone page, payload and all."""
    page = _PAGE.replace("<!--script-->", client_script("offline_sitting.js"))
    return page.replace(
        "__PAYLOAD__", script_json(payload(corpus_dir, submitted_by, submitted_for))
    )


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Case Sitting</title>
<style>
:root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1a1a18;--dim:#5c5c56;
--line:#dcdcd6;--card:#fff;--accent:#3a5a8c;--warn:#8a4b16}
@media (prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#e8e8e4;--dim:#9a9a94;
--line:#33333a;--card:#1e1e24;--accent:#8fb0e0;--warn:#d69a5c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
#shell{display:flex;min-height:100vh}
#rail{width:270px;flex:none;border-right:1px solid var(--line);padding:14px;
position:sticky;top:0;height:100vh;overflow:auto}
#stage{flex:1;padding:26px 34px;max-width:900px}
h1{font-size:18px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px}
h3{font-size:14px;margin:18px 0 6px;color:var(--dim)}
.sub{color:var(--dim);font-size:13px;margin:0 0 14px}
.row{display:block;width:100%;text-align:left;background:none;border:0;
padding:7px 8px;border-radius:6px;color:var(--fg);font:inherit;cursor:pointer}
.row:hover{background:var(--card)}
.row[aria-current=true]{background:var(--card);outline:1px solid var(--line)}
.row .t{font-size:13px}
.row .s{font-size:11px;color:var(--dim)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
margin-right:7px;background:var(--line)}
.dot.draft{background:var(--warn)}.dot.finished{background:var(--accent)}
blockquote{margin:0;padding:10px 14px;border-left:3px solid var(--line);
background:var(--card);white-space:pre-wrap;font-size:13px;overflow-x:auto}
table{border-collapse:collapse;margin:8px 0;font-size:13px;display:block;
overflow-x:auto}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left}
textarea{width:100%;min-height:150px;font:inherit;padding:9px;
border:1px solid var(--line);border-radius:6px;background:var(--card);
color:var(--fg)}
textarea[readonly]{opacity:.72}
button{font:inherit;padding:7px 13px;border-radius:6px;border:1px solid var(--line);
background:var(--card);color:var(--fg);cursor:pointer}
button.go{background:var(--accent);color:#fff;border-color:transparent}
button:disabled{opacity:.45;cursor:not-allowed}
.rec{border:1px solid var(--line);border-radius:8px;padding:11px 13px;
margin:10px 0;background:var(--card)}
.rec .meta{color:var(--dim);font-size:12px;margin-top:5px}
.marks{margin-top:8px;display:flex;gap:14px;flex-wrap:wrap;font-size:13px}
.marks label{cursor:pointer}
.bar{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);
padding:11px 0;margin-top:26px;display:flex;gap:10px;flex-wrap:wrap;
align-items:center}
.hint{color:var(--dim);font-size:13px}
</style></head><body>
<div id="shell">
  <nav id="rail"><h1>Cases</h1><p class="sub" id="who"></p><div id="rows"></div></nav>
  <main id="stage"></main>
</div>
<script><!--script--></script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submitted-by",
        help="the GitHub login that will open the pull request. Read from the"
        " authenticated `gh` when omitted.",
    )
    parser.add_argument(
        "--submitted-for",
        help="who will read: a GitHub login, or"
        f" {ANONYMOUS!r} for a reader who takes part on no name of their own."
        " Defaults to --submitted-by.",
    )
    parser.add_argument(
        "--out",
        default="sitting.html",
        help="where to write the page. Send this file to the reader.",
    )
    args = parser.parse_args(argv)

    try:
        login = submit_spine.gh_login(REPO_ROOT)
    except submit_spine.SubmitError:
        login = ""
    submitted_by = args.submitted_by or login
    if not submitted_by:
        print(
            "cannot read your gh login, so pass --submitted-by with the login"
            " that will carry the sitting",
            file=sys.stderr,
        )
        return 1
    submitted_for = args.submitted_for or submitted_by
    if not is_submitted_for(submitted_for):
        print(
            f"{submitted_for!r} is not a GitHub login and is not"
            f" {ANONYMOUS!r}; --submitted-for takes one of those two",
            file=sys.stderr,
        )
        return 1

    # Built before the file is opened, for the reason `webapp/sitting.py`
    # catches the same error: a corpus that does not parse is the operator's
    # to fix, and it names the case rather than a pydantic frame.
    try:
        page = build(sittings.CORPUS_DIR, submitted_by, submitted_for)
    except CorpusError as exc:
        print(corpus_refusal(exc), file=sys.stderr)
        return 1

    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    size = out.stat().st_size
    print(f"wrote {out} ({size // 1024} KB)")
    print(f"read by {submitted_for}, submitted by {submitted_by}")
    print(
        "\nSend that one file. The reader opens it in any browser, walks as"
        "\nmany cases as they choose, and sends back one JSON file. Then:"
        f"\n\n  python -m evals.harness.run sitting-import sitting-{submitted_by}.json"
        "\n  python -m evals.harness.run submit sitting"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
