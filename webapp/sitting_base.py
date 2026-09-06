"""The Case Sitting rules and routes, behind whichever surface serves them.

This module holds a sitting's session, its rail, and the HTTP routes that read
and write one. It has no entry point and no page of its own.
``webapp/sitting.py`` is the surface a reader runs; it passes its page to
:func:`create_app` and adds the routes only that surface needs.

The interface a surface uses is five names: :func:`create_app`,
:func:`build_session`, :func:`open_case`, :func:`held_draft`,
:func:`save_draft` and :func:`require_token`. Everything else here is private,
so a surface that needs a sixth thing asks for it rather than reaching in.

The session offers the whole corpus. A rail lists every case with a status, the
case number and the title. A row carries no claim count and no reason the case
waits, because either would tell the reader how long to make their own list
before they have written it. ``preselect`` says where the rail opens, and grants
nothing.

A case a sitting already clears is greyed and off the offered list once no draft
of it is left, whoever signed it. The draft is what re-opens a case, so a reader
who records one still presses their own row and records it again.
:func:`open_case` resolves every case id that arrives in a request against that
list, so the refusal a signed case needs is the same rule that refuses a case id
nobody wrote. The status reads :func:`evals.review_submission.current_reviews`,
and never the presence of a submission file: a submission whose digests drifted
clears nothing, and a rail keyed on the file would grey a case CI asks somebody
to read.

It is eval-side tooling rather than the product. Everything a reader writes here
lands in a **Draft Sitting** outside the repository, and a sitting becomes a
record only when its one submission file merges.

Nothing is hosted here and no credential is held. The app binds to loopback.
Every writing endpoint carries the origin check and the page token, and
request-controlled case ids resolve through the offered-case allow-list.
"""

from __future__ import annotations

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

from evals import review_submission as review_submissions
from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness.sitting import MIN_OWN_LIST, own_list_is_written
from webapp.page import (
    LOOPBACK_HOSTS,
    Grants,
    SecurityHeaders,
    refuse_cross_origin,
    render,
    response,
    script_json,
)

HOST = "127.0.0.1"
PORT = 8020
REPO_ROOT = Path(__file__).resolve().parents[1]
_PAGE_GRANTS = Grants(script=True, style=True, connect=True)
Line = envelopes.Line
CaseId = Annotated[str, Field(max_length=120)]


class Which(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: CaseId


class OwnList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: CaseId
    items: list[Line] = Field(default_factory=list, max_length=200)


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: CaseId
    marks: dict[Line, sittings.Mark] = Field(default_factory=dict, max_length=200)
    missing: list[Line] = Field(default_factory=list, max_length=200)
    notes: str = Field(default="", max_length=4000)


@dataclass
class Session:
    root: Path
    submitted_by: str
    submitted_for: str
    drafts: Path
    preselect: str | None = None
    rows: tuple[sittings.Row, ...] = ()
    prepared: dict[str, sittings.Prepared] = field(default_factory=dict)
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    dropped: set[str] = field(default_factory=set)

    @property
    def store(self) -> sittings.Store:
        return sittings.Store(
            root=self.root,
            submitted_by=self.submitted_by,
            submitted_for=self.submitted_for,
            drafts=self.drafts,
        )

    @property
    def corpus_dir(self) -> Path:
        return self.store.corpus_dir

    @property
    def offered(self) -> frozenset[str]:
        return frozenset(row.case_id for row in self.rows if row.pressable)

    def refresh(self) -> tuple[sittings.Row, ...]:
        signed, partial = review_submissions.rail_signatures(self.root)
        self.rows = sittings.rail(
            self.corpus_dir,
            signed,
            sittings.draft_states(self.drafts, self.submitted_by),
            partial,
        )
        return self.rows

    def carried(self) -> list[sittings.Row]:
        return [row for row in self.refresh() if row.state == "finished"]

    def draft(self, case_id: str) -> sittings.Draft | None:
        return sittings.load_draft(self.drafts, self.submitted_by, case_id)

    def prepare(self, case_id: str) -> sittings.Prepared:
        if case_id not in self.prepared:
            self.prepared[case_id] = sittings.prepare(self.corpus_dir / case_id)
        return self.prepared[case_id]


def create_app(session: Session, page: str, script: str) -> FastAPI:
    """The routes a sitting needs, over one surface's own page.

    ``page`` is the whole HTML template the index serves. It is a parameter
    because the surface that owns the page and the module that owns the rules
    are different modules: a surface passes its page in rather than writing
    this module's global, so the two cannot disagree about which page is live.

    The template must carry the five placeholders filled below, and
    :func:`~webapp.page.render` raises where it does not.
    """
    app = FastAPI(title="Case sitting", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return response(
            render(
                page,
                _PAGE_GRANTS,
                script=script,
                token=script_json(session.token),
                minownlist=script_json(MIN_OWN_LIST),
                # Read off the one table, so a mark the method adds
                # arrives on the page without a second list to edit.
                markvalues=script_json(list(sittings.MARKS)),
            )
        )

    @app.get("/api/rail")
    def rail() -> JSONResponse:
        rows = session.refresh()
        return JSONResponse(
            {
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
        prepared = open_case(session, case)
        held = held_draft(session, case)
        work = held or sittings.Draft(case=prepared.case_id)
        covered = review_submissions.current_reviews(session.root)
        return JSONResponse(
            {
                "case": prepared.case_id,
                "title": prepared.title,
                "blocks": prepared.part_one_blocks,
                "own_list": held.own_list if held else None,
                "state": held.state if held else None,
                # What a merged sitting already answered, and what still waits.
                # The page says so where a reader returning for one set would
                # otherwise wonder why the others arrive marked.
                "covered": sorted(covered.get(prepared.case_id, {})),
                "waiting": review_submissions.waiting(
                    session.root, prepared.case_id, covered
                ),
                "marks": work.marks,
                "missing": work.missing,
                "notes": work.notes,
                "moved": _moved(session, prepared, held),
            }
        )

    @app.post("/api/own-list")
    def own_list(request: Request, body: OwnList) -> JSONResponse:
        refuse_cross_origin(request)
        require_token(request, session)
        prepared = open_case(session, body.case)
        if held_draft(session, body.case) is not None:
            raise HTTPException(
                status_code=409,
                detail="that case already has your own list, and the recorded"
                " sets are open on it; a list written now would be evidence of"
                " an order that did not happen",
            )
        written = [item.strip() for item in body.items if item.strip()]
        typed = sum(len(item) for item in written)
        if not own_list_is_written(written):
            raise HTTPException(
                status_code=400,
                detail=f"your own list is {typed} characters and the sets open"
                f" at {MIN_OWN_LIST}; write what you think could go wrong"
                " first, because the sitting measures your list against theirs",
            )
        case_dir = session.corpus_dir / prepared.case_id
        try:
            opened = sittings.digests(case_dir, prepared.files)
        except sittings.SittingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        save_draft(
            session,
            sittings.Draft(
                case=prepared.case_id,
                own_list=written,
                opened_digests=opened,
            ),
        )
        return JSONResponse({"case": body.case})

    @app.post("/api/draft")
    def save_progress(request: Request, body: Progress) -> JSONResponse:
        refuse_cross_origin(request)
        require_token(request, session)
        open_case(session, body.case)
        held = held_draft(session, body.case)
        if held is None:
            raise HTTPException(
                status_code=409,
                detail="no draft holds that case; write your own list first",
            )
        held.marks = dict(body.marks)
        held.missing = list(body.missing)
        held.notes = body.notes
        save_draft(session, held)
        return JSONResponse({"case": body.case, "state": held.state})

    @app.get("/api/part-two")
    def part_two(case: CaseId) -> JSONResponse:
        prepared = open_case(session, case)
        if held_draft(session, case) is None:
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
        refuse_cross_origin(request)
        require_token(request, session)
        prepared = open_case(session, body.case)
        held = held_draft(session, body.case)
        if held is None:
            raise HTTPException(status_code=409, detail="no own list was written")
        moved = _moved(session, prepared, held)
        _record(session, prepared, held, body.marks, body.missing, body.notes)
        return JSONResponse(
            {
                "case": prepared.case_id,
                "moved": moved,
                "ready": len(session.carried()),
            }
        )

    @app.get("/api/stage")
    def stage() -> JSONResponse:
        rows = session.refresh()
        carried = [row for row in rows if row.state == "finished"]
        held_back = [
            row
            for row in rows
            if row.case_id in session.dropped and row.state == "draft"
        ]
        return JSONResponse(
            {
                "ready": [_stage_row(row) for row in carried],
                "held_back": [_stage_row(row) for row in held_back],
                "unfinished": sum(
                    1 for row in rows if row.state not in ("finished", "signed")
                ),
            }
        )

    @app.post("/api/drop")
    def drop(request: Request, body: Which) -> JSONResponse:
        refuse_cross_origin(request)
        require_token(request, session)
        prepared = open_case(session, body.case)
        held = held_draft(session, body.case)
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
        refuse_cross_origin(request)
        require_token(request, session)
        prepared = open_case(session, body.case)
        held = held_draft(session, body.case)
        if body.case not in session.dropped or held is None or held.state != "open":
            raise HTTPException(
                status_code=409, detail="that case is not held back from this press"
            )
        _record(session, prepared, held, held.marks, held.missing, held.notes)
        return JSONResponse({"case": prepared.case_id, "state": held.state})

    return app


def require_token(request: Request, session: Session) -> None:
    sent = request.headers.get("x-sitting-token", "")
    if not secrets.compare_digest(sent.encode(), session.token.encode()):
        raise HTTPException(status_code=403, detail="wrong or missing page token")


def open_case(session: Session, case_id: str) -> sittings.Prepared:
    if case_id not in session.offered:
        raise HTTPException(status_code=404, detail="not a case this sitting offers")
    try:
        return session.prepare(case_id)
    except sittings.SittingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def held_draft(session: Session, case_id: str) -> sittings.Draft | None:
    """This reader's draft of one case, or the refusal an unreadable one gets.

    **A case a merged sitting covers in part re-opens on that sitting.** The
    reader comes back when the case gains a **Framework**, and by then they
    have read the recorded sets, so a list written now would be evidence of an
    order that did not happen. The list written blind rides forward locked,
    with the marks already made, and only the new set is theirs to answer.

    The resumed draft is written to the store the first time it is asked for,
    because every later request — the sets, a save, the record — asks the
    store and not the corpus. A draft that exists is what opens the sets and
    refuses a second own list, so the resumed one holds the same gate.
    """
    try:
        held = session.draft(case_id)
    except sittings.DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if held is not None:
        return held
    merged = review_submissions.current_for_case(session.root, case_id)
    if merged is None:
        return None
    try:
        prepared = session.prepare(case_id)
        opened = sittings.digests(session.corpus_dir / prepared.case_id, prepared.files)
    except sittings.SittingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    resumed = sittings.Draft(
        case=prepared.case_id,
        own_list=list(merged.answers.own_list),
        opened_digests=opened,
    )
    resumed.marks = dict(merged.answers.marks)
    resumed.missing = list(merged.answers.missing)
    save_draft(session, resumed)
    return resumed


def _moved(
    session: Session, prepared: sittings.Prepared, draft: sittings.Draft | None
) -> list[str]:
    if draft is None:
        return []
    # Over the files the draft pinned, and no others. A required file the
    # draft never pinned is one the case gained since — a new set, which is
    # not a file that moved under the reader. Filtered to the case's own list,
    # so no name the draft holds is read as a path. `sitting_problems` reads
    # a merged submission by the same rule.
    return sittings.moved(
        session.corpus_dir / prepared.case_id,
        {
            name: digest
            for name, digest in draft.opened_digests.items()
            if name in prepared.files
        },
    )


def save_draft(session: Session, draft: sittings.Draft) -> None:
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
    try:
        sittings.finish(
            session.store, prepared, held, marks=marks, missing=missing, notes=notes
        )
    except (sittings.SittingError, sittings.DraftError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.dropped.discard(prepared.case_id)


def _stage_row(row: sittings.Row) -> dict[str, str]:
    return {"case": row.case_id, "number": row.number, "title": row.title}


def build_session(
    root: Path,
    submitted_by: str,
    submitted_for: str | None = None,
    case: str | None = None,
    drafts: Path | None = None,
) -> Session:
    """The session a surface serves, with its rail already read."""
    session = Session(
        root=root,
        submitted_by=submitted_by,
        submitted_for=submitted_for or submitted_by,
        drafts=drafts or sittings.draft_root(),
        preselect=case,
    )
    session.refresh()
    if case is not None and case not in {row.case_id for row in session.rows}:
        raise SystemExit(f"no case {case!r} under evals/corpus/")
    if case in session.offered:
        session.prepare(case)
    return session
