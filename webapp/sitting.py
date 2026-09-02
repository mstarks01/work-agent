"""The Case Sitting app: read a case, judge its reference sets, record it.

Run it from a clone, with no credentials of any kind::

    uv run python webapp/sitting.py

The app offers the whole corpus. A rail on the left lists every case with a
status dot, the case number and the title, and it never leaves. It is therefore
both how a reader starts and how they get back, and no control returns to a list
that never went away. A row carries no claim count and no reason the case waits,
because either would tell the reader how long to make their own list before they
have written it. ``--case`` preselects where the rail opens, and grants nothing.

A case a sitting already clears is greyed and off the offered list once no draft
of it is left, whoever signed it. The draft is what re-opens a case, so a reader
who records one still presses their own row and records it again. :func:`_open`
resolves every case id that arrives in a request against that list, so the
refusal a signed case needs is the same rule that refuses a case id nobody
wrote. The status reads :func:`evals.harness.sitting.clears`, and never the
presence of an entry in ``reviews``: a drifted digest leaves an entry that
clears nothing, and a rail keyed on the entry would grey a case CI asks somebody
to read.

It is eval-side tooling rather than the product, and it is the browser half of a
path that also works entirely from the shell. Everything it writes is what
``evals/BLESSING.md`` step 6 asks a person to write by hand, and ``submit
sitting`` checks the result the same way either way. It prepares your working
tree, and then offers three ways out: run the command yourself, paste the text
into a pull request you open, or press the button.

The button opens a pull request, through your own authenticated ``gh``. That
reverses map #319's "the web app never gains a network write", on the
maintainer's instruction, and ``docs/agents/issue-tracker.md`` records it. It is
not a hosted service: nothing is hosted, no credential is held here, and the app
still binds to loopback. What it does mean is that a request reaching
:func:`create_app`'s submit endpoint can act on GitHub as you. That one endpoint
therefore carries five controls, where the app's usual number is none:

* the Host check every loopback app here runs, which is what stops DNS
  rebinding making an attacker's page same-origin with this one;
* ``frame-ancestors 'none'``, which is what makes the next two mean anything. A
  press inside somebody else's frame arrives same-origin and carries the page
  token, because the page it comes from really is this one. Framing is therefore
  the way past both, and refusing to be framed is the answer;
* ``Sec-Fetch-Site: same-origin``, the check every local app here runs on its
  own write endpoints, through the one
  :func:`~webapp.page.refuse_cross_origin` they share;
* a one-time token, minted per process and embedded in the page, so a request
  that never read the page cannot carry it;
* no request-controlled arguments at all. The endpoint opens whatever the
  working tree holds, and takes neither a kind nor a path. A drop is what
  changes that, and a drop is a write of its own with its own controls.

Every writing endpoint carries the origin check, and not only the submit one.
``/api/finish`` writes the reading document, appends to ``case.json`` and puts
the case on the list the press carries, so a foreign page that reaches it
decides what a later press publishes. It writes the draft too, so it carries the
page token for all the reasons below. ``/api/own-list`` satisfies the method's
one rule, so a foreign page that reaches it opens the recorded sets for whoever
asks next. It carries the page token as well, because it names a case: one such
page would post an empty list for every case in the offered list, and open the
whole corpus in one pass. ``/api/draft`` and ``/api/discard`` carry both for
those two reasons and one more: they write under ``~/.local/state/``, so their
effect outlives the process. ``/api/drop`` and ``/api/put-back`` carry both for
all three, and for a fourth: they decide what the next press publishes.
``/api/part-two`` gains no token. It is a read, it serves only what a passed
gate already opened, and the frame rule covers the page that would read it.

One press submits every finished case, and the page names what stays behind. A
pinned rail footer reads ``Submit — N cases ready``, counts the finished drafts,
and is off at a count of zero, so the count and the way to the press are one
control. The submit stage lists each finished draft the press will carry, one
row each, with a **Drop**. A dropped case moves to a held-back group with a
**Put back**, so the drop is visible and reversible on the same page. The stage
also says how many cases stay unfinished, which stops the failure a read that
survives the process invites: a reader who believes they submitted five cases
when they submitted four.

The list on the page is the submission rather than a description of it.
``submit sitting`` builds itself from the working tree, so a drop takes the
filled document, the appended entry and the cleared line back out, and puts the
case to *draft in progress*. The reader keeps every word they wrote, and a put
back writes the same record again from the same draft. Nothing here reads a
draft file from the terminal, because a second reader of that file would be a
second surface over these rules.

The own list comes first, and the server enforces it. ``/api/part-two`` refuses
until the reader has posted their own threat list for that case, so the recorded
sets are not in the page at all. What the gate protects is the evidence in the
filled document. That document prints the reader's own list above the recorded
sets, and a later reader takes the order on trust. The gate makes the order true
by construction, so the trust is earned rather than asked for. It is enforced
the way the review app enforces configuration-blindness, by the payload not
carrying it, rather than by asking.

The list has to say something: ``MIN_OWN_LIST`` characters of the reader's own
words, counted with the blank lines and the padding taken out. The press was a
click before this. An empty box opened the recorded sets, and the sitting then
measured a list nobody wrote against the list it is supposed to test. The page
disables the press below the same count, but the endpoint is where the rule
lives, because the press is a request and the request is what opens the sets.

The page counts and says nothing about the count. A reader writing their own
list is the one moment of this method that has to be theirs, and a running total
sets the length they write to.

The gate re-arms per case, and a case takes one own list. Both halves are the
same rule read two ways: a case's sets open once that case's own list exists. A
reader who reaches a case they have not written for therefore reaches it blind,
and a case they already wrote for re-opens with the list they wrote and refuses
a second one. Without that refusal the reader could open a case's sets, come
back to it, and post a list the document would then print above them, which
would be evidence of an order that did not happen.

The case is laid out rather than printed flat. ``evals/build_review_docs``
describes part one and each recorded set as blocks, and the reading document and
this page render those same blocks. That is one description of a case and two
surfaces, so neither can drift into describing a different system. Each recorded
finding is a card that carries the mark answering it, rather than a wall of text
above a list of selects.

The reader walks the corpus, and Previous and Next sit in the stage header. They
step in the rail's order over the cases the reader may open, and the last Next
lands on the submit stage. The rail never leaves, so the walk reloads no
document: every arrival reads the case back from the server.

A successful submit deletes every draft it carried. That work is in a pull
request by then, and a store that only grows is a store nobody trusts.

A part-finished read survives the browser and the process. It lives in a **Draft
Sitting**, one file per case under the reader's own GitHub login, outside the
repository, and it never merges. That is what keeps an unsigned own list out of
a pull request. The own list creates it, so a reader who reads part one of ten
cases and writes nothing leaves no trace. The page holds no copy of what they
wrote: the own list, the marks, the missing list and the notes go to the draft
as they are written, and a case comes back saying what the file on disk says.
The reader stops the app, runs it again tomorrow, and finds the case where they
left it. They may also throw one draft away, which puts that case back on the
list to do and changes nothing in the repository.

The app says so when the text moved under an open draft. The draft pins the
digest of every required file as the reader opened it, and that pin is what
makes the warning honest. Without it, the app could say only that a file differs
from what a recorded sitting signed, which answers a different question. The
check runs at open, because a reader needs to know before they spend an hour on
the case, and again at finish, because a file can move while the tab sits open.
The recorded entry signs the digests taken at finish, so it signs the bytes that
will merge. The reader keeps their own list either way, and judges whether it
still answers the text. Nothing here judges that for them.

A hand-edited draft costs nothing this project can price, and nothing notices
it. A reader who types their own list into that file by hand wrote a list, which
is the whole of what the filled document claims. A timestamp pair on the draft
is rejected for the same reason: the reader owns the file, so they can write the
timestamps too. The gate's job is to make the ordinary path the correct path,
rather than to police the person at the keyboard.

A draft the app cannot read refuses its own case, names the file, and changes
nothing on disk (A10). The rail shows that one row in an error state, and every
other case still walks. Two alternatives are rejected. Treating it as absent
throws the reader's own list away and re-arms the gate, so they retype a list
they already wrote and never learn the first one existed. Repairing it writes a
guess into the one file the reader owns.

The security posture is inherited from ``webapp/review.py`` rather than
re-derived:

* Loopback only, and a checked ``Host`` (A01). Binding alone does not stop a
  rebound page in the operator's own browser, and this app writes to the corpus.
* Both names come from the command line, never from the request (A01). A browser
  field naming the submitting account would let one person file a sitting as
  another, and #320's binding rests on that name being true. The same rule
  covers ``--submitted-for``, which grants nothing but is evidence: a request
  that could set it could write a read onto somebody's name.
* Case prose reaches the page as data (LLM05, A05). The case and each recorded
  claim arrive as JSON blocks. The page builds its own markup around them and
  puts every word inside it through ``textContent``, so a sentence that spells a
  tag arrives as those characters.
* A mark is an allowlist, checked here (A05). The value is the method's closed
  set of three, and the key must name a recorded finding of this case, so a
  request cannot write a sentence of its own into the evidence.
* Every write lands inside one offered case's directory (A01), plus the
  unreviewed list. A request names a case id and never a path, and the id
  resolves against the offered list by allowlist, so a case the rail greys and a
  case nobody wrote refuse the same way.
* A draft is filed under a login and a case id, and both are checked as path
  segments (A01). The case id arrives in a request, and a value carrying a
  separator would write outside the store.
* The submit endpoint is off unless ``gh`` is authenticated. With no login there
  is nothing to act as, so the button is never offered.
"""

from __future__ import annotations

import argparse
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
from evals.harness.reference import ANONYMOUS, is_submitted_for
from evals.harness.roster import Roster
from evals.harness.sitting import MIN_OWN_LIST, own_list_is_written
from webapp.page import (
    LOOPBACK_HOSTS,
    Grants,
    SecurityHeaders,
    escape,
    refuse_cross_origin,
    render,
    response,
    script_json,
)

HOST = "127.0.0.1"
PORT = 8020

#: What this surface calls itself in the evidence it writes. The offline page
#: writes its own name into the same line, so the document says which of the
#: two a read happened on rather than assuming the older one.
HELD = "`webapp/sitting.py`"

#: The page runs its own script, carries its own style and calls its own
#: endpoints. Nothing else.
#:
#: The closed half of the policy that :mod:`webapp.page` adds is the fifth
#: control on the submit path, and it is what makes the other four mean
#: anything. A press inside somebody else's frame reaches this app as
#: same-origin and carries the page token, because the page it comes from
#: really is this one — so framing beats the header check and the token
#: together.
_PAGE_GRANTS = Grants(script=True, style=True, connect=True)


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
    #: The account this session acts as, from `gh` or ``--submitted-by``.
    submitted_by: str
    #: Who is doing the reading, from ``--submitted-for``. It equals
    #: :attr:`submitted_by` unless the operator says otherwise at launch.
    submitted_for: str
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
    #: The cases the reader recorded and then held back from this press. It
    #: is the only thing the submit stage keeps in memory, and it keeps it
    #: because a drop is a decision about one press: on disk a dropped case
    #: is *draft in progress*, which is what it is, and tomorrow's session
    #: offers it as one. What the press carries is read from the tree.
    dropped: set[str] = field(default_factory=set)

    @property
    def store(self) -> sittings.Store:
        """Where this reader's sitting reads and writes.

        The three values :mod:`evals.harness.sitting` needs to record a
        sitting and to take the record back off. Built rather than held, so a
        session that moves its clone or its draft store moves this with it.
        """
        return sittings.Store(
            root=self.root,
            submitted_by=self.submitted_by,
            submitted_for=self.submitted_for,
            drafts=self.drafts,
            held=HELD,
        )

    @property
    def document_name(self) -> str:
        """Spelled in :mod:`evals.harness.sitting`, because ``submit
        sitting`` admits this name under the case prefix and no other."""
        return self.store.document_name

    @property
    def corpus_dir(self) -> Path:
        return self.store.corpus_dir

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

        A posted own list moves its case to *draft in progress* and a record
        moves it to *finished, not submitted*, so the page asks for this after
        every write as well as at load.
        """
        self.rows = sittings.rail(
            self.corpus_dir,
            self.roster,
            sittings.draft_states(self.drafts, self.submitted_by),
        )
        return self.rows

    def carried(self) -> list[sittings.Row]:
        """The finished drafts, which is exactly what one press carries.

        Read from the tree and the store rather than remembered, so the list
        the reader sees before the press is the list the submission builds
        itself from. A drop takes the record out of the working tree and
        puts its draft back to ``open``, so it leaves this list by the same
        rule that put it here.
        """
        return [row for row in self.refresh() if row.state == "finished"]

    def draft(self, case_id: str) -> sittings.Draft | None:
        """This reader's draft of one case, read from disk on every ask.

        Never held in memory. The draft is the one record of a part-finished
        read, and a copy in the process could disagree with the file the
        reader owns.
        """
        return sittings.load_draft(self.drafts, self.submitted_by, case_id)

    def prepare(self, case_id: str) -> sittings.Prepared:
        """One case, prepared once and kept for as long as the process runs."""
        if case_id not in self.prepared:
            self.prepared[case_id] = sittings.prepare(self.corpus_dir / case_id)
        return self.prepared[case_id]


def create_app(session: Session) -> FastAPI:
    """The sitting app: the rail over the whole corpus, one case on the stage."""
    app = FastAPI(title="Case sitting", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return response(
            render(
                _PAGE,
                _PAGE_GRANTS,
                readby=escape(session.submitted_for),
                submitter=escape(session.submitted_by),
                token=script_json(session.token),
                cansubmit=script_json(session.can_submit),
                minownlist=script_json(MIN_OWN_LIST),
            )
        )

    @app.get("/api/rail")
    def rail() -> JSONResponse:
        """The whole corpus, each case with the status the reader reads.

        Re-read from the tree on every call, because a record moves its own
        case to *finished, not submitted* and the reader must see that without
        a restart. It carries no claim count and no reason a case waits: a
        number here would tell the reader how long to make their own list.
        """
        rows = session.refresh()
        return JSONResponse(
            {
                "submitted_by": session.submitted_by,
                "submitted_for": session.submitted_for,
                "todo": sum(1 for row in rows if row.state == "todo"),
                # The pinned footer's count and its own reason to be on
                # screen. It is the finished drafts, which is what the press
                # carries, so the footer and the submit stage never disagree.
                "ready": sum(1 for row in rows if row.state == "finished"),
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
                "submitted_by": session.submitted_by,
                "submitted_for": session.submitted_for,
                "blocks": prepared.part_one_blocks,
                "files": prepared.files,
                "own_list": held.own_list if held else None,
                # ``None`` where the reader holds no draft, because a draft is
                # the only thing that can say how far the read got. A finished
                # one is what puts *Re-record this sitting* on the button.
                "state": held.state if held else None,
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
        # gate re-arms for each one.
        written = [item.strip() for item in body.items if item.strip()]
        # **The list has to say something.** The page disables the press below
        # the same count, and this is why the rule is here as well: the press
        # is a request, and a request is what opens the sets. Counted over the
        # stripped words, so a page full of blank lines does not pass.
        typed = sum(len(item) for item in written)
        if not own_list_is_written(written):
            raise HTTPException(
                status_code=400,
                detail=f"your own list is {typed} characters and the sets open"
                f" at {MIN_OWN_LIST}; write what you think could go wrong"
                " first, because the sitting measures your list against"
                " theirs",
            )
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
            gone = sittings.discard_draft(
                session.drafts, session.submitted_by, body.case
            )
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
                "frameworks": prepared.part_two_blocks,
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
        # the UNREVIEWED line, and it is what puts the case on the list the
        # press carries. Everything the allow-list then carries into a pull
        # request passes through here, so it is checked like the endpoint it
        # feeds: it names a case, it writes under the reader's own store, and
        # it decides what a later press publishes.
        refuse_cross_origin(request)
        _require_token(request, session)
        prepared = _open(session, body.case)
        held = _draft(session, body.case)
        if held is None:
            raise HTTPException(status_code=409, detail="no own list was written")
        # The second half of the drift check. A file can move while the tab
        # sits open, and this is where the digest is signed — so the reader
        # hears it here as well as at open, and their own list is untouched
        # either way.
        moved = _moved(session, prepared, held)
        _record(session, prepared, held, body.marks, body.missing, body.notes)
        return JSONResponse(
            {
                "case": prepared.case_id,
                "written": _written(session, [prepared.case_id]),
                "moved": moved,
                # The count the pinned footer reads. The press and the ways
                # out live on the submit stage: there is one of each for the
                # whole session rather than one per case.
                "ready": len(session.carried()),
            }
        )

    @app.get("/api/stage")
    def stage() -> JSONResponse:
        """The end of the walk: what one press carries, and what stays behind.

        The list is the finished drafts, read from the tree on every call, so
        it is the submission the press builds rather than a description of
        one. **The stage also names what stays behind**, which is the failure
        a read that survives the process invites: a reader who believes they
        submitted five cases when they submitted four.
        """
        rows = session.refresh()
        carried = [row for row in rows if row.state == "finished"]
        held_back = [
            row
            for row in rows
            if row.case_id in session.dropped and row.state == "draft"
        ]
        cases = [row.case_id for row in carried]
        return JSONResponse(
            {
                "submitted_by": session.submitted_by,
                "submitted_for": session.submitted_for,
                "ready": [_stage_row(row) for row in carried],
                "held_back": [_stage_row(row) for row in held_back],
                # Every case that is neither carried nor signed off. A signed
                # case is done and a carried one is about to be, so what is
                # left is what nobody has finished.
                "unfinished": sum(
                    1 for row in rows if row.state not in ("finished", "signed")
                ),
                "written": _written(session, cases),
                "command": "python -m evals.harness.run submit sitting",
                "paste": _paste(session, cases),
            }
        )

    @app.post("/api/drop")
    def drop(request: Request, body: Which) -> JSONResponse:
        """Hold one recorded case back from this pull request.

        The press takes no argument and the submission builds itself from the
        working tree, so a drop is a change to what the press finds: the
        filled document, the appended entry and the cleared line all come
        back out, and the case goes back to *draft in progress*. That is what
        makes the list on the stage exactly what the press carries.

        **The reader keeps every word they wrote.** Only the two fields the
        record set are cleared, so *Put back* writes the same record again
        from the same draft.

        It carries both controls for the reasons ``/api/discard`` carries
        them, and one more: it decides what a later press publishes.
        """
        refuse_cross_origin(request)
        _require_token(request, session)
        prepared = _open(session, body.case)
        held = _draft(session, body.case)
        if held is None or held.state != "finished":
            raise HTTPException(
                status_code=409,
                detail="that case is not recorded, so this press does not carry it",
            )
        try:
            sittings.withdraw(session.store, prepared, held)
        except (sittings.SittingError, sittings.DraftError, OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.dropped.add(prepared.case_id)
        return JSONResponse({"case": prepared.case_id, "state": held.state})

    @app.post("/api/put-back")
    def put_back(request: Request, body: Which) -> JSONResponse:
        """Put a case this reader dropped back into the pull request.

        It writes the record from the draft rather than from the page, so a
        case put back carries exactly the marks, the missing list and the
        notes the reader left on it. Only a case dropped on this page comes
        back this way: a drop and a put back are one control read twice, and
        every other draft is recorded on its own case stage.
        """
        refuse_cross_origin(request)
        _require_token(request, session)
        prepared = _open(session, body.case)
        held = _draft(session, body.case)
        if body.case not in session.dropped or held is None or held.state != "open":
            raise HTTPException(
                status_code=409, detail="that case is not held back from this press"
            )
        _record(session, prepared, held, held.marks, held.missing, held.notes)
        return JSONResponse({"case": prepared.case_id, "state": held.state})

    @app.post("/api/submit")
    def open_the_pr(request: Request) -> JSONResponse:
        """Open the pull request, through the operator's own `gh`.

        The four controls this endpoint carries are in the module docstring,
        and they are all here rather than spread about because this is the
        only place in any app in this repository that can act on GitHub as
        the person running it.

        It takes no arguments. The submission is whatever stands in the
        working tree, so there is nothing in the request for an attacker to
        steer — and nothing to steer it with, since a request that did not
        read the page has no token. **One press carries every finished draft
        the reader did not drop**, because a drop takes its case out of the
        tree the submission reads.

        **A successful submit deletes every draft it carried.** That work is
        in a pull request by then, and a store that only grows is a store
        nobody trusts. A draft that will not delete is named in the answer
        rather than swallowed: the pull request is open either way, and the
        reader is the only one who can clear the file.
        """
        if not session.can_submit:
            raise HTTPException(
                status_code=409,
                detail="no authenticated gh login, so there is nothing to"
                " submit as. Run the printed command yourself.",
            )
        refuse_cross_origin(request)
        _require_token(request, session)
        carried = [row.case_id for row in session.carried()]
        if not carried:
            raise HTTPException(
                status_code=409, detail="record the sitting before submitting it"
            )
        outcome = submit_spine.submission(session.root, "sitting")
        kept = _delete_drafts(session, carried) if outcome.ok else []
        return JSONResponse(
            {**outcome.to_json(), "carried": carried, "kept": kept},
            status_code=200 if outcome.ok else 409,
        )

    return app


def _require_token(request: Request, session: Session) -> None:
    """The page token, on the two endpoints that carry it.

    A request that never read the page cannot hold it. It rides beside the
    origin check rather than instead of it, and it is spelled once because
    every writing endpoint asks the same question of a request.

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
        sittings.save_draft(session.drafts, session.submitted_by, draft)
    except (sittings.DraftError, OSError) as exc:
        raise HTTPException(
            status_code=409, detail=f"the draft did not save — {exc}"
        ) from exc


def _record(
    session: Session,
    prepared: sittings.Prepared,
    held: sittings.Draft,
    marks: dict[str, sittings.Mark],
    missing: list[str],
    notes: str,
) -> None:
    """Record one sitting, in the words a browser gets when it will not.

    The act is :func:`evals.harness.sitting.finish`, which writes the three
    files and saves the draft that says what it wrote. What is left here is
    what the app owes the page: a 409 rather than a traceback, and the one
    piece of state a press keeps in memory.
    """
    try:
        sittings.finish(
            session.store, prepared, held, marks=marks, missing=missing, notes=notes
        )
    except (sittings.SittingError, sittings.DraftError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.dropped.discard(prepared.case_id)


def _delete_drafts(session: Session, cases: list[str]) -> list[str]:
    """Delete the drafts a submission carried, and name any that survived.

    A draft that will not delete stops nothing: the pull request is open, and
    the file is in the reader's own store where they are the only one who can
    clear it. So it is reported rather than raised, and rather than swallowed.
    """
    kept = []
    for case_id in cases:
        try:
            sittings.discard_draft(session.drafts, session.submitted_by, case_id)
        except (sittings.DraftError, OSError) as exc:
            kept.append(f"{case_id}: {exc}")
    return kept


def _stage_row(row: sittings.Row) -> dict[str, str]:
    """One line of the submit stage: the case, its number and its title.

    The same three fields a rail row carries, and for the same reason —
    nothing here says how many claims a case holds.
    """
    return {"case": row.case_id, "number": row.number, "title": row.title}


def _written(session: Session, cases: list[str]) -> list[str]:
    """Every path a sitting writes, over the cases named.

    Two files per case and the unreviewed list once, which is the whole of
    what a sitting pull request may change under a case prefix apart from a
    reference set the reader corrected.
    """
    return [
        *(
            f"evals/corpus/{case}/{name}"
            for case in cases
            for name in (session.document_name, "case.json")
        ),
        *([sittings.UNREVIEWED_FILE] if cases else []),
    ]


def _paste(session: Session, cases: list[str]) -> str:
    """The copy-paste alternative, for somebody not opening the PR from here.

    It names every case the press carries, in the shape
    ``evals.harness.submit`` gives the pull request it opens: a title that
    counts the cases and a closing that lists them. Plural agreement is the
    count's own job, because a branch on a count is what this repository does
    not write.
    """
    listed = "\n".join(f"- {case}" for case in cases)
    names = sittings.naming(session.submitted_by, session.submitted_for)
    return (
        f"Sitting: {names}, {len(cases)} cases\n\n"
        f"{listed}\n\n"
        "Each case above was read whole — the sources, the model and every"
        " declared framework's reference set. The reader's own threat list for"
        " a case was written before that case's recorded sets were opened, and"
        f" the filled document is committed as `{session.document_name}`.\n\n"
        "The `reviews` entry in each `case.json` records the digest of every"
        " file as it stands in this PR, so a later edit to any of them puts"
        " that case back on the list."
    )


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
        position: sticky; top: 0; align-self: flex-start; height: 100vh;
        box-sizing: border-box; display: flex; flex-direction: column; }
  #cases { flex: 1; overflow-y: auto; }
  .pin { flex: 0 0 auto; margin: .8rem 0 0; padding-top: .8rem;
         border-top: 1px solid var(--line); }
  .pin button { width: 100%; }
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

  /* The case, laid out. Every word below still arrives through textContent;
     the markup around it is this page's own, written here and never built
     from case prose. */
  .doc h3 { font-size: .74rem; text-transform: uppercase; letter-spacing: .08em;
            color: #8a8a8a; margin: 1.8rem 0 .6rem; font-weight: 600; }
  .doc h3:first-child { margin-top: .4rem; }
  /* One framework's whole set, over the groups inside it. The rule is what
     separates two packages' sets where a case declares both. */
  .doc h3.set { font-size: .8rem; color: inherit; margin: 2.4rem 0 .8rem;
                padding-top: 1.2rem; border-top: 1px solid var(--line); }
  .doc h3.set:first-child { padding-top: 0; border-top: 0; }
  .card { border: 1px solid var(--line); border-radius: 10px; background: #8881;
          padding: .9rem 1.1rem; margin: 0 0 .8rem; }
  .card > :first-child { margin-top: 0; }
  .card > :last-child { margin-bottom: 0; }
  .card h4 { font-size: 1rem; font-weight: 600; margin: 0; flex: 1 1 16rem; }
  .head { display: flex; gap: .55rem; align-items: baseline; flex-wrap: wrap; }
  .num { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
         font-size: .8rem; color: #7a7a7a; }
  .hint { color: #7a7a7a; font-size: .85rem; margin: .4rem 0 .7rem; }
  .verbatim { background: none; border-left: 2px solid var(--line); border-radius: 0;
              margin: 0; padding: 0 0 0 1rem; font: inherit; font-size: .95rem; }
  code.id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: .85em; background: #8882; border-radius: 4px;
            padding: .05rem .3rem; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: .84rem; }
  th, td { text-align: left; padding: .35rem .6rem; white-space: nowrap;
           border-bottom: 1px solid var(--line); }
  th { font-size: .7rem; text-transform: uppercase; letter-spacing: .05em;
       color: #7a7a7a; font-weight: 600; }
  td { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  ul.terms { list-style: none; margin: 0; padding: 0; }
  ul.terms li { font-size: .9rem; padding: .35rem 0;
                border-bottom: 1px solid var(--line); }
  ul.terms li:last-child { border-bottom: 0; }

  /* One recorded finding, and the mark that answers it. */
  .rec { border-left: 3px solid #8886; }
  ul.fields { list-style: none; margin: .6rem 0 0; padding: 0; font-size: .85rem; }
  ul.fields li { padding: .12rem 0; color: #6f6f6f; }
  .lbl { text-transform: uppercase; letter-spacing: .05em; font-size: .68rem;
         color: #8a8a8a; margin-right: .35rem; }
  .mark { display: flex; gap: .6rem; align-items: baseline; margin-top: .8rem;
          padding-top: .7rem; border-top: 1px solid var(--line); }
  .mark label { font-size: .8rem; color: #7a7a7a; }
  .aside { font-size: .82rem; color: #7a7a7a; margin: .8rem 0 0;
           padding-top: .7rem; border-top: 1px solid var(--line); }
  .gap { border-left: 3px solid #c34a3c; }
  .gate { color: #7a7a7a; font-size: .85rem; margin-left: .6rem; }
</style></head>
<body>
<nav>
  <h1>Case sitting</h1>
  <p class="sub" id="left">reading the corpus…</p>
  <ol id="cases"></ol>
  <p class="pin"><button id="toSubmit" class="hidden"></button></p>
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
  <p class="sub"><code id="caseId"></code>, read by <!--readby--></p>
  <p id="moved" class="note hidden"></p>

  <section id="one">
    <h2>Part 1 — the system</h2>
    <div id="partOne" class="doc">loading…</div>
    <h2>Your list, written first</h2>
    <p class="note">Write what could go wrong: an attack, a missing control, a
    question the text does not answer. One per line. The recorded sets are not
    in this page until you submit this — that is the whole method, so the
    press waits until the list says something.</p>
    <textarea id="own" placeholder="one per line"></textarea>
    <p><button id="lock">Save my list and show the recorded sets</button>
    <span id="ownHint" class="gate"></span></p>
  </section>

  <section id="placeholder">
    <h2>Part 2 — what is recorded</h2>
    <p class="frame">The recorded sets open here, once your list is in. Until
    then they are not in this page at all, so the document can say your list
    came first and be right.</p>
  </section>

  <section id="two" class="hidden">
    <h2>Part 2 — what is recorded</h2>
    <p class="note">Mark each recorded finding: <b>Agree</b> a real finding
    worth reporting, <b>Reject</b> overstated or unsupported by the text,
    <b>Duplicate</b> the same finding as another entry. Leave a mark unset to
    say nothing about that one.</p>
    <div id="partTwo" class="doc"></div>
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
    <p class="note">Next carries you to the next case. The last Next ends at
    the submit stage, where one press carries every case you recorded — and
    where the command and the paste text are.</p>
  </section>
</article>

<article id="submitStage" class="hidden">
  <header>
    <h2>Submit</h2>
    <p class="walk"><button id="backToWalk">← Previous</button></p>
  </header>
  <p class="sub">The end of the walk, read by <!--readby--></p>
  <p id="ready" class="note"></p>
  <ol id="carrying"></ol>

  <section id="heldBox" class="hidden">
    <h2>Held back</h2>
    <p class="note">These cases stay in your working tree as drafts in
    progress, and this press does not carry them. Put one back and it is
    recorded again, with the marks, the missing list and the notes you left
    on it.</p>
    <ol id="held"></ol>
  </section>

  <section id="waysOut">
    <h2>Written into your working tree</h2>
    <pre id="stageWritten"></pre>
    <h2>Open the pull request</h2>
    <p>One command carries every case above:</p>
    <pre id="stageCommand"></pre>
    <p>Or paste this into one you open yourself:</p>
    <pre id="stagePaste"></pre>
  </section>

  <div id="submitBox" class="hidden">
    <p class="note">Or let this open it for you, through the
    <code>gh</code> you are already signed in to. It runs the same checks
    first, pushes to your fork, and opens the pull request as you.</p>
    <p><button id="submit">Open the pull request as <!--submitter--></button></p>
    <pre id="result" class="hidden"></pre>
  </div>
</article>
</main>

<script nonce="__CSP_NONCE__">
const $ = (id) => document.getElementById(id);
const lines = (id) => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);

const TOKEN = <!--token-->;
const CAN_SUBMIT = <!--cansubmit-->;
const MIN_OWN_LIST = <!--minownlist-->;

// The case on the stage, and the rail as the server last described it.
let current = null;
let rows = [];

// Nothing the reader writes is held in this page, and nor is what they
// recorded: every word of it goes to the draft on the server, and the count
// in the footer and the list on the submit stage are both read back from the
// tree. So a closed browser costs nothing, and the list before the press is
// the submission the press builds.

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
  $("start").disabled = !firstToDo();
  railFooter(d.ready);
  select(current);
  return d;
}

// The pinned footer: the count of what is ready and the way to submit it, in
// one control. It is off at a count of zero, so it never offers a press with
// nothing behind it. The count reads for itself, as every other count here
// does — a branch on one is what this repository does not write. Named apart
// from the `ready` element, because an id is a global of its own.
function railFooter(count) {
  $("toSubmit").textContent = "Submit — " + count + " cases ready";
  $("toSubmit").classList.toggle("hidden", !count);
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

// The first case nobody has started, which is what the start button offers.
// A finished case presses and the walk steps over it, but it is not to do.
function firstToDo() {
  return rows.find(row => row.state === "todo");
}

// One step from where the reader stands, in the rail's own order, landing
// only on a case they may open. It reads the rail rather than the walkable
// list, because the reader can stand on a row the walk does not offer: a
// case somebody signed while the tab sat open. Next must still carry them
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
  layout($("partOne"), d.blocks);
  warn(d.moved);
  // A case takes one own list. Where this reader already holds a draft, the
  // box comes back filled and locked and the sets open, because the server
  // refuses a second list for the same case.
  if (d.own_list === null) return;
  $("own").value = d.own_list.join("\n");
  $("missing").value = d.missing.join("\n");
  $("notes").value = d.notes;
  lock();
  // A recorded case comes back with both parts and a button that says what a
  // second press does. It offers no discard: the record is in the working
  // tree, so throwing the draft away here would leave it with nothing behind
  // it, and the walk is what carries the reader on.
  finishedNow(d.state === "finished");
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
  await loadStage();
}

// What one press carries, read from the server rather than remembered. The
// list is the finished drafts, which is the submission the press builds
// itself from, so what the reader signs is what they read here. It also says
// how many cases stay unfinished, which is the failure a read that survives
// the process invites: believing you submitted five cases when you sent four.
async function loadStage() {
  const d = await (await fetch("/api/stage")).json();
  $("ready").textContent = d.ready.length + " cases go into one pull request. "
    + d.unfinished + " cases stay unfinished, and this press does not carry them.";
  $("carrying").replaceChildren(...d.ready.map(row => stageRow(row, "Drop", drop)));
  $("held").replaceChildren(...d.held_back.map(row => stageRow(row, "Put back", putBack)));
  $("heldBox").classList.toggle("hidden", !d.held_back.length);
  $("stageWritten").textContent = d.written.join("\n");
  $("stageCommand").textContent = d.command;
  $("stagePaste").textContent = d.paste;
  // With nothing to carry there is no way out to offer, so the three of them
  // go together. The count above still says what is left to do.
  $("waysOut").classList.toggle("hidden", !d.ready.length);
  $("submitBox").classList.toggle("hidden", !(CAN_SUBMIT && d.ready.length));
}

// One row of either list, with the control that moves it to the other one.
// The title is case prose, so it lands through textContent like every other
// piece of it, and the row carries no count for the same reason a rail row
// carries none.
function stageRow(row, label, act) {
  const item = document.createElement("li");
  const line = document.createElement("div");
  line.className = "line";
  const text = document.createElement("span");
  text.textContent = row.number + "  " + row.title;
  const press = document.createElement("button");
  press.textContent = label;
  press.addEventListener("click", () => act(row.case));
  line.append(text, press);
  item.appendChild(line);
  return item;
}

// A drop takes the record out of the working tree and puts the case back to
// *draft in progress*; a put back writes it again from the draft. Both move a
// row between the two lists on this page, so both re-read the rail and the
// stage — the footer's count moves with them.
async function stageAct(path, caseId) {
  const res = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({case: caseId}),
  });
  if (!res.ok) { $("ready").textContent = (await res.json()).detail; return; }
  await loadRail();
  await loadStage();
}

const drop = (caseId) => stageAct("/api/drop", caseId);
const putBack = (caseId) => stageAct("/api/put-back", caseId);

// The gate re-arms per case, so a case arriving on the stage arrives blind.
function blank() {
  $("partOne").textContent = "loading…";
  warn([]);
  $("partTwo").replaceChildren();
  for (const id of ["own", "missing", "notes"]) $(id).value = "";
  $("written").textContent = "";
  $("own").readOnly = false;
  gate();
  $("finish").textContent = "Record the sitting";
  // A blind case shows the placeholder where part two will open, so the case
  // reads the same whichever way the reader arrived at it.
  $("placeholder").classList.remove("hidden");
  for (const id of ["two", "done", "discardBox"]) $(id).classList.add("hidden");
}

function lock() {
  $("own").readOnly = true;
  $("lock").disabled = true;
  $("ownHint").textContent = "";
}

// How much the reader has written, counted the way the server counts it: the
// stripped lines joined, so blank lines and padding are worth nothing.
function typed() {
  return lines("own").join("").length;
}

// The press waits until the list says something. This is a courtesy and not
// the rule: the press is a request, the request is what opens the sets, and
// `/api/own-list` holds the same count.
//
// It counts and says nothing about the count. A reader writing their own list
// is the one moment of this method that has to be theirs, and a page telling
// them how many characters are left sets the length they write to.
function gate() {
  if ($("own").readOnly) return;  // the list is in; the press is spent
  $("lock").disabled = typed() < MIN_OWN_LIST;
  $("ownHint").textContent = "";  // a refusal they have started answering
}

$("own").addEventListener("input", gate);

// What the case on the stage offers once it is recorded, and what it offers
// while it is not. Spelled once, because the record and a re-opened case
// arrive at the same two answers by different routes.
function finishedNow(finished) {
  $("finish").textContent = finished
    ? "Re-record this sitting"
    : "Record the sitting";
  $("discardBox").classList.toggle("hidden", finished);
}

async function showSets(caseId) {
  const res = await fetch("/api/part-two?case=" + encodeURIComponent(caseId));
  const sets = await res.json();
  if (current !== caseId) return;
  if (!res.ok) { $("partOne").textContent = sets.detail; return; }
  // A target is anchored to its claim sentence and carries no position, so
  // the sentence is what pairs a recorded finding with the mark that answers
  // it. Two claims the identity rule calls one finding share one target, and
  // the second card says so rather than offering a second select that would
  // overwrite the first.
  const byClaim = new Map();
  for (const target of sets.marks) {
    for (const claim of target.claims) byClaim.set(claim, target);
  }
  const box = $("partTwo");
  box.replaceChildren();
  for (const part of Object.values(sets.frameworks)) {
    box.append(el("h3", "set", part.heading), el("p", "note", part.question));
    const answered = new Map();
    for (const group of part.groups) {
      box.append(el("h3", null, group.name));
      for (const record of group.records) {
        box.append(recordCard(record, byClaim.get(record.title), sets.values, answered));
      }
    }
  }
  $("placeholder").classList.add("hidden");
  $("two").classList.remove("hidden");
}

$("start").addEventListener("click", () => {
  const first = firstToDo();
  if (first) openCase(first.case);
});

$("previous").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("toSubmit").addEventListener("click", openSubmit);

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
  // Said beside the press rather than over the case: a refused list is a
  // normal thing to meet, and painting it into the stage would take the
  // system the reader is about to write about off the screen.
  if (!res.ok) { $("ownHint").textContent = (await res.json()).detail; return; }
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

// Case prose is data, so it lands through textContent rather than through any
// markup — the same rule the flat text obeyed, kept while the page gained a
// layout. Every builder below writes its own elements and puts the case's own
// words inside them, so a sentence spelling a tag arrives as those characters.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function sourceBlock(block) {
  const card = el("div", "card");
  const head = el("div", "head");
  head.append(el("h4", null, block.label), el("span", "num", block.source_kind));
  card.append(head, el("p", "hint", "Exactly what the service would receive."),
              el("pre", "verbatim", block.text));
  return card;
}

function tableBlock(block) {
  const out = document.createDocumentFragment();
  const head = el("tr");
  for (const name of block.headers) head.append(el("th", null, name));
  const body = el("tbody");
  for (const row of block.rows) {
    const line = el("tr");
    for (const cell of row) line.append(el("td", null, cell));
    body.append(line);
  }
  const thead = el("thead");
  thead.append(head);
  const grid = el("table");
  grid.append(thead, body);
  const scroll = el("div", "scroll");
  scroll.append(grid);
  out.append(el("h3", null, block.caption), scroll);
  return out;
}

function termsBlock(block) {
  const out = document.createDocumentFragment();
  out.append(el("h3", null, block.caption));
  if (block.hint) out.append(el("p", "hint", block.hint));
  const list = el("ul", "terms");
  for (const item of block.items) {
    const line = el("li");
    line.append(el("code", "id", item.term), " — ", item.text);
    list.append(line);
  }
  out.append(list);
  return out;
}

// One builder per block kind, keyed rather than branched: a kind the server
// grows and this table does not carry stops the page here, rather than
// dropping that part of the case off it in silence.
const BLOCKS = {source: sourceBlock, table: tableBlock, terms: termsBlock};

function layout(box, blocks) {
  box.replaceChildren();
  for (const block of blocks) {
    const build = BLOCKS[block.kind];
    // Said on the page rather than thrown. A throw here would leave the
    // reader a blank stage and put the reason somewhere only a developer
    // looks, and this reader is about to write a list about what they see.
    if (!build) {
      const gap = el("div", "card gap");
      gap.append(el("p", "hint", "This page has no layout for a "
        + block.kind + " block, so part of the case is missing from it."));
      box.append(gap);
      continue;
    }
    box.append(build(block));
  }
}

// One printed line of a record's named fields. A field says whether its values
// are identifiers, so this page spells them as code where the reading document
// spells them in backticks — one description of the record, two surfaces.
function fieldRow(row) {
  const line = el("li");
  row.forEach((field, n) => {
    if (n) line.append(" · ");
    line.append(el("span", "lbl", field.label));
    field.values.forEach((value, i) => {
      if (i) line.append(", ");
      line.append(field.code ? el("code", "id", value) : document.createTextNode(value));
    });
  });
  return line;
}

function recordCard(record, target, values, answered) {
  const card = el("div", "card rec");
  const head = el("div", "head");
  head.append(el("span", "num", record.label));
  if (record.identifier) head.append(el("code", "id", record.identifier));
  head.append(el("h4", null, record.title));
  card.append(head);
  if (record.fields.length) {
    const list = el("ul", "fields");
    for (const row of record.fields) list.append(fieldRow(row));
    card.append(list);
  }
  if (!target) {
    card.append(el("p", "aside", "No mark: the identity rule keys no recorded"
      + " finding for this claim, so nothing here would answer it."));
    return card;
  }
  const first = answered.get(target.fingerprint);
  if (first !== undefined) {
    card.append(el("p", "aside", "The same finding as " + first
      + " above, so the one mark there answers this too."));
    return card;
  }
  answered.set(target.fingerprint, record.label);
  const select = document.createElement("select");
  select.dataset.finding = target.fingerprint;
  select.id = "mark-" + target.fingerprint;
  for (const value of ["", ...values]) {
    const option = document.createElement("option");
    option.value = value;
    // The label is the value with a capital, rather than a second table of
    // words: a table would be one more thing to keep level with `MARKS`, and
    // this way a mark the method adds arrives here already spelled.
    option.textContent = value ? value[0].toUpperCase() + value.slice(1) : "—";
    select.append(option);
  }
  const label = el("label", null, "Your mark");
  label.htmlFor = select.id;
  const mark = el("div", "mark");
  mark.append(label, select);
  card.append(mark);
  return card;
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
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({
      case: caseId, marks: marksNow(), missing: lines("missing"),
      notes: $("notes").value,
    }),
  });
  const d = await res.json();
  if (!res.ok) { $("written").textContent = d.detail; $("done").classList.remove("hidden"); return; }
  warn(d.moved);
  $("written").textContent = d.written.join("\n");
  // Part two stays on the stage. The reader changes a mark, a missing line or
  // a note and presses again, and the second press replaces the entry the
  // first one appended rather than adding to it.
  finishedNow(true);
  $("done").classList.remove("hidden");
  // The row moves to *finished, not submitted* where it stands, and it still
  // presses — the record is in the working tree and has not merged. The
  // pinned footer counts one more case ready by the same read.
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
  // A draft that survived the delete. The pull request is open either way,
  // and the file is in a store only the reader can clear.
  if ((d.kept || []).length) report.push("", "these drafts would not delete:", ...d.kept);
  $("result").textContent = report.join("\n");
  // The carried drafts are gone, so their rows grey and the footer goes out.
  await loadRail();
  await loadStage();
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
    submitted_by: str,
    submitted_for: str | None = None,
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

    ``submitted_for`` defaults to the submitting account, which is the common
    shape: a person reads their own case and answers for it.
    """
    session = Session(
        root=root,
        submitted_by=submitted_by,
        submitted_for=submitted_for or submitted_by,
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
        "--submitted-by",
        help="your GitHub login: the account that carries the sitting and"
        " opens the pull request. Read from the authenticated `gh` when"
        " omitted, because it is the name the record carries either way.",
    )
    parser.add_argument(
        "--submitted-for",
        help="who is reading, when that is not you: a GitHub login, or"
        f" {ANONYMOUS!r} for a reader who takes part on no name of their own."
        " Defaults to --submitted-by. It records who read the case and grants"
        " nothing; the submitting account still answers for the sitting.",
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

    submitted_by = args.submitted_by or login
    if not submitted_by:
        print(
            "cannot read your gh login, so pass --submitted-by with the login"
            " the record should carry",
            file=sys.stderr,
        )
        return 1

    # Checked here, at the command line, so a value the record cannot hold
    # stops the session rather than the submission. The shape is the model's,
    # read off it rather than spelled again (A05).
    submitted_for = args.submitted_for or submitted_by
    if not is_submitted_for(submitted_for):
        print(
            f"{submitted_for!r} is not a GitHub login and is not"
            f" {ANONYMOUS!r}; --submitted-for takes one of those two",
            file=sys.stderr,
        )
        return 1

    # Only ever as yourself. A submission opened under a name `gh` does not
    # hold would fail #320's binding in CI, so offering the button would be
    # inviting a red PR. Who the read was *for* does not enter this: it binds
    # nothing, so it loosens nothing.
    can_submit = bool(login) and login == submitted_by and not args.no_submit
    session = build_session(root, submitted_by, submitted_for, args.case, can_submit)
    import uvicorn

    print(f"sitting as {sittings.naming(submitted_by, submitted_for)}")
    print(f"{len(session.offered)} cases to do")
    if can_submit:
        print("the page can open the pull request as you; --no-submit hides it")
    print(f"open http://{HOST}:{PORT}/")
    uvicorn.run(create_app(session), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
