"""The Case Sitting app: read a case, judge its reference sets, record it.

Run it from a clone, with no credentials of any kind::

    uv run python webapp/sitting.py

The app offers the whole corpus. A rail on the left lists every case with a
status, the case number and the title, and it never leaves. It is therefore
both how a reader starts and how they get back. A row carries no claim count
and no reason the case waits, because either would tell the reader how long to
make their own list before they have written it. ``--case`` preselects where
the rail opens, and grants nothing.

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

The button opens a pull request, through your own authenticated ``gh``. It is
not a hosted service: nothing is hosted, no credential is held here, and the app
binds to loopback. Every writing endpoint carries the origin check and page
token where appropriate, and request-controlled case ids resolve through the
offered-case allow-list.
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

from evals.harness import envelope as envelopes
from evals.harness import roster as rosters
from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from evals.harness.reference import (
    ANONYMOUS,
    CorpusError,
    corpus_refusal,
    is_submitted_for,
)
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
REPO_ROOT = Path(__file__).resolve().parents[1]
HELD = "`webapp/sitting.py`"
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
    roster: Roster
    drafts: Path
    preselect: str | None = None
    rows: tuple[sittings.Row, ...] = ()
    prepared: dict[str, sittings.Prepared] = field(default_factory=dict)
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    can_submit: bool = False
    dropped: set[str] = field(default_factory=set)

    @property
    def store(self) -> sittings.Store:
        return sittings.Store(
            root=self.root,
            submitted_by=self.submitted_by,
            submitted_for=self.submitted_for,
            drafts=self.drafts,
            held=HELD,
        )

    @property
    def document_name(self) -> str:
        return self.store.document_name

    @property
    def corpus_dir(self) -> Path:
        return self.store.corpus_dir

    @property
    def offered(self) -> frozenset[str]:
        return frozenset(row.case_id for row in self.rows if row.pressable)

    def refresh(self) -> tuple[sittings.Row, ...]:
        self.rows = sittings.rail(
            self.corpus_dir,
            self.roster,
            sittings.draft_states(self.drafts, self.submitted_by),
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


def create_app(session: Session) -> FastAPI:
    app = FastAPI(title="Case sitting", docs_url=None, redoc_url=None, openapi_url=None)
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
        rows = session.refresh()
        return JSONResponse(
            {
                "submitted_by": session.submitted_by,
                "submitted_for": session.submitted_for,
                "todo": sum(1 for row in rows if row.state == "todo"),
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
        prepared = _open(session, case)
        held = _draft(session, case)
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
                "state": held.state if held else None,
                "marks": work.marks,
                "missing": work.missing,
                "notes": work.notes,
                "moved": _moved(session, prepared, held),
            }
        )

    @app.post("/api/own-list")
    def own_list(request: Request, body: OwnList) -> JSONResponse:
        refuse_cross_origin(request)
        _require_token(request, session)
        prepared = _open(session, body.case)
        if _draft(session, body.case) is not None:
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
        _require_token(request, session)
        prepared = _open(session, body.case)
        held = _draft(session, body.case)
        if held is None:
            raise HTTPException(status_code=409, detail="no own list was written")
        moved = _moved(session, prepared, held)
        _record(session, prepared, held, body.marks, body.missing, body.notes)
        return JSONResponse(
            {
                "case": prepared.case_id,
                "written": _written(session, [prepared.case_id]),
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
        cases = [row.case_id for row in carried]
        return JSONResponse(
            {
                "submitted_by": session.submitted_by,
                "submitted_for": session.submitted_for,
                "ready": [_stage_row(row) for row in carried],
                "held_back": [_stage_row(row) for row in held_back],
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
        refuse_cross_origin(request)
        _require_token(request, session)
        if not session.can_submit:
            raise HTTPException(
                status_code=409,
                detail="no authenticated gh login, so there is nothing to"
                " submit as. Run the printed command yourself.",
            )
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
    sent = request.headers.get("x-sitting-token", "")
    if not secrets.compare_digest(sent.encode(), session.token.encode()):
        raise HTTPException(status_code=403, detail="wrong or missing page token")


def _open(session: Session, case_id: str) -> sittings.Prepared:
    if case_id not in session.offered:
        raise HTTPException(status_code=404, detail="not a case this sitting offers")
    try:
        return session.prepare(case_id)
    except sittings.SittingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _draft(session: Session, case_id: str) -> sittings.Draft | None:
    try:
        return session.draft(case_id)
    except sittings.DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _moved(
    session: Session, prepared: sittings.Prepared, draft: sittings.Draft | None
) -> list[str]:
    if draft is None:
        return []
    return sittings.moved(
        session.corpus_dir / prepared.case_id,
        {name: draft.opened_digests.get(name, "") for name in prepared.files},
    )


def _save(session: Session, draft: sittings.Draft) -> None:
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


def _delete_drafts(session: Session, cases: list[str]) -> list[str]:
    kept = []
    for case_id in cases:
        try:
            sittings.discard_draft(session.drafts, session.submitted_by, case_id)
        except (sittings.DraftError, OSError) as exc:
            kept.append(f"{case_id}: {exc}")
    return kept


def _stage_row(row: sittings.Row) -> dict[str, str]:
    return {"case": row.case_id, "number": row.number, "title": row.title}


def _written(session: Session, cases: list[str]) -> list[str]:
    return [
        *(
            f"evals/corpus/{case}/{name}"
            for case in cases
            for name in (session.document_name, "case.json")
        ),
        *([sittings.UNREVIEWED_FILE] if cases else []),
    ]


def _paste(session: Session, cases: list[str]) -> str:
    listed = "\n".join(f"- {case}" for case in cases)
    names = sittings.naming(session.submitted_by, session.submitted_for)
    return (
        f"Sitting: {names}, {len(cases)} cases\n\n"
        f"{listed}\n\n"
        "Each case above was read from the same source material used by the"
        " analysis. The reader wrote an independent list before the recorded"
        " framework findings were opened, then judged the findings they chose"
        " to assess. The filled document is committed as"
        f" `{session.document_name}`.\n\n"
        "The `reviews` entry in each `case.json` records the digest of every"
        " file as it stands in this PR, so a later edit to any of them puts"
        " that case back on the list."
    )


_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Case sitting</title>
<style nonce="__CSP_NONCE__">
  :root { color-scheme: light dark; --line: #8884; }
  body { font: 16px/1.55 system-ui, sans-serif; margin: 0; display: flex;
         align-items: stretch; min-height: 100vh; }
  nav { flex: 0 0 20rem; border-right: 1px solid var(--line); padding: 1.4rem 1rem;
        position: sticky; top: 0; align-self: flex-start; height: 100vh;
        box-sizing: border-box; display: flex; flex-direction: column; }
  #cases { flex: 1; overflow-y: auto; }
  .pin { flex: 0 0 auto; margin: .8rem 0 0; padding-top: .8rem;
         border-top: 1px solid var(--line); }
  .pin button { width: 100%; }
  main { flex: 1; padding: 2rem 1.5rem 6rem; max-width: 46rem; }
  h1 { font-size: 1.1rem; margin: 0 0 .2rem; }
  h2 { font-size: 1.2rem; }
  h3 { font-size: 1rem; }
  .sub { color: #7a7a7a; margin-top: 0; }
  ol { list-style: none; margin: 1rem 0 0; padding: 0; }
  li { margin: 0; }
  .row { display: flex; gap: .55rem; align-items: baseline; width: 100%;
         text-align: left; font: inherit; padding: .45rem .5rem; border: 0;
         border-radius: 6px; background: none; }
  button.row { cursor: pointer; }
  button.row:hover { background: #8882; }
  li.current .row { background: #8883; }
  .row.dead { color: #7a7a7a; }
  .dot { flex: 0 0 .55rem; height: .55rem; border-radius: 50%; }
  .dot.todo { background: #d08b28; }
  .dot.draft { background: #3f7fd0; }
  .dot.finished { background: #2f9e5e; }
  .dot.signed { background: #8886; }
  .dot.error { background: #c34a3c; }
  .label { flex: 1; min-width: 0; }
  .status { flex: 0 0 auto; color: #777; font-size: .72rem; white-space: nowrap; }
  pre { background: #8881; padding: 1rem; overflow-x: auto; white-space: pre-wrap;
        border-radius: 6px; font-size: .88rem; }
  textarea { width: 100%; min-height: 9rem; font: inherit; padding: .6rem;
             box-sizing: border-box; border: 1px solid var(--line); border-radius: 6px; }
  button { font: inherit; padding: .5rem 1rem; border-radius: 6px;
           border: 1px solid var(--line); background: #8882; cursor: pointer; }
  button:disabled { cursor: default; opacity: .5; }
  section { border-top: 1px solid var(--line); margin-top: 2rem; padding-top: 1rem; }
  header { display: flex; gap: 1rem; align-items: baseline;
           justify-content: space-between; }
  header h2 { margin: 0; }
  .walk { flex: 0 0 auto; margin: 0; display: flex; gap: .5rem; align-items: center; }
  .walk.bottom { margin-top: 2.5rem; padding-top: 1.25rem;
                 border-top: 1px solid var(--line); justify-content: flex-end; }
  .progress { color: #777; font-size: .85rem; margin: 0 .2rem; }
  .hidden { display: none; }
  .note { background: #8881; padding: .8rem 1rem; border-radius: 6px; }
  .frame { border: 1px dashed var(--line); border-radius: 6px; color: #7a7a7a;
           padding: 1.2rem 1rem; }
  select { font: inherit; padding: .2rem .4rem; border-radius: 6px;
           border: 1px solid var(--line); }
  .help-button { width: 100%; margin: .5rem 0 .7rem; }
  #guide { border: 1px solid var(--line); border-radius: 8px; padding: .8rem 1rem;
           margin: 0 0 1.5rem; background: #8881; }
  #guide summary { cursor: pointer; font-weight: 600; }
  #guide[open] summary { margin-bottom: .8rem; }
  #guide section { margin-top: 1.2rem; padding-top: .8rem; }
  #guide ul { margin: .5rem 0 0; padding-left: 1.25rem; }
  .example { margin: .65rem 0; padding-left: .8rem; border-left: 2px solid var(--line); }
  .example p { margin: .25rem 0; }
  .why { color: #777; font-size: .9rem; }
  .line { display: flex; gap: 1.25rem; align-items: center;
          justify-content: space-between; padding: .55rem 0; }
  .line > span { flex: 1; min-width: 0; }
  #submitBox { margin: .7rem 0 1.2rem; }
  .framework-picker { border: 0; margin: 1rem 0; padding: 0; }
  .framework-picker legend { font-weight: 600; margin-bottom: .35rem; }
  .framework-picker label { display: inline-flex; align-items: center; gap: .35rem;
                            margin-right: 1rem; }
  details.framework { border: 1px solid var(--line); border-radius: 8px;
                      padding: .75rem 1rem; margin: .8rem 0; }
  details.framework > summary { cursor: pointer; font-weight: 600; }
  details.framework[open] > summary { margin-bottom: .9rem; }
  details.framework .framework-body > h3:first-child { margin-top: .5rem; }
  .save-status { margin-left: .7rem; color: #777; font-size: .9rem; }

  .doc h3 { font-size: .74rem; text-transform: uppercase; letter-spacing: .08em;
            color: #8a8a8a; margin: 1.8rem 0 .6rem; font-weight: 600; }
  .doc h3:first-child { margin-top: .4rem; }
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
  <button id="helpToggle" class="help-button" aria-controls="guide" aria-expanded="true">Review guide</button>
  <ol id="cases"></ol>
  <p class="pin"><button id="toSubmit" class="hidden"></button></p>
</nav>

<main>
<details id="guide" open>
  <summary>How to complete a case sitting</summary>
  <p><b>Thank you for helping to make Work Agent better.</b></p>
  <p>This exercise gives the project an independent human check on what the
  analysis produced. Your judgments show where the model is reliably finding
  real issues, where it overreaches, where it repeats itself, and where it
  misses something a security reviewer notices. Those results can be measured
  over time and used to improve prompts, rules, evaluation data, and future
  analysis quality.</p>
  <p><b>Part 1</b> asks you to read the system description and write your own
  concerns before seeing the project's recorded findings. Keeping that step
  blind makes the comparison meaningful instead of letting the existing answer
  steer yours.</p>
  <p><b>Part 2</b> reveals the findings already recorded for the case, grouped by
  framework. Compare them with the same system description and mark the findings
  you review. You can review one framework or several; findings you do not mark
  simply remain unreviewed.</p>
  <ul>
    <li><b>Agree</b> when the underlying finding is real, supported by the case, and worth reporting.</li>
    <li><b>Reject</b> when the finding is unsupported, materially overstated, or simply incorrect.</li>
    <li><b>Duplicate</b> when another recorded entry already describes the same underlying issue.</li>
  </ul>
  <p><b>Judge the issue, not the wording.</b> Two differently worded findings can
  be duplicates, while two similar-looking findings can be distinct if they
  affect different assets, trust boundaries, or failure paths.</p>

  <section>
    <h3>Agree examples</h3>
    <div class="example">
      <p><b>Finding:</b> “The admin endpoint has no authorization check.”</p>
      <p class="why"><b>Why Agree:</b> The case explicitly says the endpoint is reachable after login but performs no role check. The claim is directly supported and distinct.</p>
    </div>
    <div class="example">
      <p><b>Finding:</b> “API tokens are stored in plaintext.”</p>
      <p class="why"><b>Why Agree:</b> The case states that raw tokens are stored in the database. The finding describes the actual control gap without adding assumptions.</p>
    </div>
  </section>

  <section>
    <h3>Reject examples</h3>
    <div class="example">
      <p><b>Finding:</b> “The application is vulnerable to SQL injection because it uses a SQL database.”</p>
      <p class="why"><b>Why Reject:</b> Using SQL does not show unsafe query construction. The case supplies no evidence for the claimed vulnerability.</p>
    </div>
    <div class="example">
      <p><b>Finding:</b> “All traffic is unencrypted.”</p>
      <p class="why"><b>Why Reject:</b> If the case explicitly says browser traffic uses TLS, “all traffic” is materially overstated even if an internal link is unspecified.</p>
    </div>
  </section>

  <section>
    <h3>Duplicate examples</h3>
    <div class="example">
      <p><b>Finding A:</b> “A user can fetch another user’s note by changing the note ID.”</p>
      <p><b>Finding B:</b> “The note-read endpoint does not verify ownership.”</p>
      <p class="why"><b>Why Duplicate:</b> Both describe the same missing ownership check on the same read path. Keep one underlying issue rather than counting wording twice.</p>
    </div>
    <div class="example">
      <p><b>Finding A:</b> “Revoked sessions remain usable.”</p>
      <p><b>Finding B:</b> “Logout does not invalidate the active session token.”</p>
      <p class="why"><b>Why Duplicate:</b> If both point to the same session invalidation failure, the second is another expression of the first, not a separate finding.</p>
    </div>
  </section>
  <p><b>Thank you for helping to make the project better.</b></p>
</details>

<div id="empty">
  <h2>Start a sitting</h2>
  <p class="note">Thank you for helping to make Work Agent better. Choose a case
  on the left or start with the first case to do. Your independent list comes
  first; the recorded findings remain hidden until you save it. Your review
  becomes evidence the project can use to measure and improve analysis quality.</p>
  <p><button id="start">Start with the first case to do</button></p>
</div>

<article id="case" class="hidden">
  <header>
    <h2 id="caseTitle"></h2>
    <p class="walk">
      <button id="previous">← Previous</button>
      <span id="progressTop" class="progress"></span>
      <button id="next">Next →</button>
    </p>
  </header>
  <p class="sub"><code id="caseId"></code>, read by <!--readby--></p>
  <p id="moved" class="note hidden"></p>

  <section id="one">
    <h2>Part 1 — your independent review</h2>
    <div id="partOne" class="doc">loading…</div>
    <h2>Your list, written first</h2>
    <p class="note">Before seeing the recorded findings, write the security
    concerns or unanswered questions you notice in the system description.
    One per line. This blind first pass gives the project a meaningful human
    comparison instead of an answer influenced by the model's output.</p>
    <textarea id="own" placeholder="one concern or question per line"></textarea>
    <p><button id="lock">Save my list and show Part 2</button>
    <span id="ownHint" class="gate"></span></p>
  </section>

  <section id="placeholder">
    <h2>Part 2 — compare with the recorded findings</h2>
    <p class="frame">After you save your independent list, the project's
    recorded findings appear here. They stay hidden until then so the first
    part remains an independent human check.</p>
  </section>

  <section id="two" class="hidden">
    <h2>Part 2 — compare with the recorded findings</h2>
    <p class="note">These are findings Work Agent previously recorded for this
    same case. Review one framework or several. Your Agree, Reject, and Duplicate
    decisions help measure what the analysis gets right, what it overstates, and
    where it repeats itself. Unmarked findings remain unreviewed.</p>
    <div id="frameworkPicker"></div>
    <div id="partTwo" class="doc"></div>
    <h2>What did your independent review find that these findings missed?</h2>
    <p class="note">If something on your original list is not represented by
    the framework findings you reviewed, enter it here, one per line. These are
    potential coverage gaps: they help identify issues the analysis may need to
    learn to find or express more clearly. Leave this blank if nothing is missing.</p>
    <textarea id="missing" placeholder="one potentially missed issue per line"></textarea>
    <h2>Notes</h2>
    <p id="markCounts" class="note"></p>
    <p class="hint">Counts are calculated automatically. Use notes only for
    context the structured choices do not capture.</p>
    <textarea id="notes" placeholder="optional context or explanation"></textarea>
    <p><button id="finish">Record the sitting</button><span id="saveStatus" class="save-status" role="status"></span></p>
  </section>

  <section id="discardBox" class="hidden">
    <h2>Discard this draft</h2>
    <p class="note">Throws away your own list, your marks, your missing list
    and your notes for this case, and puts it back on the list to do. No other
    case changes, and nothing in the repository changes.</p>
    <p><button id="discard">Discard my draft for this case</button></p>
  </section>

  <section id="done" class="hidden">
    <h2 id="doneTitle">Recorded</h2>
    <p id="summary" class="note"></p>
    <p>Written into your working tree:</p>
    <pre id="written"></pre>
    <p class="note">Thank you for helping to make the project better. Continue
    to the next case when you are ready. The last Next ends at the submit stage,
    where one pull request can carry every case you recorded.</p>
  </section>

  <p class="walk bottom">
    <button id="previousBottom">← Previous</button>
    <span id="progressBottom" class="progress"></span>
    <button id="nextBottom">Next →</button>
  </p>
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
    <div id="submitBox">
      <p><button id="submit">Open pull request as <!--submitter--></button></p>
      <p id="submitHint" class="hint"></p>
      <pre id="result" class="hidden"></pre>
    </div>
    <p>If you prefer the command line, run:</p>
    <pre id="stageCommand"></pre>
    <p>Or paste this into a pull request you open yourself:</p>
    <pre id="stagePaste"></pre>
  </section>
</article>
</main>

<script nonce="__CSP_NONCE__">
const $ = (id) => document.getElementById(id);
const lines = (id) => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
const TOKEN = <!--token-->;
const CAN_SUBMIT = <!--cansubmit-->;
const MIN_OWN_LIST = <!--minownlist-->;
let current = null;
let rows = [];
let queued = 0;

function queueSave() {
  clearTimeout(queued);
  queued = setTimeout(saveDraft, 600);
}

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
  if (current) updateWalk(current);
  return d;
}

function railFooter(count) {
  $("toSubmit").textContent = "Submit — " + count + " cases ready";
  $("toSubmit").classList.toggle("hidden", !count);
}

function statusText(state) {
  return ({
    todo: "Not reviewed",
    draft: "In progress",
    finished: "Reviewed",
    signed: "Submitted",
    error: "Error",
  })[state] || state;
}

function railRow(row) {
  const item = document.createElement("li");
  item.title = row.status;
  item.dataset.case = row.case;
  const dot = document.createElement("span");
  dot.className = "dot " + row.state;
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = row.number + "  " + row.title;
  const status = document.createElement("span");
  status.className = "status";
  status.textContent = statusText(row.state);
  const press = document.createElement(row.pressable ? "button" : "span");
  press.className = row.pressable ? "row" : "row dead";
  press.append(dot, label, status);
  if (row.pressable) press.addEventListener("click", () => openCase(row.case));
  item.appendChild(press);
  return item;
}

function select(caseId) {
  for (const item of $("cases").children) {
    item.classList.toggle("current", item.dataset.case === caseId);
  }
}

function walkable() {
  return rows.filter(row => row.pressable);
}

function firstToDo() {
  return rows.find(row => row.state === "todo");
}

function updateWalk(caseId) {
  const list = walkable();
  const index = list.findIndex(row => row.case === caseId);
  const text = index >= 0 ? "Case " + (index + 1) + " of " + list.length : "";
  $("progressTop").textContent = text;
  $("progressBottom").textContent = text;
  const first = list[0];
  const previousDisabled = !first || first.case === caseId;
  $("previous").disabled = previousDisabled;
  $("previousBottom").disabled = previousDisabled;
}

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

function warn(files) {
  const box = $("moved");
  box.classList.toggle("hidden", !files.length);
  box.textContent = files.length
    ? "These files moved since you opened this case: " + files.join(", ")
      + ". Your list is untouched. Read them again, and decide whether it still answers the text."
    : "";
}

function setMarks(marks) {
  for (const select of document.querySelectorAll("select[data-finding]")) {
    select.value = marks[select.dataset.finding] || "";
  }
  updateMarkCounts();
}

async function openCase(caseId) {
  await saveDraft();
  current = caseId;
  select(caseId);
  blank();
  $("guide").open = false;
  $("helpToggle").setAttribute("aria-expanded", "false");
  $("empty").classList.add("hidden");
  $("submitStage").classList.add("hidden");
  $("case").classList.remove("hidden");
  updateWalk(caseId);
  const res = await fetch("/api/part-one?case=" + encodeURIComponent(caseId));
  if (current !== caseId) return;
  const d = await res.json();
  if (!res.ok) { $("partOne").textContent = d.detail; return; }
  $("caseTitle").textContent = d.title;
  $("caseId").textContent = d.case;
  layout($("partOne"), d.blocks);
  warn(d.moved);
  if (d.own_list === null) return;
  $("own").value = d.own_list.join("\n");
  $("missing").value = d.missing.join("\n");
  $("notes").value = d.notes;
  lock();
  finishedNow(d.state === "finished");
  await showSets(caseId);
  if (current !== caseId) return;
  setMarks(d.marks);
}

async function openSubmit() {
  await saveDraft();
  current = null;
  select(null);
  $("guide").open = false;
  $("helpToggle").setAttribute("aria-expanded", "false");
  $("empty").classList.add("hidden");
  $("case").classList.add("hidden");
  $("submitStage").classList.remove("hidden");
  await loadStage();
}

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
  $("waysOut").classList.toggle("hidden", !d.ready.length);
  $("submit").disabled = !(CAN_SUBMIT && d.ready.length);
  $("submitHint").textContent = CAN_SUBMIT
    ? "This uses the gh account already authenticated on this machine."
    : "Button unavailable because this session has no authenticated gh account. The command and manual options below still work.";
}

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

function blank() {
  $("partOne").textContent = "loading…";
  warn([]);
  $("partTwo").replaceChildren();
  $("frameworkPicker").replaceChildren();
  for (const id of ["own", "missing", "notes"]) $(id).value = "";
  $("written").textContent = "";
  $("summary").textContent = "";
  $("markCounts").textContent = "Review summary: 0 agree · 0 reject · 0 duplicate · 0 unmarked";
  $("saveStatus").textContent = "";
  $("doneTitle").textContent = "Recorded";
  $("own").readOnly = false;
  gate();
  $("finish").textContent = "Record the sitting";
  $("placeholder").classList.remove("hidden");
  for (const id of ["two", "done", "discardBox"]) $(id).classList.add("hidden");
}

function lock() {
  $("own").readOnly = true;
  $("lock").disabled = true;
  $("ownHint").textContent = "";
}

function typed() {
  return lines("own").join("").length;
}

function gate() {
  if ($("own").readOnly) return;
  $("lock").disabled = typed() < MIN_OWN_LIST;
  $("ownHint").textContent = "";
}

$("own").addEventListener("input", gate);

function finishedNow(finished) {
  $("finish").textContent = finished ? "Save changes" : "Record the sitting";
  $("discardBox").classList.toggle("hidden", finished);
}

function frameworkChoice(name, details) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = true;
  input.dataset.framework = name;
  input.addEventListener("change", () => {
    details.classList.toggle("hidden", !input.checked);
    if (input.checked) details.open = true;
    updateMarkCounts();
  });
  label.append(input, document.createTextNode(name.toUpperCase()));
  return label;
}

async function showSets(caseId) {
  const res = await fetch("/api/part-two?case=" + encodeURIComponent(caseId));
  const sets = await res.json();
  if (current !== caseId) return;
  if (!res.ok) { $("partOne").textContent = sets.detail; return; }
  const byClaim = new Map();
  for (const target of sets.marks) {
    for (const claim of target.claims) byClaim.set(claim, target);
  }
  const box = $("partTwo");
  const picker = $("frameworkPicker");
  box.replaceChildren();
  picker.replaceChildren();
  const fieldset = el("fieldset", "framework-picker");
  fieldset.append(el("legend", null, "Frameworks to review"));
  for (const [name, part] of Object.entries(sets.frameworks)) {
    const details = el("details", "framework");
    details.open = true;
    details.dataset.framework = name;
    details.append(el("summary", null, part.heading));
    const body = el("div", "framework-body");
    body.append(el("p", "note", part.question));
    const answered = new Map();
    for (const group of part.groups) {
      body.append(el("h3", null, group.name));
      for (const record of group.records) {
        body.append(recordCard(record, byClaim.get(record.title), sets.values, answered, name));
      }
    }
    details.append(body);
    box.append(details);
    fieldset.append(frameworkChoice(name, details));
  }
  picker.append(fieldset);
  $("placeholder").classList.add("hidden");
  $("two").classList.remove("hidden");
  updateMarkCounts();
}

$("helpToggle").addEventListener("click", () => {
  $("guide").open = !$("guide").open;
  $("helpToggle").setAttribute("aria-expanded", String($("guide").open));
  if ($("guide").open) $("guide").scrollIntoView({behavior: "smooth", block: "start"});
});
$("guide").addEventListener("toggle", () => {
  $("helpToggle").setAttribute("aria-expanded", String($("guide").open));
});

$("start").addEventListener("click", () => {
  const first = firstToDo();
  if (first) openCase(first.case);
});

for (const id of ["previous", "previousBottom"]) {
  $(id).addEventListener("click", () => step(-1));
}
for (const id of ["next", "nextBottom"]) {
  $(id).addEventListener("click", () => step(1));
}
$("toSubmit").addEventListener("click", openSubmit);

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
  if (!res.ok) { $("ownHint").textContent = (await res.json()).detail; return; }
  lock();
  $("discardBox").classList.remove("hidden");
  await showSets(caseId);
  $("two").scrollIntoView({behavior: "smooth"});
});

for (const name of ["input", "change"]) {
  $("two").addEventListener(name, () => {
    updateMarkCounts();
    queueSave();
    $("saveStatus").textContent = "";
  });
}

$("discard").addEventListener("click", async () => {
  const caseId = current;
  if (!confirm("Throw away your draft for this case? This cannot be undone.")) return;
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

const BLOCKS = {source: sourceBlock, table: tableBlock, terms: termsBlock};

function layout(box, blocks) {
  box.replaceChildren();
  for (const block of blocks) {
    const build = BLOCKS[block.kind];
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

function recordCard(record, target, values, answered, framework) {
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
  select.dataset.framework = framework;
  select.id = "mark-" + target.fingerprint;
  for (const value of ["", ...values]) {
    const option = document.createElement("option");
    option.value = value;
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

function selectedFrameworks() {
  return new Set(
    [...document.querySelectorAll("input[data-framework]")]
      .filter(input => input.checked)
      .map(input => input.dataset.framework)
  );
}

function visibleSelects() {
  const selected = selectedFrameworks();
  return [...document.querySelectorAll("select[data-finding]")]
    .filter(select => selected.has(select.dataset.framework));
}

function marksNow() {
  const marks = {};
  for (const select of visibleSelects()) {
    if (select.value) marks[select.dataset.finding] = select.value;
  }
  return marks;
}

function markSummary() {
  const counts = {agree: 0, reject: 0, duplicate: 0};
  const selects = visibleSelects();
  for (const select of selects) {
    if (select.value in counts) counts[select.value] += 1;
  }
  const marked = counts.agree + counts.reject + counts.duplicate;
  return "Review summary: " + counts.agree + " agree · " + counts.reject
    + " reject · " + counts.duplicate + " duplicate · "
    + (selects.length - marked) + " unmarked";
}

function updateMarkCounts() {
  $("markCounts").textContent = markSummary();
}

$("finish").addEventListener("click", async () => {
  const caseId = current;
  const wasFinished = $("finish").textContent === "Save changes";
  $("finish").disabled = true;
  $("saveStatus").textContent = wasFinished ? "Saving changes…" : "Recording…";
  const res = await fetch("/api/finish", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({
      case: caseId, marks: marksNow(), missing: lines("missing"),
      notes: $("notes").value,
    }),
  });
  const d = await res.json();
  $("finish").disabled = false;
  if (!res.ok) {
    $("saveStatus").textContent = "Could not save";
    $("written").textContent = d.detail;
    $("doneTitle").textContent = "Not recorded";
    $("done").classList.remove("hidden");
    return;
  }
  warn(d.moved);
  $("summary").textContent = markSummary();
  $("written").textContent = d.written.join("\n");
  $("doneTitle").textContent = wasFinished ? "Changes saved" : "Recorded";
  $("saveStatus").textContent = wasFinished ? "Changes saved" : "Recorded";
  finishedNow(true);
  $("done").classList.remove("hidden");
  await loadRail();
});

$("submit").addEventListener("click", async () => {
  if (!CAN_SUBMIT) return;
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
  if ((d.kept || []).length) report.push("", "these drafts would not delete:", ...d.kept);
  $("result").textContent = report.join("\n");
  await loadRail();
  await loadStage();
});

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

    root = REPO_ROOT
    if args.list:
        print("\n".join(sittings.unreviewed_cases(root)))
        return 0

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

    submitted_for = args.submitted_for or submitted_by
    if not is_submitted_for(submitted_for):
        print(
            f"{submitted_for!r} is not a GitHub login and is not"
            f" {ANONYMOUS!r}; --submitted-for takes one of those two",
            file=sys.stderr,
        )
        return 1

    can_submit = bool(login) and login == submitted_by and not args.no_submit
    try:
        session = build_session(
            root, submitted_by, submitted_for, args.case, can_submit
        )
    except CorpusError as exc:
        print(corpus_refusal(exc), file=sys.stderr)
        return 1
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
