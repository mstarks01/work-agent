"""The Case Sitting app: read a case, judge its reference sets, record it.

Run it from a clone, with no credentials of any kind::

    uv run python webapp/sitting.py

**The app offers the whole corpus.** A rail on the left lists every case with
a status dot, the case number and the title, and it never leaves — so it is
both how a reader starts and how they get back, and no control returns to a
list that never went away. A row carries no claim count and no reason the case
waits, because either would tell the reader how long to make their own list
before they have written it. ``--case`` preselects where the rail opens and
grants nothing.

**A case a sitting already clears is greyed, whoever signed it, and is off the
offered list.** :func:`_open` resolves every case id that arrives in a request
against that list, so the refusal a signed case needs is the same rule that
refuses a case id nobody wrote. The status reads
:func:`evals.harness.sitting.clears` and never the presence of an entry in
``reviews``: a drifted digest leaves an entry that clears nothing, and a rail
keyed on the entry would grey a case CI asks somebody to read.

It is **eval-side tooling and not the product**, and it is the browser half of
a path that also works entirely from the shell: everything it writes is what
``evals/BLESSING.md`` step 6 asks a person to write by hand, and
``submit sitting`` checks the result the same way either way. It prepares your
working tree, and then offers three ways out — run the command yourself, paste
the text into a pull request you open, or press the button.

**The button opens a pull request, through your own authenticated ``gh``.**
That reverses map #319's "the web app never gains a network write", on the
maintainer's instruction and recorded in ``docs/agents/issue-tracker.md``.
It is not a hosted service: nothing is hosted, no credential is held here, and
the app still binds to loopback. What it does mean is that a request reaching
:func:`create_app`'s submit endpoint can act on GitHub as you, so that one
endpoint carries five controls rather than the app's usual none:

* the **Host** check every loopback app here runs, which is what stops DNS
  rebinding making an attacker's page same-origin with this one;
* **``frame-ancestors 'none'``**, which is what makes the next two mean
  anything. A press inside somebody else's frame arrives same-origin and
  carries the page token, because the page it comes from really is this one.
  So framing is the way past both, and refusing to be framed is the answer;
* **``Sec-Fetch-Site: same-origin``**, the check ``webapp/main.py`` uses on its
  own write endpoint, through the ``refuse_cross_origin`` both apps share;
* a **one-time token** minted per process and embedded in the page, so a
  request that never read the page cannot carry it;
* **no request-controlled arguments at all** — the endpoint opens the one
  submission this session prepared, and takes neither a kind nor a path.

**Every writing endpoint carries the origin check, not only the submit one.**
``/api/finish`` writes the reading document, appends to ``case.json`` and sets
the flag ``/api/submit`` tests, so a foreign page that reaches it decides what
a later press publishes. ``/api/own-list`` satisfies the method's one rule, so
a foreign page that reaches it opens the recorded sets for whoever asks next.
It carries the page token too, because it names a case: one such page would
post an empty list for every case in the offered list and open the whole
corpus in one pass. ``/api/draft`` and ``/api/discard`` carry both for those
two reasons and one more: they write under ``~/.local/state/``, so their
effect outlives the process. ``/api/part-two`` gains no token — it is a read,
it serves only what a passed gate already opened, and the frame rule covers
the page that would read it.

**The own list comes first, and the server enforces it.** ``/api/part-two``
refuses until the reader has posted their own threat list for that case, so
the recorded sets are not in the page at all. **What the gate protects is the
evidence in the filled document.** That document prints the reader's own list
above the recorded sets, and a later reader takes the order on trust. The gate
makes the order true by construction, so the trust is earned rather than
asked for. It is enforced the way the review app enforces
configuration-blindness — by the payload not carrying it — rather than by
asking.

**The gate re-arms per case, and a case takes one own list.** Both halves are
the same rule read two ways: a case's sets open once that case's own list
exists. So a reader who reaches a case they have not written for reaches it
blind, and a case they already wrote for re-opens with the list they wrote and
refuses a second one. Without that refusal the reader could open a case's
sets, come back to it, and post a list the document would then print above
them — evidence of an order that did not happen.

**The reader walks the corpus, and Previous and Next sit in the stage header.**
They step in the rail's order over the cases the reader may open, and the last
Next lands on the submit stage. The rail never leaves, so the walk reloads no
document: every arrival reads the case back from the server.

**A part-finished read survives the browser and the process.** It lives in a
**Draft Sitting**, one file per case under the reader's own GitHub login,
outside the repository, and it never merges — which is what keeps an unsigned
own list out of a pull request. The own list creates it, so a reader who reads
part one of ten cases and writes nothing leaves no trace. The page holds no
copy of what they wrote: the own list, the marks, the missing list and the
notes go to the draft as they are written, and a case comes back saying what
the file on disk says. The reader stops the app, runs it again tomorrow, and
finds the case where they left it. They may also throw one draft away, which
puts that case back on the list to do and changes nothing in the repository.

**The app says so when the text moved under an open draft.** The draft pins
the digest of every required file as the reader opened it, and that pin is
what makes the warning honest: without it the app could say only that a file
differs from what a recorded sitting signed, which answers a different
question. The check runs at open, because a reader needs to know before they
spend an hour on the case, and again at finish, because a file can move while
the tab sits open. The recorded entry signs the digests taken at finish, so it
signs the bytes that will merge. The reader keeps their own list either way,
and judges whether it still answers the text; nothing here judges that for
them.

**A hand-edited draft costs nothing this project can price, and nothing
notices it.** A reader who types their own list into that file by hand wrote a
list, which is the whole of what the filled document claims. A timestamp pair
on the draft is rejected for the same reason: the reader owns the file, so
they can write the timestamps too. The gate's job is to make the ordinary path
the correct path, not to police the person at the keyboard.

**A draft the app cannot read refuses its own case, names the file, and
changes nothing on disk** (A10). The rail shows that one row in an error
state, and every other case still walks. Two alternatives are rejected. To
treat it as absent throws the reader's own list away and re-arms the gate, so
they retype a list they already wrote and never learn the first one existed.
To repair it writes a guess into the one file the reader owns.

Security posture, inherited from ``webapp/review.py`` rather than re-derived:

* **Loopback only, and a checked ``Host``** (A01). Binding alone does not stop
  a rebound page in the operator's own browser, and this app writes to the
  corpus.
* **The reviewer comes from the command line, never from the request** (A01).
  A browser field naming the reviewer would let one person file a sitting as
  another, and #320's binding rests on the name being true.
* **Case prose reaches the page as data** (LLM05, A05). The document and each
  recorded claim are injected as JSON the client renders through
  ``textContent``.
* **A mark is an allow-list, checked here** (A05). The value is the method's
  closed set of three, and the key must name a recorded finding of this case,
  so a request cannot write a sentence of its own into the evidence.
* **Every write lands inside one offered case's directory** (A01), plus the
  unreviewed list. A request names a case id and never a path, and the id is
  resolved against the offered list by allow-list, so a case the rail greys
  and a case nobody wrote refuse the same way.
* **A draft is filed under a login and a case id, and both are checked as
  path segments** (A01). The case id arrives in a request, and a value
  carrying a separator would write outside the store.
* **The submit endpoint is off unless ``gh`` is authenticated** — with no
  login there is nothing to act as, so the button is never offered.
"""

from __future__ import annotations

import argparse
import html
import json
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness import roster as rosters
from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from evals.harness.roster import Roster
from webapp.main import LOOPBACK_HOSTS, SecurityHeaders, refuse_cross_origin

HOST = "127.0.0.1"
PORT = 8020

_NONCE_PLACEHOLDER = "__CSP_NONCE__"

#: ``frame-ancestors 'none'`` is the fifth control on the submit path, and it
#: is what makes the other four mean anything. A press inside somebody else's
#: frame reaches this app as same-origin and carries the page token, because
#: the page it comes from really is this one — so framing beats the header
#: check and the token together. The directive is spelled out because it does
#: not fall back to ``default-src``, as ``base-uri`` and ``form-action`` beside
#: it do not.
_CSP = (
    "default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}';"
    " connect-src 'self'; base-uri 'none'; form-action 'none';"
    " frame-ancestors 'none'"
)


#: One written line, wherever this app takes a list of them. A cap on the list
#: alone bounds how many lines arrive and not how long one is, so 200 items of
#: no stated length is no bound at all — and every one of them is written into
#: the reading document, which the submit allow-list then carries into a pull
#: request. Generous for a line somebody types, and finite.
Line = Annotated[str, Field(max_length=500)]

#: A case id as it arrives in a request. The bound is here so an oversized
#: string is refused before anything reads it; which ids exist is not a shape
#: question, and :func:`_open` answers it against the offered list.
CaseId = Annotated[str, Field(max_length=120)]


class Which(BaseModel):
    """A case and nothing else, for a write whose whole argument is which one."""

    model_config = ConfigDict(extra="forbid")

    case: CaseId


class OwnList(BaseModel):
    """What the reader saw for themselves, before the sets were shown."""

    model_config = ConfigDict(extra="forbid")

    case: CaseId
    items: list[Line] = Field(default_factory=list, max_length=200)


class Progress(BaseModel):
    """The reader's work in one case: a mark per recorded finding, and their own.

    Both ``/api/draft`` and ``/api/finish`` take it. Saving and recording ask
    for the same thing, so a second model would be the same fields under a
    second name and a chance for the two to drift.
    """

    model_config = ConfigDict(extra="forbid")

    case: CaseId

    #: Keyed by the finding's fingerprint, which the page reads off
    #: ``/api/part-two``. The value is the method's closed set, so a mark this
    #: app records is a mark a count can be taken over; the key is bounded like
    #: every other written line, and
    #: :func:`evals.harness.sitting.document` refuses one that names no
    #: recorded finding of this case.
    marks: dict[Line, sittings.Mark] = Field(default_factory=dict, max_length=200)
    missing: list[Line] = Field(default_factory=list, max_length=200)
    notes: str = Field(default="", max_length=4000)


@dataclass
class Session:
    """One reader over the whole corpus: who is reading, and how far they got."""

    root: Path
    reviewer: str
    #: Read once from the clone the reader is in, because the rail's status
    #: asks the clearing rule and that rule asks who is rostered.
    roster: Roster
    #: Where this reader's **Draft Sitting**s live. A field the caller sets
    #: rather than a path this module resolves, so a test points it at a
    #: temporary directory and no test writes into a real home directory.
    drafts: Path
    #: The case ``--case`` named, or ``None``. It moves the rail and grants
    #: nothing: the endpoints resolve against :attr:`offered`, which a
    #: preselect does not join.
    preselect: str | None = None
    #: The rail as the tree last stood. :meth:`refresh` re-reads it, and
    #: nothing else writes it, so the offered list moves only when the tree
    #: does.
    rows: tuple[sittings.Row, ...] = ()
    #: One prepared case per case the reader opened. Preparing reads a whole
    #: case directory, and a reader opens a few of thirteen.
    prepared: dict[str, sittings.Prepared] = field(default_factory=dict)
    #: Minted per process and embedded in the page. The submit endpoint
    #: requires it, so a request that never read the page cannot carry it —
    #: and reading the page cross-origin is what the Host and Sec-Fetch-Site
    #: checks already refuse.
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    #: Whether a `gh` login is available to act as. With none there is nothing
    #: to submit with, so the button is never offered.
    can_submit: bool = False
    recorded: set[str] = field(default_factory=set)

    @property
    def document_name(self) -> str:
        """Spelled in :mod:`evals.harness.sitting`, because ``submit
        sitting`` admits this name under the case prefix and no other."""
        return sittings.document_name(self.reviewer)

    @property
    def corpus_dir(self) -> Path:
        return self.root / "evals" / "corpus"

    @property
    def offered(self) -> frozenset[str]:
        """The cases this session may open — the rail rows that press.

        A case the rail greys is off this set, so the refusal a signed case
        needs is the same rule that refuses a case id nobody wrote, and it
        costs no check of its own.
        """
        return frozenset(row.case_id for row in self.rows if row.pressable)

    def refresh(self) -> tuple[sittings.Row, ...]:
        """Re-read the rail from the tree and the store, and with it the offered list.

        A recorded sitting greys its own case and a posted own list moves its
        case to *draft in progress*, so the page asks for this after every
        write as well as at load.
        """
        self.rows = sittings.rail(
            self.corpus_dir,
            self.roster,
            sittings.draft_states(self.drafts, self.reviewer),
        )
        return self.rows

    def draft(self, case_id: str) -> sittings.Draft | None:
        """This reader's draft of one case, read from disk on every ask.

        Never held in memory. The draft is the one record of a part-finished
        read, and a copy in the process could disagree with the file the
        reader owns.
        """
        return sittings.load_draft(self.drafts, self.reviewer, case_id)

    def prepare(self, case_id: str) -> sittings.Prepared:
        """One case, prepared once and kept for as long as the process runs."""
        if case_id not in self.prepared:
            self.prepared[case_id] = sittings.prepare(self.corpus_dir / case_id)
        return self.prepared[case_id]


def create_app(session: Session) -> FastAPI:
    """The sitting app: the rail over the whole corpus, one case on the stage."""
    app = FastAPI(title="STRIDE case sitting", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return _html(
            _page(
                _PAGE,
                reviewer=_escape(session.reviewer),
                token=_js_literal(session.token),
                cansubmit=_js_literal(session.can_submit),
            )
        )

    @app.get("/api/rail")
    def rail() -> JSONResponse:
        """The whole corpus, each case with the status the reader reads.

        Re-read from the tree on every call, because a recorded sitting greys
        its own case and the reader must see that without a restart. It
        carries no claim count and no reason a case waits: a number here would
        tell the reader how long to make their own list.
        """
        rows = session.refresh()
        return JSONResponse(
            {
                "reviewer": session.reviewer,
                "todo": sum(1 for row in rows if row.pressable),
                "preselect": session.preselect,
                "cases": [
                    {
                        "case": row.case_id,
                        "number": row.number,
                        "title": row.title,
                        "status": row.status,
                        "state": row.state,
                        "pressable": row.pressable,
                    }
                    for row in rows
                ],
            }
        )

    @app.get("/api/part-one")
    def part_one(case: CaseId) -> JSONResponse:
        """The system, and this case's draft where the reader holds one.

        The reader's own words ride here so a case they come back to re-opens
        with what they wrote rather than with an empty box — their own list,
        their marks, their missing list and their notes. All of it is theirs
        and this case's alone, so it discloses nothing: a case they have not
        written for answers ``null`` for the own list, which is what re-arms
        the gate.

        The draft is read here rather than held, so the page it paints is the
        file on disk. That is what makes a read survive the process: the
        reader stops the app, runs it again, and this answers the same.
        """
        prepared = _open(session, case)
        held = _draft(session, case)
        # An empty draft where the reader holds none, so the three fields
        # that are not gated read the same either way.
        work = held or sittings.Draft(case=prepared.case_id, clone=str(session.root))
        return JSONResponse(
            {
                "case": prepared.case_id,
                "title": prepared.title,
                "reviewer": session.reviewer,
                "body": prepared.part_one,
                "files": prepared.files,
                "own_list": held.own_list if held else None,
                "marks": work.marks,
                "missing": work.missing,
                "notes": work.notes,
                "moved": _moved(session, prepared, held),
            }
        )

    @app.post("/api/own-list")
    def own_list(request: Request, body: OwnList) -> JSONResponse:
        # A foreign page that posts this decides the reader saw nothing, and
        # the sets open for whoever asks next. The rule is the method, so the
        # endpoint that satisfies it is a write like any other — and it names
        # a case, so one such page would post an empty list for every case in
        # the offered list and open the whole corpus in one pass.
        refuse_cross_origin(request)
        _require_token(request, session)
        prepared = _open(session, body.case)
        if _draft(session, body.case) is not None:
            # **A case takes one own list, and it is the first one.** The
            # sets for this case are open by now, so a second list would be
            # written after them and recorded as though it came first — which
            # is exactly the order the filled document claims. Refusing it is
            # what makes that claim true rather than hoped for.
            raise HTTPException(
                status_code=409,
                detail="that case already has your own list, and the recorded"
                " sets are open on it; a list written now would be evidence of"
                " an order that did not happen",
            )
        # Written before part two is reachable, and per case, because the
        # gate re-arms for each one. An empty list is allowed — "I saw
        # nothing" is an answer — but it has to be given.
        written = [item.strip() for item in body.items if item.strip()]
        # **The own list creates the draft; opening a case creates nothing.**
        # A reader who reads part one of ten cases and writes nothing leaves
        # no trace. The digests are taken here, so they say what the required
        # files held when this read began.
        case_dir = session.corpus_dir / prepared.case_id
        try:
            opened = sittings.digests(case_dir, prepared.files)
        except sittings.SittingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _save(
            session,
            sittings.Draft(
                case=prepared.case_id,
                clone=str(session.root),
                own_list=written,
                opened_digests=opened,
            ),
        )
        return JSONResponse({"case": body.case, "accepted": len(written)})

    @app.post("/api/draft")
    def save_progress(request: Request, body: Progress) -> JSONResponse:
        """Keep the marks, the missing list and the notes in the draft.

        The page posts this as the reader works, so a closed browser costs
        them nothing. It carries both controls for the reasons
        ``/api/own-list`` carries them: it names a case, and it writes under
        the reader's own store, so its effect outlives the process.
        """
        refuse_cross_origin(request)
        _require_token(request, session)
        _open(session, body.case)
        held = _draft(session, body.case)
        if held is None:
            raise HTTPException(
                status_code=409,
                detail="no draft holds that case; write your own list first",
            )
        held.marks = dict(body.marks)
        held.missing = list(body.missing)
        held.notes = body.notes
        _save(session, held)
        return JSONResponse({"case": body.case, "state": held.state})

    @app.post("/api/discard")
    def discard(request: Request, body: Which) -> JSONResponse:
        """Throw one draft away, and put its case back where it started.

        The reader's own decision to abandon a case, so it deletes and does
        not archive: the draft never merged, and nothing else records it.
        """
        refuse_cross_origin(request)
        _require_token(request, session)
        _open(session, body.case)
        try:
            gone = sittings.discard_draft(session.drafts, session.reviewer, body.case)
        except sittings.DraftError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"case": body.case, "discarded": gone})

    @app.get("/api/part-two")
    def part_two(case: CaseId) -> JSONResponse:
        prepared = _open(session, case)
        if _draft(session, case) is None:
            # The method's one rule, enforced rather than requested, and
            # asked of this case rather than of the session.
            raise HTTPException(
                status_code=409,
                detail="write your own list first; the recorded sets are not"
                " served until it is in, because the document prints your list"
                " above them and a later reader takes that order on trust",
            )
        return JSONResponse(
            {
                "case": prepared.case_id,
                "frameworks": prepared.part_two,
                "marks": [
                    {
                        "fingerprint": target.fingerprint,
                        "framework": target.framework,
                        "claims": list(target.claims),
                    }
                    for target in prepared.mark_targets
                ],
                "values": list(sittings.MARKS),
            }
        )

    @app.post("/api/finish")
    def finish(request: Request, body: Progress) -> JSONResponse:
        # This writes the reading document, appends to `case.json` and clears
        # the UNREVIEWED line — and it is what puts the case in `recorded`,
        # which `/api/submit` tests. Everything the allow-list then carries
        # into a pull request passes through here, so it is checked like the
        # endpoint it feeds.
        refuse_cross_origin(request)
        prepared = _open(session, body.case)
        held = _draft(session, body.case)
        if held is None:
            raise HTTPException(status_code=409, detail="no own list was written")
        if body.case in session.recorded:
            # One entry per reader per case in one pull request. This is the
            # local half of the rule the rail states: the case is signed now,
            # so the next reader of the rail sees it greyed either way.
            raise HTTPException(
                status_code=409, detail="this session already recorded that case"
            )
        # The second half of the drift check. A file can move while the tab
        # sits open, and this is where the digest is signed — so the reader
        # hears it here as well as at open, and their own list is untouched
        # either way.
        moved = _moved(session, prepared, held)
        case_dir = session.corpus_dir / body.case
        try:
            text = sittings.document(
                prepared, held.own_list, body.marks, body.missing, body.notes
            )
            (case_dir / session.document_name).write_text(text, encoding="utf-8")
            read = sittings.read_records(case_dir, prepared.files)
            sittings.record(
                case_dir, session.reviewer, read, session.document_name, body.notes
            )
            cleared = sittings.clear_unreviewed(session.root, prepared.case_id)
        except sittings.SittingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.recorded.add(body.case)
        # The draft keeps what the reader wrote and says the sitting is
        # recorded. It stays until a submit carries it: the record is in the
        # working tree by now, and nothing is a record until it merges.
        held.marks = dict(body.marks)
        held.missing = list(body.missing)
        held.notes = body.notes
        held.state = "finished"
        _save(session, held)
        return JSONResponse(
            {
                "case": prepared.case_id,
                "written": [
                    f"evals/corpus/{prepared.case_id}/{session.document_name}",
                    f"evals/corpus/{prepared.case_id}/case.json",
                    *([sittings.UNREVIEWED_FILE] if cleared else []),
                ],
                "moved": moved,
                "command": "python -m evals.harness.run submit sitting",
                "paste": _paste(session, prepared, len(read)),
                "can_submit": session.can_submit,
            }
        )

    @app.post("/api/submit")
    def open_the_pr(request: Request) -> JSONResponse:
        """Open the pull request, through the operator's own `gh`.

        The four controls this endpoint carries are in the module docstring,
        and they are all here rather than spread about because this is the
        only place in any app in this repository that can act on GitHub as
        the person running it.

        It takes no arguments. The submission is whatever this session already
        recorded into the working tree, so there is nothing in the request for
        an attacker to steer — and nothing to steer it with, since a request
        that did not read the page has no token.
        """
        if not session.can_submit:
            raise HTTPException(
                status_code=409,
                detail="no authenticated gh login, so there is nothing to"
                " submit as. Run the printed command yourself.",
            )
        refuse_cross_origin(request)
        _require_token(request, session)
        if not session.recorded:
            raise HTTPException(
                status_code=409, detail="record the sitting before submitting it"
            )
        outcome = submit_spine.submission(session.root, "sitting")
        return JSONResponse(outcome.to_json(), status_code=200 if outcome.ok else 409)

    return app


def _require_token(request: Request, session: Session) -> None:
    """The page token, on the two endpoints that carry it.

    A request that never read the page cannot hold it. It rides beside the
    origin check rather than instead of it, and it is spelled once because
    ``/api/own-list`` and ``/api/submit`` ask the same question of a request.

    ``/api/part-two`` gains no token: it is a read, it serves only what a
    passed gate already opened, and ``frame-ancestors 'none'`` covers the
    page that would read it.
    """
    sent = request.headers.get("x-sitting-token", "")
    if not secrets.compare_digest(sent.encode(), session.token.encode()):
        raise HTTPException(status_code=403, detail="wrong or missing page token")


def _open(session: Session, case_id: str) -> sittings.Prepared:
    """One offered case, prepared — or the refusal every naming endpoint gives.

    **This is the single rule for a case id that arrives in a request.** It
    resolves against the offered list, which is the rail's own pressable rows,
    so a signed case and a case nobody wrote refuse the same way and neither
    costs a check of its own. Reading the list is an allow-list check, so no
    request can name a path (A01). The message names no id, because a refusal
    that echoes the request tells a caller which ids exist.
    """
    if case_id not in session.offered:
        raise HTTPException(status_code=404, detail="not a case this sitting offers")
    try:
        return session.prepare(case_id)
    except sittings.SittingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _draft(session: Session, case_id: str) -> sittings.Draft | None:
    """This reader's draft of one case, or the refusal an unreadable one gets.

    **A draft the app cannot read refuses its own case, names the file, and
    changes nothing on disk.** Every other case still walks, because the rail
    surveys the store per case and this is asked per case. The message
    carries the path, which is the reader's next step: the file is theirs and
    nothing here will guess at what it should have said.
    """
    try:
        return session.draft(case_id)
    except sittings.DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _moved(
    session: Session, prepared: sittings.Prepared, draft: sittings.Draft | None
) -> list[str]:
    """The required files that moved since this reader opened this case.

    Asked at open, because a reader needs to know the text moved before they
    spend an hour on it, and again at finish, because a file can move while
    the tab sits open. It names files and judges nothing: the reader keeps
    their own list either way and decides whether it still answers the text.

    **The walk is the required list, never the draft's own keys.** The draft
    is a file the reader owns, so a name in it is a name this app did not
    write; walking the required files means every path read here comes from
    the case. A required file the draft never pinned carries no digest, which
    no file matches, so it reads as moved — a file the reader never opened.
    """
    if draft is None:
        return []
    return sittings.moved(
        session.corpus_dir / prepared.case_id,
        {name: draft.opened_digests.get(name, "") for name in prepared.files},
    )


def _save(session: Session, draft: sittings.Draft) -> None:
    """Write one draft, or refuse in the words the store used."""
    try:
        sittings.save_draft(session.drafts, session.reviewer, draft)
    except (sittings.DraftError, OSError) as exc:
        raise HTTPException(
            status_code=409, detail=f"the draft did not save — {exc}"
        ) from exc


def _paste(session: Session, prepared: sittings.Prepared, files_read: int) -> str:
    """The copy-paste alternative, for somebody not opening the PR from here."""
    return (
        f"Sitting: {prepared.case_id} by {session.reviewer}\n\n"
        f"Held over {files_read} file(s) — the sources, the model and every"
        " declared framework's reference set. My own threat list was written"
        " before the recorded sets were opened; the filled document is"
        f" committed as `{session.document_name}`.\n\n"
        "The `reviews` entry in `case.json` records the digest of each file as"
        " it stands in this PR, so a later edit to any of them puts the case"
        " back on the list."
    )


def _escape(text: str) -> str:
    """Escape for the page's **markup**, where the value lands in element text.

    Not for the ``<script>`` block: a script block does not decode HTML
    entities, so this is the wrong escape there and :func:`_js_literal` is the
    right one. Which of the two a field wants is decided by where its
    placeholder sits in :data:`_PAGE`.
    """
    return html.escape(text)


def _js_literal(value: object) -> str:
    """A value as a JavaScript literal, for a placeholder inside ``<script>``.

    Two things have to be true and neither is HTML escaping. The value has to
    survive as a JavaScript literal, which is what :func:`json.dumps` gives —
    ``html.escape`` would deliver the characters ``&quot;`` where a quote was,
    because nothing decodes entities in a script block. And it must not close
    the block: a value spelling ``</script>`` ends it and the rest of the page
    parses as HTML, so every ``<`` goes out as ``\\u003c``. That is the same
    escape, for the same reason, as the report payload in
    :func:`webapp.main.render_report`.

    The two values this carries today are a token and a boolean, and neither
    can spell either character. It is written this way so that stays a fact
    about the page rather than a condition the next field has to re-satisfy.
    """
    return json.dumps(value).replace("<", "\\u003c")


def _page(template: str, **fields: str) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(16)
    page = template.replace(_NONCE_PLACEHOLDER, nonce)
    for name, value in fields.items():
        page = page.replace(f"<!--{name}-->", value)
    return page, _CSP.format(nonce=nonce)


def _html(page: tuple[str, str]) -> HTMLResponse:
    body, csp = page
    return HTMLResponse(content=body, headers={"Content-Security-Policy": csp})


#: The page, as a raw string. Every ``\n`` in it is a newline escape inside a
#: JavaScript string literal, and a plain string would hand the browser the
#: newline itself — which ends the literal and stops the whole script block
#: parsing. ``tests/test_sitting_app.py`` holds that as a check.
_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Case sitting</title>
<style nonce="__CSP_NONCE__">
  :root { color-scheme: light dark; --line: #8884; }
  body { font: 16px/1.55 system-ui, sans-serif; margin: 0; display: flex;
         align-items: stretch; min-height: 100vh; }
  nav { flex: 0 0 19rem; border-right: 1px solid var(--line); padding: 1.4rem 1rem;
        position: sticky; top: 0; align-self: flex-start; max-height: 100vh;
        overflow-y: auto; }
  main { flex: 1; padding: 2rem 1.5rem 6rem; max-width: 46rem; }
  h1 { font-size: 1.1rem; margin: 0 0 .2rem; }
  h2 { font-size: 1.2rem; }
  .sub { color: #7a7a7a; margin-top: 0; }
  ol { list-style: none; margin: 1rem 0 0; padding: 0; }
  li { margin: 0; }
  .row { display: flex; gap: .55rem; align-items: baseline; width: 100%;
         text-align: left; font: inherit; padding: .4rem .5rem; border: 0;
         border-radius: 6px; background: none; }
  button.row { cursor: pointer; }
  button.row:hover { background: #8882; }
  li.current .row { background: #8883; }
  .row.dead { color: #7a7a7a; }
  .dot { flex: 0 0 .6rem; height: .6rem; border-radius: 50%; }
  .dot.todo { background: #d08b28; }
  .dot.draft { background: #3f7fd0; }
  .dot.finished { background: #2f9e5e; }
  .dot.signed { background: #8886; }
  .dot.error { background: #c34a3c; }
  .label { flex: 1; }
  pre { background: #8881; padding: 1rem; overflow-x: auto; white-space: pre-wrap;
        border-radius: 6px; font-size: .88rem; }
  textarea { width: 100%; min-height: 9rem; font: inherit; padding: .6rem;
             border: 1px solid var(--line); border-radius: 6px; }
  button { font: inherit; padding: .5rem 1rem; border-radius: 6px;
           border: 1px solid var(--line); background: #8882; cursor: pointer; }
  button:disabled { cursor: default; opacity: .5; }
  section { border-top: 1px solid var(--line); margin-top: 2rem; padding-top: 1rem; }
  header { display: flex; gap: 1rem; align-items: baseline;
           justify-content: space-between; }
  header h2 { margin: 0; }
  .walk { flex: 0 0 auto; margin: 0; display: flex; gap: .4rem; }
  .hidden { display: none; }
  .note { background: #8881; padding: .8rem 1rem; border-radius: 6px; }
  .frame { border: 1px dashed var(--line); border-radius: 6px; color: #7a7a7a;
           padding: 1.2rem 1rem; }
  select { font: inherit; padding: .2rem .4rem; border-radius: 6px;
           border: 1px solid var(--line); }
  .mark { display: flex; gap: .8rem; align-items: baseline; padding: .35rem 0;
          border-bottom: 1px solid var(--line); }
  .mark span { flex: 1; }
</style></head>
<body>
<nav>
  <h1>Case sitting</h1>
  <p class="sub" id="left">reading the corpus…</p>
  <ol id="cases"></ol>
</nav>

<main>
<div id="empty">
  <h2>The whole corpus is on the left</h2>
  <p class="note">Every case is in the list, with what it is waiting for. Pick
  one, or take the first case nobody has read. The list stays where it is, so
  there is nothing to come back from.</p>
  <p><button id="start">Start with the first case to do</button></p>
</div>

<article id="case" class="hidden">
  <header>
    <h2 id="caseTitle"></h2>
    <p class="walk">
      <button id="previous">← Previous</button>
      <button id="next">Next →</button>
    </p>
  </header>
  <p class="sub"><code id="caseId"></code>, read by <!--reviewer--></p>
  <p id="moved" class="note hidden"></p>

  <section id="one">
    <h2>Part 1 — the system</h2>
    <pre id="partOne">loading…</pre>
    <h2>Your list, written first</h2>
    <p class="note">Write what could go wrong: an attack, a missing control, a
    question the text does not answer. One per line. The recorded sets are not
    in this page until you submit this — that is the whole method.</p>
    <textarea id="own" placeholder="one per line"></textarea>
    <p><button id="lock">Save my list and show the recorded sets</button></p>
  </section>

  <section id="placeholder">
    <h2>Part 2 — what is recorded</h2>
    <p class="frame">The recorded sets open here, once your list is in. Until
    then they are not in this page at all, so the document can say your list
    came first and be right.</p>
  </section>

  <section id="two" class="hidden">
    <h2>Part 2 — what is recorded</h2>
    <p class="note">Mark each recorded finding: <code>agree</code> a real finding
    worth reporting, <code>doubt</code> overstated or unsupported by the text,
    <code>dup</code> the same finding as another entry. Leave a mark unset to say
    nothing about that one.</p>
    <div id="partTwo"></div>
    <h2>On your list and not on theirs</h2>
    <p class="note">The finding this sitting exists for. One per line.</p>
    <textarea id="missing" placeholder="one per line"></textarea>
    <h2>Notes</h2>
    <textarea id="notes" placeholder="counts, and anything you changed"></textarea>
    <p><button id="finish">Record the sitting</button></p>
  </section>

  <section id="discardBox" class="hidden">
    <h2>Discard this draft</h2>
    <p class="note">Throws away your own list, your marks, your missing list
    and your notes for this case, and puts it back on the list to do. No other
    case changes, and nothing in the repository changes.</p>
    <p><button id="discard">Discard my draft for this case</button></p>
  </section>

  <section id="done" class="hidden">
    <h2>Recorded</h2>
    <p>Written into your working tree:</p>
    <pre id="written"></pre>
    <p>Open the pull request:</p>
    <pre id="command"></pre>
    <p>Or paste this into one you open yourself:</p>
    <pre id="paste"></pre>
    <p class="note">Next carries you to the next case. The last Next ends at
    the submit stage, which is where the pull request is opened.</p>
  </section>
</article>

<article id="submitStage" class="hidden">
  <header>
    <h2>Submit</h2>
    <p class="walk"><button id="backToWalk">← Previous</button></p>
  </header>
  <p class="sub">The end of the walk, read by <!--reviewer--></p>
  <p id="ready" class="note"></p>
  <pre id="stageCommand" class="hidden"></pre>
  <div id="submitBox" class="hidden">
    <p class="note">Or let this open it for you, through the
    <code>gh</code> you are already signed in to. It runs the same checks
    first, pushes to your fork, and opens the pull request as you.</p>
    <p><button id="submit">Open the pull request as <!--reviewer--></button></p>
    <pre id="result" class="hidden"></pre>
  </div>
</article>
</main>

<script nonce="__CSP_NONCE__">
const $ = (id) => document.getElementById(id);
const lines = (id) => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);

const TOKEN = <!--token-->;
const CAN_SUBMIT = <!--cansubmit-->;

// The case on the stage, and the rail as the server last described it.
let current = null;
let rows = [];
// The command the last record printed, which the submit stage repeats. Named
// apart from the `command` element, because an id is a global of its own.
let printedCommand = "";

// What this session recorded. Nothing the reader writes is held here: every
// word of it goes to the draft on the server, so a case they come back to
// comes back as the file on disk says, and a closed browser costs nothing.
const recorded = new Set();

// The pending save. Every field in part two saves on a short delay, and the
// walk flushes before it moves, so what leaves the stage is already on disk.
let queued = 0;

function queueSave() {
  clearTimeout(queued);
  queued = setTimeout(saveDraft, 600);
}

// The reader's marks, missing list and notes, into the draft that already
// holds their own list. Nothing to save before part two is open: the draft
// does not exist yet, and the server refuses a save that names no draft.
async function saveDraft() {
  clearTimeout(queued);
  if (!current || $("two").classList.contains("hidden")) return;
  await fetch("/api/draft", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({
      case: current, marks: marksNow(), missing: lines("missing"),
      notes: $("notes").value,
    }),
  });
}

async function loadRail() {
  const d = await (await fetch("/api/rail")).json();
  rows = d.cases;
  $("left").textContent = d.todo + " to do";
  $("cases").replaceChildren(...rows.map(railRow));
  $("start").disabled = !rows.some(row => row.pressable);
  select(current);
  return d;
}

// A status dot, the case number and the title, with the full status as the
// row's tooltip. Nothing else rides here: a claim count would tell the reader
// how long to make their own list before they have written it. The title is
// case prose, so it lands through textContent like every other piece of it.
function railRow(row) {
  const item = document.createElement("li");
  item.title = row.status;
  item.dataset.case = row.case;
  const dot = document.createElement("span");
  dot.className = "dot " + row.state;
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = row.number + "  " + row.title;
  const press = document.createElement(row.pressable ? "button" : "span");
  press.className = row.pressable ? "row" : "row dead";
  press.append(dot, label);
  if (row.pressable) press.addEventListener("click", () => openCase(row.case));
  item.appendChild(press);
  return item;
}

function select(caseId) {
  for (const item of $("cases").children) {
    item.classList.toggle("current", item.dataset.case === caseId);
  }
}

// The cases the walk steps over: the rail's pressable rows, in rail order,
// which is the same list the server resolves a request against. A dead row is
// off it, so the walk never stops on a case that would refuse to open.
function walkable() {
  return rows.filter(row => row.pressable);
}

// One step from where the reader stands, in the rail's own order, landing
// only on a case they may open. It reads the rail rather than the walkable
// list, because recording a case greys its row: the reader is then standing
// on a case the walk no longer offers, and Next must still carry them
// forward from there rather than back to the top.
function step(delta) {
  const at = rows.findIndex(row => row.case === current);
  for (let i = at + delta; i >= 0 && i < rows.length; i += delta) {
    if (rows[i].pressable) {
      openCase(rows[i].case);
      return;
    }
  }
  if (delta > 0) openSubmit();
}

// The text moved under a read in progress. The reader hears it at open and
// again at finish, keeps every word they wrote, and judges for themselves
// whether their list still answers the text — this names files and no more.
function warn(files) {
  const box = $("moved");
  box.classList.toggle("hidden", !files.length);
  box.textContent = files.length
    ? "These files moved since you opened this case: " + files.join(", ")
      + ". Your list is untouched. Read them again, and decide whether it still answers the text."
    : "";
}

// The marks a draft came back with, onto the rows part two just drew.
function setMarks(marks) {
  for (const select of document.querySelectorAll("select[data-finding]")) {
    select.value = marks[select.dataset.finding] || "";
  }
}

async function openCase(caseId) {
  await saveDraft();
  current = caseId;
  select(caseId);
  blank();
  $("empty").classList.add("hidden");
  $("submitStage").classList.add("hidden");
  $("case").classList.remove("hidden");
  const first = walkable()[0];
  $("previous").disabled = !first || first.case === caseId;
  const res = await fetch("/api/part-one?case=" + encodeURIComponent(caseId));
  // A second click while this one was in flight owns the stage now, so this
  // answer is stale and must not paint one case's prose under another's name.
  if (current !== caseId) return;
  const d = await res.json();
  if (!res.ok) { $("partOne").textContent = d.detail; return; }
  $("caseTitle").textContent = d.title;
  $("caseId").textContent = d.case;
  $("partOne").textContent = d.body;
  warn(d.moved);
  // A case takes one own list. Where this reader already holds a draft, the
  // box comes back filled and locked and the sets open, because the server
  // refuses a second list for the same case.
  if (d.own_list === null) return;
  $("own").value = d.own_list.join("\n");
  $("missing").value = d.missing.join("\n");
  $("notes").value = d.notes;
  lock();
  $("discardBox").classList.remove("hidden");
  await showSets(caseId);
  if (current !== caseId) return;
  setMarks(d.marks);
}

// The end of the walk. The rail stays where it is, as it does on every case,
// so this is a stage rather than a page the reader has to come back from.
async function openSubmit() {
  await saveDraft();
  current = null;
  select(null);
  $("empty").classList.add("hidden");
  $("case").classList.add("hidden");
  $("submitStage").classList.remove("hidden");
  $("ready").textContent = recorded.size
    ? recorded.size + " case(s) recorded into your working tree."
    : "No case is recorded yet. Walk back and record one.";
  $("stageCommand").textContent = printedCommand;
  $("stageCommand").classList.toggle("hidden", !printedCommand);
  $("submitBox").classList.toggle("hidden", !(CAN_SUBMIT && recorded.size));
}

// The gate re-arms per case, so a case arriving on the stage arrives blind.
function blank() {
  $("partOne").textContent = "loading…";
  warn([]);
  $("partTwo").replaceChildren();
  for (const id of ["own", "missing", "notes"]) $(id).value = "";
  for (const id of ["written", "command", "paste"]) $(id).textContent = "";
  $("own").readOnly = false;
  $("lock").disabled = false;
  // A blind case shows the placeholder where part two will open, so the case
  // reads the same whichever way the reader arrived at it.
  $("placeholder").classList.remove("hidden");
  for (const id of ["two", "done", "discardBox"]) $(id).classList.add("hidden");
}

function lock() {
  $("own").readOnly = true;
  $("lock").disabled = true;
}

async function showSets(caseId) {
  const res = await fetch("/api/part-two?case=" + encodeURIComponent(caseId));
  const sets = await res.json();
  if (current !== caseId) return;
  if (!res.ok) { $("partOne").textContent = sets.detail; return; }
  const box = $("partTwo");
  box.replaceChildren();
  for (const [name, body] of Object.entries(sets.frameworks)) {
    const pre = document.createElement("pre");
    pre.textContent = body;
    box.appendChild(pre);
    for (const target of sets.marks.filter(m => m.framework === name)) {
      box.appendChild(markRow(target, sets.values));
    }
  }
  $("placeholder").classList.add("hidden");
  $("two").classList.remove("hidden");
}

$("start").addEventListener("click", () => {
  const first = walkable()[0];
  if (first) openCase(first.case);
});

$("previous").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));

// Previous off the submit stage lands on the last case the walk offers, which
// is the case the last Next came from.
$("backToWalk").addEventListener("click", () => {
  const list = walkable();
  if (list.length) openCase(list[list.length - 1].case);
});

$("lock").addEventListener("click", async () => {
  const caseId = current;
  const res = await fetch("/api/own-list", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({case: caseId, items: lines("own")}),
  });
  // Locked only once the server holds the list. A refused post that locked
  // the box anyway would leave the reader with nowhere to write it.
  if (!res.ok) { $("partOne").textContent = (await res.json()).detail; return; }
  lock();
  $("discardBox").classList.remove("hidden");
  await showSets(caseId);
  $("two").scrollIntoView({behavior: "smooth"});
});

// Every field in part two saves itself. A textarea reports `input` and a mark
// reports `change`, and both land on the same delayed save.
for (const name of ["input", "change"]) $("two").addEventListener(name, queueSave);

$("discard").addEventListener("click", async () => {
  const caseId = current;
  if (!confirm("Throw away your draft for this case? This cannot be undone.")) return;
  // Blanked first, so the flush inside openCase finds nothing to save and
  // the case comes back the way a case nobody wrote for comes back.
  blank();
  const res = await fetch("/api/discard", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({case: caseId}),
  });
  if (!res.ok) { $("partOne").textContent = (await res.json()).detail; return; }
  await loadRail();
  await openCase(caseId);
});

// The claim text is data, so it lands through textContent rather than through
// any markup — the same rule the rest of this page reads case prose under.
function markRow(target, values) {
  const row = document.createElement("div");
  row.className = "mark";
  const text = document.createElement("span");
  text.textContent = target.claims.join(" / ");
  const select = document.createElement("select");
  select.dataset.finding = target.fingerprint;
  for (const value of ["", ...values]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value || "—";
    select.appendChild(option);
  }
  row.append(text, select);
  return row;
}

function marksNow() {
  const marks = {};
  for (const select of document.querySelectorAll("select[data-finding]")) {
    if (select.value) marks[select.dataset.finding] = select.value;
  }
  return marks;
}

$("finish").addEventListener("click", async () => {
  const caseId = current;
  const res = await fetch("/api/finish", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      case: caseId, marks: marksNow(), missing: lines("missing"),
      notes: $("notes").value,
    }),
  });
  const d = await res.json();
  if (!res.ok) { $("written").textContent = d.detail; $("done").classList.remove("hidden"); return; }
  warn(d.moved);
  $("written").textContent = d.written.join("\n");
  $("command").textContent = d.command;
  $("paste").textContent = d.paste;
  $("two").classList.add("hidden");
  // The record is in the working tree now, so this case is off the rail and
  // the draft behind it is no longer the reader's to throw away here.
  $("discardBox").classList.add("hidden");
  $("done").classList.remove("hidden");
  printedCommand = d.command;
  recorded.add(caseId);
  // The recorded entry clears the case, so its row greys where it stands.
  await loadRail();
});

$("submit").addEventListener("click", async () => {
  $("submit").disabled = true;
  $("result").classList.remove("hidden");
  $("result").textContent = "running the checks…";
  const res = await fetch("/api/submit", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
  });
  const d = await res.json();
  if (d.detail) { $("result").textContent = d.detail; $("submit").disabled = false; return; }
  const report = (d.checks || []).map(c => (c.passed ? "ok   " : "FAIL ") + c.name
    + (c.problems.length ? "\n       " + c.problems.join("\n       ") : ""));
  if (d.ok) { report.push("", d.url, "", d.closing); }
  else { report.push("", d.error || "nothing opened; fix the failures above."); $("submit").disabled = false; }
  $("result").textContent = report.join("\n");
});

// A preselect moves the rail. It opens the case only when the rail presses it,
// because the endpoints resolve against the offered list and grant it nothing.
loadRail().then(d => {
  if (!d.preselect) return;
  const row = rows.find(item => item.case === d.preselect);
  if (row && row.pressable) openCase(row.case);
  else select(d.preselect);
});
</script>
</body></html>
"""


def build_session(
    root: Path,
    reviewer: str,
    case: str | None = None,
    can_submit: bool = False,
    drafts: Path | None = None,
) -> Session:
    """One reader, the whole corpus, and the case ``--case`` preselected.

    A preselect that names a case the corpus does not hold refuses here, at
    the start, where the reader is still looking at their terminal. A
    preselect the rail greys is kept: it moves the rail and grants nothing,
    because :func:`_open` reads the offered list rather than this field.

    ``drafts`` is where the reader's part-finished reads live. It is a
    parameter for the same reason the review app takes its ledger path as
    one: a caller that means a temporary tree must not write into a real home
    directory.
    """
    session = Session(
        root=root,
        reviewer=reviewer,
        roster=rosters.load(root / submit_spine.ROSTER_FILE),
        drafts=drafts or sittings.draft_root(),
        preselect=case,
        can_submit=can_submit,
    )
    session.refresh()
    if case is not None and case not in {row.case_id for row in session.rows}:
        raise SystemExit(f"no case {case!r} under evals/corpus/")
    if case in session.offered:
        # Eagerly, so a case whose claims the identity rule cannot key
        # refuses at the command line rather than at the first click.
        session.prepare(case)
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        help="the case id the rail opens on. One value: a preselect answers"
        " where the walk starts, and that question has one answer.",
    )
    parser.add_argument(
        "--reviewer",
        help="your GitHub login. Read from the authenticated `gh` when omitted,"
        " because it is the name the record carries either way.",
    )
    parser.add_argument(
        "--list", action="store_true", help="print the cases nobody has read"
    )
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="hide the button that opens the pull request, leaving the printed"
        " command and the paste text as the only ways out",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if args.list:
        print("\n".join(sittings.unreviewed_cases(root)))
        return 0

    # One read of the login, answering two questions: what name the record
    # carries, and whether there is an account to open a pull request as. A
    # tree with no `gh` still works — it just ends at the command rather than
    # the button.
    try:
        login = submit_spine.gh_login(root)
    except submit_spine.SubmitError:
        login = ""

    reviewer = args.reviewer or login
    if not reviewer:
        print(
            "cannot read your gh login, so pass --reviewer with the login the"
            " record should carry",
            file=sys.stderr,
        )
        return 1

    # Only ever as yourself. A submission opened under a name `gh` does not
    # hold would fail #320's binding in CI, so offering the button would be
    # inviting a red PR.
    can_submit = bool(login) and login == reviewer and not args.no_submit
    session = build_session(root, reviewer, args.case, can_submit)
    import uvicorn

    print(f"sitting as {reviewer}")
    print(f"{len(session.offered)} cases to do")
    if can_submit:
        print("the page can open the pull request as you; --no-submit hides it")
    print(f"open http://{HOST}:{PORT}/")
    uvicorn.run(create_app(session), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
