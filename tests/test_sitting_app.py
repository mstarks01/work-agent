"""The Case Sitting app, and the domain logic under it.

The property that carries this module is the method's one rule: **the recorded
sets are not reachable until the reader's own list is in.** It is enforced by
the server, not by asking, so it is tested the way the review app's
configuration-blindness is — by checking the payload cannot carry it.

The rest is what a sitting writes: the filled document, the append-only entry
with a digest per file read, and the UNREVIEWED line. Everything a person
doing this
by hand would write, so ``submit sitting`` cannot tell the two paths apart.

Deterministic, offline and credential-free, like the act itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evals import verify_corpus
from evals.harness import sitting as sittings
from evals.harness.reference import CorpusError
from webapp.page import client_script
from webapp.sitting import _PAGE, MIN_OWN_LIST, build_session, create_app
from webapp.sitting import main as app_main

LOOPBACK = "http://127.0.0.1:8020"
CASE = "02-iot-fleet-telemetry"
OTHER = "03-batch-data-pipeline"

#: An own list long enough to open the recorded sets. Most tests here measure
#: something other than the length gate, so they post this and get on with it.
OWN_LIST = ["a spoofed device"]
#: The same, for ``OTHER``, so a test that writes for both cases can tell the
#: two lists apart in the file each one lands in.
OTHER_LIST = ["a bad row in the batch"]

#: The two real cases the throwaway tree holds. Two rather than one, because
#: the rail is a list and a list of one proves nothing about order, about the
#: count of what is left, or about a row the reader may not press.
CASES = (CASE, OTHER)

#: What a browser puts on every request this page makes. Every writing endpoint
#: refuses without it, so the clients carry it and the tests that care about
#: its absence set their own.
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}

#: The tree's own roster. Written here rather than copied from the clone, so a
#: test that needs a rostered reader does not wait on who the project rosters.
ROSTER = """version = 1

[voters.ada]
standing = "contributor"

[voters.sam]
standing = "maintainer"
"""


def browser(session):
    """A client carrying what a browser reading this page carries.

    Both controls, because both writing endpoints take both: the origin
    header a browser sets, and the per-process token the page holds. A test
    that wants one of them missing builds its own client and says so.
    """
    return TestClient(
        create_app(session),
        base_url=LOOPBACK,
        headers={**SAME_ORIGIN, "X-Sitting-Token": session.token},
    )


def drafts_root(tree: Path) -> Path:
    """Where a test's **Draft Sitting**s live.

    Beside the throwaway clone rather than inside it, because that is where a
    real store sits — and it is a temporary directory rather than a real home
    directory, which is the whole reason the root is a field the caller sets.
    """
    return tree.parent / "state" / "sittings"


def session_for(tree, reviewer="ada", case=None, can_submit=False, read_for=None):
    """One session over the throwaway tree, with its own draft store."""
    return build_session(
        tree, reviewer, read_for, case, can_submit, drafts=drafts_root(tree)
    )


def draft_file(tree, case, reviewer="ada") -> Path:
    """One reader's draft of one case, as the store files it."""
    return drafts_root(tree) / reviewer / f"{case}.json"


def read_and_record(app, case=CASE, notes="21 agree", missing=()):
    """One whole sitting through the app: the own list, then the record.

    The shortest route to a finished draft, which is the one thing the submit
    stage, the pinned footer and the drop are all about.
    """
    app.post("/api/own-list", json={"case": case, "items": OWN_LIST})
    app.get(f"/api/part-two?case={case}")
    return app.post(
        "/api/finish",
        json={"case": case, "marks": {}, "missing": list(missing), "notes": notes},
    )


def build_tree(tmp_path):
    """A throwaway repo holding two real corpus cases, a roster and the list.

    A function as well as a fixture, because ``tests/test_sitting.py`` sits
    with the same corpus one layer down, against the module rather than the
    app.
    """
    root = Path(__file__).resolve().parents[1]
    tmp_path = tmp_path / "clone"
    (tmp_path / "evals" / "corpus").mkdir(parents=True)
    for case in CASES:
        source = root / "evals" / "corpus" / case
        for item in source.rglob("*"):
            target = tmp_path / "evals" / "corpus" / case / item.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.is_file():
                target.write_bytes(item.read_bytes())
    (tmp_path / "evals" / "review").mkdir(parents=True)
    (tmp_path / "evals" / "review" / "voters.toml").write_text(ROSTER, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_case_review.py").write_text(
        "UNREVIEWED: dict[str, str] = {\n"
        f'    "{CASE}": (\n'
        '        "18 STRIDE claims and 8 ASVS records, unread."\n'
        "    ),\n"
        f'    "{OTHER}": "17 STRIDE claims, unread.",\n'
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path)


def sign(tree, case, reviewer):
    """Give one case a sitting that clears it, as a real reader would leave it.

    Every required file read at the bytes it holds now, a rostered reviewer,
    and the filled document beside the case — which is what
    :func:`evals.harness.sitting.clears` asks for.
    """
    case_dir = tree / "evals" / "corpus" / case
    name = sittings.document_name(reviewer)
    (case_dir / name).write_text(f"# read by {reviewer}\n", encoding="utf-8")
    declared = json.loads((case_dir / "case.json").read_text("utf-8"))["frameworks"]
    read = sittings.read_records(
        case_dir, sittings.required_files(item["name"] for item in declared)
    )
    sittings.record(case_dir, reviewer, reviewer, read, name, "")


@pytest.fixture
def client(tree):
    session = session_for(tree, "ada", CASE)
    return browser(session), session, tree


class TestTheOwnListRuleIsEnforced:
    def test_part_two_is_refused_before_the_own_list(self, client):
        """The method's only rule, and the reason it is server-side."""
        app, _, _ = client
        refused = app.get(f"/api/part-two?case={CASE}")
        assert refused.status_code == 409
        assert "own list first" in refused.json()["detail"]

    def test_part_one_carries_no_reference_set(self, client):
        """The document prints the own list above the sets, so the payload
        that comes before the list must not carry them."""
        app, _, _ = client
        body = app.get(f"/api/part-one?case={CASE}").json()
        blob = json.dumps(body)
        claims = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "evals"
                / "corpus"
                / CASE
                / "claims"
                / "stride.json"
            ).read_text(encoding="utf-8")
        )
        first = claims[0]["claim"]
        assert first not in blob, "the recorded claim reached part one"

    def test_the_sets_arrive_once_the_list_is_in(self, client):
        app, _, _ = client
        app.post(
            "/api/own-list", json={"case": CASE, "items": ["someone spoofs a device"]}
        )
        sets = app.get(f"/api/part-two?case={CASE}").json()["frameworks"]
        assert "stride" in sets
        records = [
            record for group in sets["stride"]["groups"] for record in group["records"]
        ]
        assert records, "the recorded set arrived with no record in it"

    def test_finishing_without_a_list_is_refused(self, client):
        app, _, _ = client
        refused = app.post("/api/finish", json={"case": CASE})
        assert refused.status_code == 409

    def test_a_list_too_short_to_say_anything_is_refused(self, client):
        """A gate nothing has to pass is not a gate.

        The press opened the recorded sets on an empty box, so a reader could
        reach them with one click and the sitting then measured their nothing
        against the recorded set. The count is here rather than only on the
        page, because the press is a request and the request is what opens
        the sets.
        """
        app, _, _ = client
        refused = app.post("/api/own-list", json={"case": CASE, "items": ["short"]})
        assert refused.status_code == 400
        assert str(MIN_OWN_LIST) in refused.json()["detail"]
        assert app.get(f"/api/part-two?case={CASE}").status_code == 409

    def test_the_count_reads_the_words_and_not_the_padding(self, client):
        """Blank lines and spaces open nothing; the server counts what the
        page counts, which is the stripped lines joined."""
        app, _, _ = client
        padding = ["   ", "", "  a  ", "\t"]
        refused = app.post("/api/own-list", json={"case": CASE, "items": padding})
        assert refused.status_code == 400
        assert "1 characters" in refused.json()["detail"]

    def test_a_list_long_enough_opens_the_sets(self, client):
        app, _, _ = client
        written = app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert written.status_code == 200
        assert app.get(f"/api/part-two?case={CASE}").status_code == 200

    def test_the_page_takes_the_count_from_the_server(self, client):
        """One count, injected. A second copy written into the script would
        drift from the endpoint that actually holds the rule."""
        app, _, _ = client
        assert f"const MIN_OWN_LIST = {MIN_OWN_LIST};" in app.get("/").text


class TestWhatASittingWrites:
    def finish(self, app):
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        app.get(f"/api/part-two?case={CASE}")
        return app.post(
            "/api/finish",
            json={
                "case": CASE,
                "marks": {},
                "missing": ["nothing about the fleet key"],
                "notes": "21 agree",
            },
        )

    def test_the_filled_document_is_written_as_evidence(self, client):
        app, _, tree = client
        self.finish(app)
        document = tree / "evals" / "corpus" / CASE / "REVIEW-ada.md"
        assert document.is_file()
        text = document.read_text(encoding="utf-8")
        assert "a spoofed device" in text, "the own list is the evidence"
        assert "nothing about the fleet key" in text

    def test_the_entry_records_a_digest_per_required_file(self, client):
        app, _, tree = client
        self.finish(app)
        case_dir = tree / "evals" / "corpus" / CASE
        entry = json.loads((case_dir / "case.json").read_text("utf-8"))["reviews"][-1]
        assert entry["submitted_by"] == "ada"
        assert entry["document"] == "REVIEW-ada.md"
        read = {item["file"]: item["sha256"] for item in entry["read"]}
        declared = json.loads((case_dir / "case.json").read_text("utf-8"))["frameworks"]
        required = sittings.required_files(item["name"] for item in declared)
        assert set(read) == set(required)
        for name, digest in read.items():
            actual = hashlib.sha256((case_dir / name).read_bytes()).hexdigest()
            assert digest == actual, f"{name}'s digest does not match the bytes"

    def test_the_required_files_derive_from_the_declaration(self, client):
        """A case that gains a package requires its set read, with no table edit."""
        _, _, tree = client
        case_dir = tree / "evals" / "corpus" / CASE
        declared = json.loads((case_dir / "case.json").read_text("utf-8"))["frameworks"]
        files = sittings.required_files(item["name"] for item in declared)
        for framework in declared:
            assert f"claims/{framework['name']}.json" in files

    def test_the_unreviewed_line_is_cleared_and_the_others_are_not(self, client):
        app, _, tree = client
        self.finish(app)
        listing = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        assert CASE not in listing
        assert "03-batch-data-pipeline" in listing, "only this case comes off the list"

    def test_the_reviews_list_is_append_only(self, client):
        app, _, tree = client
        case_dir = tree / "evals" / "corpus" / CASE
        meta = json.loads((case_dir / "case.json").read_text("utf-8"))
        meta["reviews"] = [
            {
                "submitted_by": "sam",
                "submitted_for": "sam",
                "date": "2026-08-01",
                "read": [{"file": "source.md", "sha256": "0" * 64}],
                "document": "REVIEW-sam.md",
                "notes": "",
            }
        ]
        (case_dir / "case.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        self.finish(app)
        after = json.loads((case_dir / "case.json").read_text("utf-8"))["reviews"]
        assert len(after) == 2
        assert after[0]["submitted_by"] == "sam", "the recorded sitting was rewritten"

    def test_it_answers_with_the_paths_it_wrote(self, client):
        """The command and the paste text moved to the submit stage.

        One press carries every case, so there is one command and one paste
        text for the session rather than one per case. What a record answers
        with is what that record wrote.
        """
        app, _, _ = client
        payload = self.finish(app).json()
        assert f"evals/corpus/{CASE}/case.json" in payload["written"]
        assert f"evals/corpus/{CASE}/REVIEW-ada.md" in payload["written"]
        assert sittings.UNREVIEWED_FILE in payload["written"]
        assert payload["ready"] == 1, "the pinned footer counts this case"


class TestThePosture:
    def test_a_rebound_host_is_refused(self, tree):
        """This app writes to the corpus, so it gets the same Host check."""
        session = session_for(tree, "ada", CASE)
        app = TestClient(create_app(session), base_url="http://attacker.example")
        assert app.get("/").status_code == 400
        assert (
            app.post(
                "/api/own-list", json={"case": CASE, "items": OWN_LIST}
            ).status_code
            == 400
        )

    def test_the_submitting_account_cannot_be_set_from_a_request(self, client):
        """#320's binding: a browser field naming the account would break it."""
        app, _, _ = client
        refused = app.post(
            "/api/own-list",
            json={"case": CASE, "items": OWN_LIST, "submitted_by": "sam"},
        )
        assert refused.status_code == 422

    def test_the_page_carries_a_nonce_policy(self, client):
        app, _, _ = client
        page = app.get("/")
        assert "Content-Security-Policy" in page.headers
        assert "default-src 'none'" in page.headers["Content-Security-Policy"]

    def test_the_page_cannot_be_framed(self, client):
        """The control the submit button's other four rest on.

        A press inside somebody else's frame arrives same-origin and carries
        the page token, because the page it comes from really is this one. So
        framing beats the header check and the token together, and only this
        directive stops it. It does not fall back to ``default-src``.
        """
        app, _, _ = client
        csp = app.get("/").headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp

    def test_every_writing_endpoint_refuses_a_cross_site_request(self, client):
        """Not only ``/api/submit``.

        ``/api/finish`` writes the document, appends to ``case.json`` and sets
        the flag ``/api/submit`` tests, so a foreign page that reaches it
        decides what a later press publishes. ``/api/own-list`` satisfies the
        method's one rule, so a foreign page that reaches it opens the recorded
        sets for whoever asks next.
        """
        app, session, tree = client
        writes = {
            "/api/own-list": {"case": CASE, "items": OWN_LIST},
            "/api/finish": {
                "case": CASE,
                "marks": {},
                "missing": [],
                "notes": "not mine",
            },
        }
        for path, body in writes.items():
            for site in ("cross-site", "same-site", "none"):
                refused = app.post(path, json=body, headers={"Sec-Fetch-Site": site})
                assert refused.status_code == 403, f"{path} accepted a {site} request"

        assert not draft_file(tree, CASE).exists(), "a refused request wrote a draft"
        assert not session.carried(), "a refused request recorded the sitting"
        assert not (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").exists()

    def test_every_writing_endpoint_carries_the_page_token(self, tree):
        """Not only the ones that name a case.

        ``/api/finish`` writes the reading document, appends to ``case.json``
        and puts the case on the list the press carries, and it writes the
        draft under the reader's own store. It asks a request the same
        question every other write does.
        """
        session = session_for(tree, "ada", CASE)
        untokened = TestClient(
            create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN
        )
        writes = {
            "/api/own-list": {"case": CASE, "items": OWN_LIST},
            "/api/draft": {"case": CASE, "marks": {}, "missing": [], "notes": ""},
            "/api/discard": {"case": CASE},
            "/api/finish": {"case": CASE, "marks": {}, "missing": [], "notes": ""},
            "/api/drop": {"case": CASE},
            "/api/put-back": {"case": CASE},
        }
        for path, body in writes.items():
            assert untokened.post(path, json=body).status_code == 403, path
        assert not draft_file(tree, CASE).exists()
        assert not (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").exists()

    def test_a_bare_post_is_refused_too(self, tree):
        """No header at all is not the same as ``same-origin``.

        The check admits exactly one value rather than rejecting a list, so
        anything that sends nothing is refused too. This builds its own client
        because the shared one carries the header on every request.
        """
        session = session_for(tree, "ada", CASE)
        app = TestClient(create_app(session), base_url=LOOPBACK)
        assert (
            app.post(
                "/api/own-list", json={"case": CASE, "items": OWN_LIST}
            ).status_code
            == 403
        )
        assert not draft_file(tree, CASE).exists()

    def test_the_page_token_is_a_javascript_literal(self, client):
        """A ``<script>`` block does not decode HTML entities.

        So ``html.escape`` is the wrong escape for a value that lands in one,
        and the page carries these two as JSON. Nothing breaks today — a token
        and a boolean spell no quote — but the escape being right is a
        property of the page rather than of the values passing through it.
        """
        app, session, _ = client
        page = app.get("/").text
        assert f'const TOKEN = "{session.token}";' in page
        assert "const CAN_SUBMIT = false;" in page
        assert "&quot;" not in page.split("<script")[-1]

    def test_a_line_longer_than_the_cap_is_refused(self, client):
        """A cap on the list bounds how many lines arrive, not how long one is.

        Every one of them is written into the reading document, which the
        submit allow-list then carries into a pull request.
        """
        app, _, _ = client
        long_line = "x" * 501
        assert (
            app.post(
                "/api/own-list", json={"case": CASE, "items": [long_line]}
            ).status_code
            == 422
        )

        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        for body in (
            {"case": CASE, "marks": {}, "missing": [long_line], "notes": ""},
            {
                "case": CASE,
                "marks": {"stride:1": long_line},
                "missing": [],
                "notes": "",
            },
            {"case": CASE, "marks": {long_line: "ok"}, "missing": [], "notes": ""},
            {
                "case": CASE,
                "marks": {str(n): "ok" for n in range(201)},
                "missing": [],
                "notes": "",
            },
        ):
            assert app.post("/api/finish", json=body).status_code == 422


class TestTheSubmitButton:
    """The one endpoint in any app here that can act on GitHub as the operator.

    Four controls, and each is tested for refusing on its own: the Host check
    (#350), ``Sec-Fetch-Site``, the per-process page token, and the rule that
    the endpoint takes no arguments at all. Nothing here reaches GitHub — a
    refusal is asserted before the spine is ever called, and the one accepting
    test stubs the spine.
    """

    def sat(self, tree, can_submit=True):
        session = session_for(tree, "ada", CASE, can_submit=can_submit)
        app = browser(session)
        app.post("/api/own-list", json={"case": CASE, "items": ["a stolen key"]})
        app.get(f"/api/part-two?case={CASE}")
        app.post(
            "/api/finish", json={"case": CASE, "marks": {}, "missing": [], "notes": ""}
        )
        return app, session

    def headers(self, session):
        return {"Sec-Fetch-Site": "same-origin", "X-Sitting-Token": session.token}

    def test_a_cross_site_request_is_refused(self, tree):
        app, session = self.sat(tree)
        refused = app.post(
            "/api/submit",
            headers={"Sec-Fetch-Site": "cross-site", "X-Sitting-Token": session.token},
        )
        assert refused.status_code == 403

    def test_a_request_without_the_page_token_is_refused(self, tree):
        """A page that never read this one cannot have the token.

        Its own client, carrying the origin header and nothing else, because
        the shared one is a browser and a browser holds the token. The token
        is checked before the session is asked what it recorded, so a fresh
        session is enough to prove the refusal.
        """
        session = session_for(tree, "ada", CASE, can_submit=True)
        app = TestClient(create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN)
        assert app.post("/api/submit").status_code == 403

    def test_a_wrong_token_is_refused(self, tree):
        app, _ = self.sat(tree)
        refused = app.post(
            "/api/submit",
            headers={"Sec-Fetch-Site": "same-origin", "X-Sitting-Token": "guessed"},
        )
        assert refused.status_code == 403

    def test_a_rebound_host_is_refused_here_too(self, tree):
        session = session_for(tree, "ada", CASE, can_submit=True)
        app = TestClient(create_app(session), base_url="http://attacker.example")
        assert app.post("/api/submit", headers=self.headers(session)).status_code == 400

    def test_submitting_before_recording_is_refused(self, tree):
        session = session_for(tree, "ada", CASE, can_submit=True)
        app = TestClient(create_app(session), base_url=LOOPBACK)
        refused = app.post("/api/submit", headers=self.headers(session))
        assert refused.status_code == 409
        assert "record the sitting" in refused.json()["detail"]

    def test_without_a_gh_login_the_endpoint_is_closed(self, tree):
        app, session = self.sat(tree, can_submit=False)
        refused = app.post("/api/submit", headers=self.headers(session))
        assert refused.status_code == 409
        assert "nothing to" in refused.json()["detail"]

    def test_it_runs_the_same_spine_and_takes_no_arguments(self, tree, monkeypatch):
        """The submission is what the session recorded; the request steers nothing."""
        from evals.harness import submit as spine

        seen = {}

        def fake(root, kind, **kwargs):
            seen["root"], seen["kind"], seen["kwargs"] = root, kind, kwargs
            return spine.Outcome(
                author="ada",
                url="https://example.test/pr/9",
                closing="a maintainer reviews every line",
            )

        monkeypatch.setattr(spine, "submission", fake)
        app, session = self.sat(tree)
        answer = app.post(
            "/api/submit",
            headers=self.headers(session),
            json={"kind": "baseline", "path": "/etc/passwd"},
        )
        assert answer.status_code == 200
        assert seen["kind"] == "sitting", "the request cannot choose the kind"
        assert seen["root"] == tree
        assert answer.json()["url"] == "https://example.test/pr/9"

    def test_a_failed_checklist_comes_back_as_the_checklist(self, tree, monkeypatch):
        from evals.harness import submit as spine

        monkeypatch.setattr(
            spine,
            "submission",
            lambda root, kind, **kw: spine.Outcome(
                author="ada",
                checks=(spine.Check(name="the digests hold", problems=("source.md",)),),
            ),
        )
        app, session = self.sat(tree)
        answer = app.post("/api/submit", headers=self.headers(session))
        assert answer.status_code == 409
        assert answer.json()["checks"][0]["problems"] == ["source.md"]


class TestOnlyYourOwnEntryComesOff:
    """A sitting somebody else recorded is untouchable, checked where it is
    removed.

    Both the entry a re-record replaces and the entry a drop takes off reach
    the harness from a draft the reader owns, so the rule cannot be assumed
    of the caller that supplied the value.
    """

    def merged(self, tree, case=CASE, reviewer="sam"):
        """One entry by somebody else, partial so it clears nothing and the
        case still presses."""
        case_dir = tree / "evals" / "corpus" / case
        read = sittings.read_records(case_dir, ["source.md"])
        return case_dir, sittings.record(
            case_dir, reviewer, reviewer, read, f"REVIEW-{reviewer}.md", "theirs"
        )

    def reviewers(self, tree, case=CASE):
        path = tree / "evals" / "corpus" / case / "case.json"
        return [
            e["submitted_by"] for e in json.loads(path.read_text("utf-8"))["reviews"]
        ]

    def test_a_record_refuses_to_replace_it(self, tree):
        case_dir, theirs = self.merged(tree)
        read = sittings.read_records(case_dir, ["source.md"])
        with pytest.raises(sittings.SittingError, match="not yours to take off"):
            sittings.record(
                case_dir, "ada", "ada", read, "REVIEW-ada.md", "", replaces=theirs
            )
        assert self.reviewers(tree) == ["sam"]

    def test_an_unrecord_refuses_to_remove_it(self, tree):
        case_dir, theirs = self.merged(tree)
        with pytest.raises(sittings.SittingError, match="not yours to take off"):
            sittings.unrecord(case_dir, "ada", theirs)
        assert self.reviewers(tree) == ["sam"]

    def test_a_doctored_draft_cannot_delete_it_through_the_app(self, tree):
        """The whole route, as a reader with a hand-edited draft would take
        it: their own list, then a record pointed at somebody else's entry."""
        _, theirs = self.merged(tree)
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        path = draft_file(tree, CASE)
        held = json.loads(path.read_text("utf-8"))
        held["recorded"] = theirs
        path.write_text(json.dumps(held), encoding="utf-8")
        app.get(f"/api/part-two?case={CASE}")
        refused = app.post(
            "/api/finish", json={"case": CASE, "marks": {}, "missing": [], "notes": ""}
        )
        assert refused.status_code == 409
        assert "not yours to take off" in refused.json()["detail"]
        assert self.reviewers(tree) == ["sam"], "somebody else's sitting was removed"

    def test_the_last_case_comes_off_and_leaves_an_empty_table(self, tree):
        """The day every case is read. The table names nobody, and the reader
        who cleared the last line gets the same answer as every other."""
        for case in CASES:
            sittings.clear_unreviewed(tree, case)
        assert sittings.unreviewed_cases(tree) == []

    def test_the_unreviewed_cases_are_listed_in_file_order(self, tree):
        assert sittings.unreviewed_cases(tree) == [CASE, "03-batch-data-pipeline"]


class TestTheMarks:
    """A mark per recorded finding, keyed by the finding's own identity.

    The by-hand document writes one ``> mark:`` slot per claim, so a browser
    sitting that recorded none recorded less than the shell path beside it.
    The key is the fingerprint rather than a position, because an insertion
    into a claim file moves every position and moves no fingerprint.
    """

    def marked(self, app, marks):
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        app.get(f"/api/part-two?case={CASE}")
        return app.post(
            "/api/finish",
            json={"case": CASE, "marks": marks, "missing": [], "notes": ""},
        )

    def test_part_two_offers_a_target_per_recorded_finding(self, client):
        app, _, _ = client
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        payload = app.get(f"/api/part-two?case={CASE}").json()
        assert payload["values"] == list(sittings.MARKS)
        offered = payload["marks"]
        assert offered, "part two offered nothing to mark"
        assert {target["framework"] for target in offered} == set(
            payload["frameworks"]
        ), "a declared framework offered no mark target"
        for target in offered:
            assert target["fingerprint"].startswith("v")
            assert target["claims"], "a target names no recorded claim"

    def test_one_finding_takes_one_mark_however_often_it_is_written(self, client):
        """The fingerprint is the identity, so it is what a target is keyed by."""
        _, session, _ = client
        keys = [target.fingerprint for target in session.prepared[CASE].mark_targets]
        assert len(keys) == len(set(keys))

    def test_a_value_outside_the_closed_set_is_refused(self, client):
        app, session, _ = client
        first = session.prepared[CASE].mark_targets[0].fingerprint
        assert self.marked(app, {first: "maybe"}).status_code == 422

    def test_a_mark_naming_no_recorded_finding_is_refused(self, client):
        app, _, tree = client
        refused = self.marked(app, {"v2:0000000000000000": "agree"})
        assert refused.status_code == 409
        assert "v2:0000000000000000" in refused.json()["detail"]
        assert not (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").exists()

    def test_each_framework_prints_its_own_marks(self, client):
        """Selected off the fingerprint's components, never off a key prefix."""
        app, session, tree = client
        first = {
            target.framework: target.fingerprint
            for target in reversed(session.prepared[CASE].mark_targets)
        }
        assert len(first) > 1, "this case declares one framework"
        self.marked(app, dict.fromkeys(first.values(), "reject"))

        text = (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").read_text("utf-8")
        for framework, fingerprint in first.items():
            head = text.split(f"## The recorded `{framework}` set")[1]
            section = head.split("\n---\n")[0]
            assert fingerprint in section, f"{framework}'s mark landed elsewhere"

    def test_a_sitting_with_no_marks_still_records(self, client):
        app, _, tree = client
        assert self.marked(app, {}).status_code == 200
        text = (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").read_text("utf-8")
        assert "### Marks" not in text

    def test_an_insertion_into_a_claim_file_re_points_no_mark(self, client):
        """A positional key would move every mark below the insertion."""
        _, session, tree = client
        before = {
            target.fingerprint: target.claims
            for target in session.prepared[CASE].mark_targets
        }

        claims_file = tree / "evals" / "corpus" / CASE / "claims" / "stride.json"
        claims = json.loads(claims_file.read_text("utf-8"))
        inserted = dict(claims[0], claim="an inserted claim", verb="flood")
        claims_file.write_text(
            json.dumps([inserted, *claims], indent=2), encoding="utf-8"
        )

        after = {
            target.fingerprint: target.claims
            for target in session_for(tree, "ada", CASE).prepared[CASE].mark_targets
        }
        assert len(after) == len(before) + 1, "the inserted claim is not a new finding"
        for fingerprint, texts in before.items():
            assert after[fingerprint] == texts, f"{fingerprint} re-pointed"

    def test_the_by_hand_document_offers_the_same_closed_set(self):
        """One method, two surfaces. The prose a reader follows names each value."""
        from evals import build_review_docs as build_docs

        for value in sittings.MARKS:
            assert f"`{value}`" in build_docs.MARK_GUIDANCE

    def test_a_claim_the_rule_cannot_key_refuses_by_name(self, tree):
        """A claim nobody can mark stops the case, and the refusal says which.

        The corpus gate in ``tests/test_verb_coverage.py`` keeps this off
        ``main``, so what is left is the message a contributor meets on a
        branch: the case, the framework and the sentence, rather than the
        identity rule's own words about a component it wanted.
        """
        claims_file = tree / "evals" / "corpus" / CASE / "claims" / "stride.json"
        claims = json.loads(claims_file.read_text("utf-8"))
        claims[0]["verb"] = None
        claims_file.write_text(json.dumps(claims, indent=2), encoding="utf-8")

        with pytest.raises(sittings.SittingError) as refused:
            session_for(tree, "ada", CASE)
        assert CASE in str(refused.value)
        assert claims[0]["claim"] in str(refused.value)


class TestTheRail:
    """The whole corpus in one list, and what a row may say about a case.

    Two properties carry this. A row says the case number, the title and the
    status and nothing else, because a claim count would tell the reader how
    long to make their own list before they have written it. And the status
    asks the clearing rule rather than the presence of an entry in ``reviews``,
    so the page never greys a case CI still asks somebody to read.
    """

    def rail(self, app):
        return app.get("/api/rail").json()

    def rows(self, app):
        return {row["case"]: row for row in self.rail(app)["cases"]}

    def opened(self, tree, case=None):
        session = session_for(tree, "ada", case)
        return browser(session)

    def test_the_app_starts_with_no_case_named(self, tree):
        """The reader chooses inside the page, so the terminal names nothing."""
        app = self.opened(tree)
        assert app.get("/").status_code == 200
        assert len(self.rail(app)["cases"]) == len(CASES)

    def test_every_case_has_a_row_with_a_number_and_a_title(self, tree):
        app = self.opened(tree)
        rows = self.rows(app)
        assert set(rows) == set(CASES)
        for case, row in rows.items():
            assert row["number"] == case.split("-")[0]
            assert row["title"] and row["title"] != case
            assert row["status"]

    def test_the_rows_are_in_corpus_order(self, tree):
        app = self.opened(tree)
        assert [row["case"] for row in self.rail(app)["cases"]] == sorted(CASES)

    def test_a_row_carries_no_claim_count_and_no_reason_the_case_waits(self, tree):
        """The payload's shape is the contract, so it is asserted whole.

        A field added here reaches every reader, and the one thing the page
        must never carry is how much is recorded against a case they have not
        read yet. The reason a case waits is the same disclosure in prose.
        """
        app = self.opened(tree)
        for row in self.rail(app)["cases"]:
            assert set(row) == {
                "case",
                "number",
                "title",
                "status",
                "state",
                "pressable",
            }
        listing = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        blob = json.dumps(self.rail(app))
        assert "STRIDE claims" not in blob, "the rail carries a claim count"
        assert "STRIDE claims" in listing, "the reason moved; re-point this test"

    def test_the_header_counts_what_is_left(self, tree):
        app = self.opened(tree)
        assert self.rail(app)["todo"] == len(CASES)
        sign(tree, OTHER, "sam")
        assert self.rail(app)["todo"] == len(CASES) - 1

    def test_a_case_the_reader_started_is_no_longer_to_do(self, tree):
        """The count names the cases nobody opened. A row that presses is a
        different question, and a finished case presses."""
        app = self.opened(tree)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert self.rail(app)["todo"] == len(CASES) - 1

    def test_a_signed_case_is_greyed_and_unpressable(self, tree):
        """Whoever signed it. The status names the signer, which reads both ways."""
        sign(tree, OTHER, "sam")
        app = self.opened(tree)
        assert self.rows(app)[OTHER] == {
            "case": OTHER,
            "number": "03",
            "title": self.rows(app)[OTHER]["title"],
            "status": "signed by sam",
            "state": "signed",
            "pressable": False,
        }
        assert self.rows(app)[CASE]["pressable"] is True

    def test_the_reader_s_own_signature_greys_it_too(self, tree):
        """The draft is what re-opens a case, not the login, so a submitted
        session leaves a case of the reader's own looking exactly like one
        somebody else signed."""
        sign(tree, OTHER, "ada")
        app = self.opened(tree)
        assert self.rows(app)[OTHER]["status"] == "signed by ada"
        assert self.rows(app)[OTHER]["pressable"] is False

    def test_a_sitting_by_an_unrostered_reader_greys_nothing(self, tree):
        """The clearing rule asks for a rostered reader, and the rail asks it."""
        sign(tree, OTHER, "nobody")
        app = self.opened(tree)
        assert self.rows(app)[OTHER]["status"] == sittings.TO_DO
        assert self.rows(app)[OTHER]["pressable"] is True

    def test_a_drifted_digest_puts_the_case_back_on_the_rail(self, tree):
        """The entry stays and clears nothing, and CI asks for that case.

        A rail keyed on the presence of an entry would grey it, so the reader
        could not reach the one case the failing check names.
        """
        sign(tree, OTHER, "sam")
        source = tree / "evals" / "corpus" / OTHER / "source.md"
        source.write_text(source.read_text("utf-8") + "\nan edit\n", encoding="utf-8")
        app = self.opened(tree)
        assert json.loads(
            (tree / "evals" / "corpus" / OTHER / "case.json").read_text("utf-8")
        )["reviews"], "the entry is still there"
        assert self.rows(app)[OTHER]["status"] == sittings.TO_DO
        assert self.rows(app)[OTHER]["pressable"] is True

    def test_recording_a_sitting_moves_its_row_to_finished(self, tree):
        """The record is in the working tree and the draft behind it lives, so
        the row says the record is not submitted and it still presses."""
        app = self.opened(tree)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        app.post(
            "/api/finish", json={"case": CASE, "marks": {}, "missing": [], "notes": ""}
        )
        assert self.rows(app)[CASE]["status"] == "finished, not submitted"
        assert self.rows(app)[CASE]["state"] == "finished"
        assert self.rows(app)[CASE]["pressable"] is True
        assert self.rail(app)["todo"] == len(CASES) - 1

    def test_a_signature_greys_nothing_while_a_draft_of_it_lives(self, tree):
        """The draft is what makes a case re-openable, not the login. A case
        dies in the rail when it carries a clearing signature and no draft."""
        app = self.opened(tree)
        app.post("/api/own-list", json={"case": OTHER, "items": OTHER_LIST})
        sign(tree, OTHER, "sam")
        assert self.rows(app)[OTHER]["status"] == "draft in progress"
        assert self.rows(app)[OTHER]["pressable"] is True

    def test_the_gate_re_arms_for_each_case(self, tree):
        """One case's own list opens that case's sets and no other's."""
        app = self.opened(tree)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert app.get(f"/api/part-two?case={CASE}").status_code == 200
        assert app.get(f"/api/part-two?case={OTHER}").status_code == 409

    def test_the_page_offers_one_button_and_no_way_back_to_a_list(self, tree):
        """The rail never leaves, so there is nothing to return to."""
        page = self.opened(tree).get("/").text
        assert "Start with the first case to do" in page
        assert "Back to" not in page


class TestEveryEndpointResolvesItsCase:
    """Decision 34, and the refusal decision 40 says costs no code of its own.

    A case id that arrives in a request is resolved against the offered list —
    the rail's pressable rows. So a signed case, a case somebody guessed and a
    case nobody wrote all refuse the same way, and the refusal names no id,
    because a message that echoes the request tells a caller which ids exist.
    """

    def naming(self, case):
        return {
            "GET /api/part-one": ("get", f"/api/part-one?case={case}", None),
            "GET /api/part-two": ("get", f"/api/part-two?case={case}", None),
            "POST /api/own-list": (
                "post",
                "/api/own-list",
                {"case": case, "items": OWN_LIST},
            ),
            "POST /api/finish": (
                "post",
                "/api/finish",
                {"case": case, "marks": {}, "missing": [], "notes": ""},
            ),
            "POST /api/drop": ("post", "/api/drop", {"case": case}),
            "POST /api/put-back": ("post", "/api/put-back", {"case": case}),
        }

    def refused(self, app, case):
        for name, (method, path, body) in self.naming(case).items():
            answer = getattr(app, method)(path, **({"json": body} if body else {}))
            assert answer.status_code == 404, f"{name} accepted {case}"
            assert case not in answer.text, f"{name} echoed the case id back"

    def test_a_case_nobody_wrote_is_refused(self, tree):
        session = session_for(tree, "ada")
        app = browser(session)
        self.refused(app, "99-not-a-case")

    def test_a_signed_case_is_refused(self, tree):
        sign(tree, OTHER, "sam")
        session = session_for(tree, "ada")
        app = browser(session)
        self.refused(app, OTHER)

    def test_a_case_signed_mid_session_is_refused_from_then_on(self, tree):
        """The offered list is re-read from the tree, so it can only shrink
        under the reader while they are working — never grow stale open."""
        session = session_for(tree, "ada")
        app = browser(session)
        assert app.get(f"/api/part-one?case={OTHER}").status_code == 200
        sign(tree, OTHER, "sam")
        app.get("/api/rail")
        self.refused(app, OTHER)

    def test_a_write_still_refuses_a_cross_site_request_first(self, tree):
        """The origin check runs before the case is resolved, so an unoffered
        case tells a foreign page nothing it could not already guess."""
        session = session_for(tree, "ada")
        app = TestClient(create_app(session), base_url=LOOPBACK)
        for path, body in (
            ("/api/own-list", {"case": "99-not-a-case", "items": OWN_LIST}),
            (
                "/api/finish",
                {"case": "99-not-a-case", "marks": {}, "missing": [], "notes": ""},
            ),
        ):
            assert app.post(path, json=body).status_code == 403


class TestThePreselect:
    """``--case`` moves the rail, and grants nothing.

    It answers where the reader opens, which has one answer, so the flag takes
    one value. Choosing several cases is what the rail is for.
    """

    def test_it_names_the_row_the_page_opens_on(self, tree):
        session = session_for(tree, "ada", CASE)
        app = browser(session)
        assert app.get("/api/rail").json()["preselect"] == CASE

    def test_a_case_id_not_in_the_corpus_refuses_at_the_command_line(self, tree):
        with pytest.raises(SystemExit) as refused:
            session_for(tree, "ada", "99-not-a-case")
        assert "99-not-a-case" in str(refused.value)

    def test_a_signed_case_preselects_greyed_and_opens_nothing(self, tree):
        sign(tree, CASE, "sam")
        session = session_for(tree, "ada", CASE)
        app = browser(session)
        rail = app.get("/api/rail").json()
        assert rail["preselect"] == CASE
        row = next(item for item in rail["cases"] if item["case"] == CASE)
        assert row["pressable"] is False
        assert app.get(f"/api/part-one?case={CASE}").status_code == 404

    def test_no_preselect_prepares_no_case(self, tree):
        """Preparing reads a whole case directory, and the reader opens a few."""
        assert session_for(tree, "ada").prepared == {}


class TestACaseTakesOneOwnList:
    """The first list, and no second one.

    The filled document prints the reader's own list above the recorded sets,
    and the gate is what makes that order true rather than hoped for. A case
    the reader can re-open is a case whose sets are already open, so a second
    list posted against it would be evidence of an order that did not happen.
    """

    def opened(self, tree):
        session = session_for(tree, "ada")
        return browser(session)

    def test_a_second_list_for_the_same_case_is_refused(self, tree):
        app = self.opened(tree)
        first = {"case": CASE, "items": OWN_LIST}
        assert app.post("/api/own-list", json=first).status_code == 200
        refused = app.post("/api/own-list", json={"case": CASE, "items": ["seen it"]})
        assert refused.status_code == 409
        assert "already has your own list" in refused.json()["detail"]

    def test_the_refused_list_changes_nothing(self, tree):
        app = self.opened(tree)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        app.post("/api/own-list", json={"case": CASE, "items": ["written after"]})
        app.post(
            "/api/finish",
            json={"case": CASE, "marks": {}, "missing": [], "notes": ""},
        )
        text = (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").read_text("utf-8")
        assert "a spoofed device" in text
        assert "written after" not in text, "a list written after the sets was recorded"

    def test_a_second_case_still_takes_its_own_first_list(self, tree):
        app = self.opened(tree)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        answer = app.post("/api/own-list", json={"case": OTHER, "items": OTHER_LIST})
        assert answer.status_code == 200

    def test_part_one_carries_this_case_s_list_back_and_no_other_s(self, tree):
        """A case comes back filled; a case never written for comes back blind."""
        app = self.opened(tree)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert app.get(f"/api/part-one?case={CASE}").json()["own_list"] == [
            "a spoofed device"
        ]
        blind = app.get(f"/api/part-one?case={OTHER}").json()
        assert blind["own_list"] is None
        assert "a spoofed device" not in json.dumps(blind)


class TestTheOwnListCarriesThePageToken:
    """Two controls on the endpoint that opens a case's recorded sets.

    The endpoint names a case now. One foreign page that reached it would
    post an empty list for every case in the offered list, and open the whole
    corpus in one pass. The origin check refuses a page served from anywhere
    else; the token refuses a request that never read this page at all.
    """

    def bare(self, tree):
        """The origin header and no token: a same-origin request that never
        read the page."""
        session = session_for(tree, "ada")
        app = TestClient(create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN)
        return app, session

    def test_a_post_without_the_token_is_refused(self, tree):
        app, _ = self.bare(tree)
        refused = app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert refused.status_code == 403
        assert not draft_file(tree, CASE).exists(), "a refused post wrote a draft"

    def test_a_wrong_token_is_refused(self, tree):
        app, _ = self.bare(tree)
        refused = app.post(
            "/api/own-list",
            json={"case": CASE, "items": OWN_LIST},
            headers={"X-Sitting-Token": "guessed"},
        )
        assert refused.status_code == 403
        assert not draft_file(tree, CASE).exists()

    def test_the_refused_post_leaves_the_case_blind(self, tree):
        """The refusal is the whole point: the sets stay shut behind it."""
        app, _ = self.bare(tree)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert app.get(f"/api/part-two?case={CASE}").status_code == 409

    def test_the_page_s_own_token_opens_it(self, tree):
        session = session_for(tree, "ada")
        app = browser(session)
        assert (
            app.post(
                "/api/own-list", json={"case": CASE, "items": OWN_LIST}
            ).status_code
            == 200
        )

    def test_the_origin_check_still_runs_first(self, tree):
        """A cross-site request carrying a real token is refused as cross-site,
        so a framed page gains nothing by holding the token it can read."""
        session = session_for(tree, "ada")
        app = browser(session)
        refused = app.post(
            "/api/own-list",
            json={"case": CASE, "items": OWN_LIST},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert refused.status_code == 403
        assert "did not come from the app's page" in refused.json()["detail"]

    def test_part_two_takes_no_token(self, tree):
        """It is a read, it serves only what a passed gate already opened, and
        the frame rule covers the page that would read it. A token here would
        be a third opinion on the same question rather than a new control."""
        session = session_for(tree, "ada")
        app = browser(session)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        opened = TestClient(create_app(session), base_url=LOOPBACK)
        assert opened.get(f"/api/part-two?case={CASE}").status_code == 200


class TestTheWalkStaysBlind:
    """The gate re-arms per case, asserted over the whole offered list.

    The loop is over the corpus rather than over a written list of cases, so a
    case somebody adds tomorrow is walked by this test on the day it lands. At
    each step it fetches every endpoint the page can reach and reads the whole
    answer, because the rule is about the payload rather than about the
    screen.
    """

    def claims(self, tree, case):
        """Every recorded claim sentence of one case, from its own files."""
        directory = tree / "evals" / "corpus" / case / "claims"
        return [
            claim["claim"]
            for path in sorted(directory.iterdir())
            for claim in json.loads(path.read_text("utf-8"))
        ]

    def everything(self, app, offered):
        """What the page can reach, at one step of the walk."""
        answers = [app.get("/").text, app.get("/api/rail").text]
        for case in offered:
            answers.append(app.get(f"/api/part-one?case={case}").text)
            answers.append(app.get(f"/api/part-two?case={case}").text)
        return "\n".join(answers)

    def blind(self, app, tree, offered, started, step):
        seen = self.everything(app, offered)
        for case in offered:
            if case in started:
                continue
            for claim in self.claims(tree, case):
                assert claim not in seen, f"{case} leaked a claim {step}"

    def test_no_case_leaks_before_its_own_list_is_in(self, tree):
        app = browser(session_for(tree, "ada"))
        offered = [
            row["case"]
            for row in app.get("/api/rail").json()["cases"]
            if row["pressable"]
        ]
        assert len(offered) > 1, "a walk of one case proves nothing about the gate"
        started = set()
        for case in offered:
            self.blind(app, tree, offered, started, f"before {case} opened")
            app.post("/api/own-list", json={"case": case, "items": OWN_LIST})
            started.add(case)
            self.blind(app, tree, offered, started, f"after {case} opened")

    def test_recording_one_case_leaks_no_other(self, tree):
        """Finish answers with the written paths and the paste text, and the
        rail is re-read after it. Neither may carry a case still to do."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        recorded = app.post(
            "/api/finish", json={"case": CASE, "marks": {}, "missing": [], "notes": ""}
        )
        seen = recorded.text + self.everything(app, [OTHER])
        for claim in self.claims(tree, OTHER):
            assert claim not in seen


class TestTheWalk:
    """Previous and Next, and where the last Next lands.

    The move between cases happens in the page. The rail never leaves, so no
    document reloads and the server sees the same per-case endpoints it
    already answers — which is why what the walk itself must do is asserted
    against the page the server serves. What a request may reach is asserted
    against the endpoints, in :class:`TestEveryEndpointResolvesItsCase`.
    """

    def page(self, tree):
        return browser(session_for(tree, "ada")).get("/").text

    def header(self, tree):
        return self.page(tree).split("<header>")[1].split("</header>")[0]

    def test_previous_and_next_sit_in_the_stage_header(self, tree):
        """Beside the case title, so the controls sit with the case they move."""
        header = self.header(tree)
        assert 'id="caseTitle"' in header
        assert 'id="previous"' in header
        assert 'id="next"' in header

    def test_the_walk_steps_only_over_the_cases_the_reader_may_open(self, tree):
        """A dead row is off the offered list, so the walk never stops on one.

        The page takes its step over the same pressable rows that
        :func:`webapp.sitting_base.open_case` resolves a request against, so a case the
        walk cannot reach is a case a request cannot open either.
        """
        assert "rows.filter(row => row.pressable)" in self.page(tree)

    def test_the_last_next_lands_on_the_submit_stage(self, tree):
        assert 'id="submitStage"' in self.page(tree)

    def test_a_blind_case_shows_a_placeholder_where_part_two_opens(self, tree):
        """So the case reads the same whichever way the reader arrives at it."""
        assert 'id="placeholder"' in self.page(tree)

    def test_the_walk_carries_each_case_s_answers(self, tree):
        """A case the reader comes back to comes back as they left it.

        Every word of it comes back from the server, because the draft is
        where it lives. The page holds no copy, so there is nothing that can
        disagree with the file on disk.
        """
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        app.post(
            "/api/draft",
            json={"case": CASE, "marks": {}, "missing": ["theirs"], "notes": "mine"},
        )
        came_back = app.get(f"/api/part-one?case={CASE}").json()
        assert came_back["own_list"] == OWN_LIST
        assert came_back["missing"] == ["theirs"]
        assert came_back["notes"] == "mine"


class TestATokenOutsideAscii:
    """``compare_digest`` refuses two ``str`` values unless both are ASCII.

    So a header carrying one byte above 127 raised inside the check, and the
    caller met a 500 where the answer is a refusal. Both endpoints that take
    the token compare bytes, which has no such rule.
    """

    def sent(self, app, path, **kwargs):
        # As bytes, because the client refuses to encode a ``str`` header
        # outside ASCII. A request off the wire carries bytes either way, and
        # the server decodes them latin-1 before the check ever sees them.
        return app.post(
            path, headers={"X-Sitting-Token": "tokén".encode("latin-1")}, **kwargs
        )

    def test_the_own_list_refuses_it(self, tree):
        session = session_for(tree, "ada")
        app = TestClient(create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN)
        refused = self.sent(
            app, "/api/own-list", json={"case": CASE, "items": OWN_LIST}
        )
        assert refused.status_code == 403
        assert not draft_file(tree, CASE).exists()

    def test_the_submit_endpoint_refuses_it(self, tree):
        session = session_for(tree, "ada", CASE, can_submit=True)
        app = TestClient(create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN)
        assert self.sent(app, "/api/submit").status_code == 403


class TestTheDraftSurvivesTheProcess:
    """A part-finished read outlives the browser and the process.

    The property here is that the page holds no copy of what the reader
    wrote. Every word of it is in the **Draft Sitting** the moment they write
    it, so a second process over the same store answers the same as the
    first — which is what a reader who closes a laptop is owed.
    """

    def test_opening_a_case_writes_nothing(self, tree):
        """A reader who reads part one of ten cases and writes nothing leaves
        no trace, so the rail stays clean."""
        app = browser(session_for(tree, "ada"))
        assert app.get(f"/api/part-one?case={CASE}").status_code == 200
        assert not draft_file(tree, CASE).exists()
        assert not drafts_root(tree).exists()

    def test_the_own_list_creates_the_draft(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert draft_file(tree, CASE).is_file()

    def test_the_draft_holds_the_shape_the_spec_names(self, tree):
        """The shape is a decision, so it is asserted whole.

        A field added here reaches the reader's own store, which is the one
        place in this path nothing else can repair.
        """
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        held = json.loads(draft_file(tree, CASE).read_text("utf-8"))
        assert set(held) == {
            "case",
            "clone",
            "state",
            "own_list",
            "marks",
            "missing",
            "notes",
            "opened_digests",
            "recorded",
            "unreviewed_entry",
        }
        assert held["case"] == CASE
        assert held["clone"] == str(tree), "the clone path is in the file"
        assert held["state"] == "open"
        assert held["own_list"] == OWN_LIST

    def test_it_pins_every_required_file_as_it_stood(self, tree):
        """The opening digests are what make a drift warning honest.

        They answer a different question from the digest in the case
        metadata, which pins what a recorded sitting signed.
        """
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        held = json.loads(draft_file(tree, CASE).read_text("utf-8"))
        case_dir = tree / "evals" / "corpus" / CASE
        declared = json.loads((case_dir / "case.json").read_text("utf-8"))["frameworks"]
        wanted = sittings.required_files(item["name"] for item in declared)
        assert set(held["opened_digests"]) == set(wanted)
        for name, digest in held["opened_digests"].items():
            assert hashlib.sha256((case_dir / name).read_bytes()).hexdigest() == digest

    def test_the_draft_caches_no_case_text(self, tree):
        """Part one and part two are read from the case directory each time,
        because a cache that can disagree with its source is a defect that
        waits."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        blocks = app.get(f"/api/part-one?case={CASE}").json()["blocks"]
        held = draft_file(tree, CASE).read_text("utf-8")
        prose = "\n".join(
            block["text"] for block in blocks if block["kind"] == "source"
        )
        longest = max(prose.split("\n"), key=len).strip()
        assert len(longest) > 40, "the case has a sentence long enough to look for"
        assert longest not in held

    def test_a_second_process_finds_the_read_where_it_was_left(self, tree):
        """The reader stops the app, runs it again, and continues."""
        first = browser(session_for(tree, "ada"))
        first.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        marks = {
            target["fingerprint"]: "agree"
            for target in first.get(f"/api/part-two?case={CASE}").json()["marks"][:1]
        }
        first.post(
            "/api/draft",
            json={
                "case": CASE,
                "marks": marks,
                "missing": ["nobody rotates the key"],
                "notes": "counted 18",
            },
        )

        again = browser(session_for(tree, "ada"))
        came_back = again.get(f"/api/part-one?case={CASE}").json()
        assert came_back["own_list"] == OWN_LIST
        assert came_back["marks"] == marks
        assert came_back["missing"] == ["nobody rotates the key"]
        assert came_back["notes"] == "counted 18"

    def test_the_gate_stays_open_across_the_restart(self, tree):
        """The gate asks whether the case has an own list, and the draft is
        where that answer now lives — so a restart re-arms nothing."""
        first = browser(session_for(tree, "ada"))
        first.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        again = browser(session_for(tree, "ada"))
        assert again.get(f"/api/part-two?case={CASE}").status_code == 200
        assert again.get(f"/api/part-two?case={OTHER}").status_code == 409

    def test_a_case_with_a_draft_says_so_in_the_rail(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        rows = {row["case"]: row for row in app.get("/api/rail").json()["cases"]}
        assert rows[CASE]["status"] == "draft in progress"
        assert rows[CASE]["state"] == "draft"
        assert rows[CASE]["pressable"] is True
        assert rows[OTHER]["state"] == "todo"

    def test_recording_the_sitting_finishes_the_draft_and_keeps_it(self, tree):
        """The record is in the working tree by then, and nothing is a record
        until it merges — so the draft says ``finished`` and stays."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        app.post(
            "/api/finish",
            json={"case": CASE, "marks": {}, "missing": ["mine"], "notes": "done"},
        )
        held = json.loads(draft_file(tree, CASE).read_text("utf-8"))
        assert held["state"] == "finished"
        assert held["missing"] == ["mine"]
        assert held["notes"] == "done"

    def test_saving_before_the_own_list_is_refused(self, tree):
        """There is no draft to save into, and this endpoint never makes one:
        the own list is the only thing that opens a case."""
        app = browser(session_for(tree, "ada"))
        refused = app.post(
            "/api/draft", json={"case": CASE, "marks": {}, "missing": [], "notes": "x"}
        )
        assert refused.status_code == 409
        assert not draft_file(tree, CASE).exists()

    def test_the_page_holds_no_copy_of_what_the_reader_wrote(self, tree):
        """Asserted on the page, because a second store is what would make a
        restart disagree with the file on disk."""
        page = browser(session_for(tree, "ada")).get("/").text
        assert "/api/draft" in page, "the page saves what the reader writes"
        assert "const answers" not in page


class TestReRecordingACase:
    """A recorded case re-opens, and a second press replaces the first entry.

    Append-only governs the record, and nothing is a record until it merges.
    A pull request carrying two entries by one reader for one case would force
    a strange exception into the check that reads the new entries, so the entry
    this session appended comes off before the new one goes on.
    """

    def record(self, app, case=CASE, notes="21 agree", missing=()):
        return app.post(
            "/api/finish",
            json={
                "case": case,
                "marks": {},
                "missing": list(missing),
                "notes": notes,
            },
        )

    def read_and_record(self, app, case=CASE, notes="21 agree", missing=()):
        """One whole sitting: the own list, then the record."""
        app.post("/api/own-list", json={"case": case, "items": OWN_LIST})
        return self.record(app, case, notes, missing)

    def reviews(self, tree, case=CASE):
        path = tree / "evals" / "corpus" / case / "case.json"
        return json.loads(path.read_text("utf-8"))["reviews"]

    def test_a_finished_case_re_opens_with_both_parts(self, tree):
        """The draft survives the record, so the reader comes back to the
        case with their own list, their answers and the recorded sets."""
        app = browser(session_for(tree, "ada"))
        self.read_and_record(app, missing=["nothing about the fleet key"])
        came_back = app.get(f"/api/part-one?case={CASE}").json()
        assert came_back["own_list"] == OWN_LIST
        assert came_back["missing"] == ["nothing about the fleet key"]
        assert came_back["state"] == "finished"
        assert app.get(f"/api/part-two?case={CASE}").status_code == 200

    def test_a_case_nobody_started_names_no_state(self, tree):
        """The field says what the draft says, and a case without one holds
        no draft to speak for it."""
        app = browser(session_for(tree, "ada"))
        assert app.get(f"/api/part-one?case={CASE}").json()["state"] is None

    def test_the_page_offers_the_re_record_button(self, tree):
        """The label is the reader's one sign that a second press corrects the
        record rather than adding to it."""
        page = browser(session_for(tree, "ada")).get("/").text
        assert "Re-record this sitting" in page

    def test_the_draft_keeps_the_answers_the_second_press_carried(self, tree):
        app = browser(session_for(tree, "ada"))
        self.read_and_record(app, notes="21 agree")
        self.record(app, notes="22 agree", missing=["nobody rotates the key"])
        held = json.loads(draft_file(tree, CASE).read_text("utf-8"))
        assert held["state"] == "finished"
        assert held["notes"] == "22 agree"
        assert held["missing"] == ["nobody rotates the key"]

    def test_it_records_whichever_case_is_in_the_stage(self, tree):
        """The walk records the case the reader stands on, and a re-record
        reaches back to a case they left."""
        app = browser(session_for(tree, "ada"))
        self.read_and_record(app, CASE, notes="the fleet")
        self.read_and_record(app, OTHER, notes="the pipeline")
        self.record(app, CASE, notes="the fleet, again")
        assert [entry["notes"] for entry in self.reviews(tree, CASE)] == [
            "the fleet, again"
        ]
        assert [entry["notes"] for entry in self.reviews(tree, OTHER)] == [
            "the pipeline"
        ]


class TestTheTextMovedUnderTheRead:
    """A required file moves while a draft is open, and the reader hears it.

    The draft's opening digests are what make the warning honest: they say
    what the reader opened, where the digest in the case metadata says what a
    recorded sitting signed. The check runs at open, because a reader needs
    to know before they spend an hour, and again at finish, because a file
    can move while the tab sits open.

    The app names the files and judges nothing. The reader keeps their own
    list either way, and decides for themselves whether it still answers the
    text.
    """

    def move(self, tree, case=CASE, name="source.md"):
        """Somebody edits a read file under a read in progress."""
        path = tree / "evals" / "corpus" / case / name
        path.write_text(
            path.read_text("utf-8") + "\nA paragraph added later.\n", encoding="utf-8"
        )

    def finish(self, app, case=CASE):
        return app.post(
            "/api/finish",
            json={"case": case, "marks": {}, "missing": [], "notes": ""},
        )

    def test_a_case_that_did_not_move_names_nothing(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert app.get(f"/api/part-one?case={CASE}").json()["moved"] == []

    def test_a_case_with_no_draft_names_nothing(self, tree):
        """There is no opening digest to compare against, because nothing
        opened: the warning answers a read in progress and not a file."""
        app = browser(session_for(tree, "ada"))
        self.move(tree)
        assert app.get(f"/api/part-one?case={CASE}").json()["moved"] == []

    def test_the_open_names_the_file_that_moved(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        self.move(tree)
        assert app.get(f"/api/part-one?case={CASE}").json()["moved"] == ["source.md"]

    def test_it_names_only_the_case_that_moved(self, tree):
        app = browser(session_for(tree, "ada"))
        for case in CASES:
            app.post("/api/own-list", json={"case": case, "items": OWN_LIST})
        self.move(tree, OTHER)
        assert app.get(f"/api/part-one?case={CASE}").json()["moved"] == []
        assert app.get(f"/api/part-one?case={OTHER}").json()["moved"] == ["source.md"]

    def test_the_own_list_survives_the_warning_at_open(self, tree):
        """A reader never retypes a list because somebody edited a file."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        self.move(tree)
        came_back = app.get(f"/api/part-one?case={CASE}").json()
        assert came_back["own_list"] == OWN_LIST
        assert app.get(f"/api/part-two?case={CASE}").status_code == 200

    def test_a_file_that_moves_while_the_tab_sits_open_reaches_the_finish(self, tree):
        """The open said nothing, because at open nothing had moved."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert app.get(f"/api/part-one?case={CASE}").json()["moved"] == []
        self.move(tree)
        assert self.finish(app).json()["moved"] == ["source.md"]

    def test_the_finish_names_nothing_where_nothing_moved(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert self.finish(app).json()["moved"] == []

    def test_the_own_list_survives_the_warning_at_finish(self, tree):
        """The record is written, and the reader's list rides into it whole."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        self.move(tree)
        assert self.finish(app).status_code == 200
        case_dir = tree / "evals" / "corpus" / CASE
        filled = (case_dir / sittings.document_name("ada")).read_text("utf-8")
        assert "a spoofed device" in filled
        held = json.loads(draft_file(tree, CASE).read_text("utf-8"))
        assert held["own_list"] == OWN_LIST

    def test_the_page_carries_the_warning(self, tree):
        """Both payloads that carry the drift reach the reader, and the box
        that holds it starts hidden — a case that did not move says nothing."""
        page = browser(session_for(tree, "ada")).get("/").text
        assert 'id="moved" class="note hidden"' in page
        assert page.count("warn(d.moved)") == 2, "at open, and again at finish"

    def test_the_recorded_entry_signs_the_bytes_that_will_merge(self, tree):
        """The digests are taken fresh at finish, so a file that moved under
        the read is signed as it stands rather than as it was opened."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        self.move(tree)
        assert self.finish(app).status_code == 200
        case_dir = tree / "evals" / "corpus" / CASE
        entry = json.loads((case_dir / "case.json").read_text("utf-8"))["reviews"][-1]
        signed = {record["file"]: record["sha256"] for record in entry["read"]}
        now = hashlib.sha256((case_dir / "source.md").read_bytes()).hexdigest()
        assert signed["source.md"] == now
        opened = json.loads(draft_file(tree, CASE).read_text("utf-8"))
        assert opened["opened_digests"]["source.md"] != now


class TestDiscardingADraft:
    """One draft the reader abandons, by hand, on the case it belongs to."""

    def discard(self, app, case=CASE):
        return app.post("/api/discard", json={"case": case})

    def test_the_case_returns_to_to_do(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        assert self.discard(app).json() == {"case": CASE, "discarded": True}
        assert not draft_file(tree, CASE).exists()
        rows = {row["case"]: row for row in app.get("/api/rail").json()["cases"]}
        assert rows[CASE]["status"] == sittings.TO_DO
        assert rows[CASE]["state"] == "todo"
        assert rows[CASE]["pressable"] is True

    def test_the_gate_re_arms_behind_it(self, tree):
        """The case is blind again, which is the whole of what *to do* means."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        self.discard(app)
        assert app.get(f"/api/part-two?case={CASE}").status_code == 409
        assert app.get(f"/api/part-one?case={CASE}").json()["own_list"] is None

    def test_it_takes_the_case_it_is_given_and_no_other(self, tree):
        app = browser(session_for(tree, "ada"))
        for case in CASES:
            app.post("/api/own-list", json={"case": case, "items": OWN_LIST})
        self.discard(app, CASE)
        assert not draft_file(tree, CASE).exists()
        assert draft_file(tree, OTHER).is_file()

    def test_discarding_where_there_is_no_draft_changes_nothing(self, tree):
        app = browser(session_for(tree, "ada"))
        assert self.discard(app).json() == {"case": CASE, "discarded": False}

    def test_it_carries_both_controls(self, tree):
        """It names a case and it writes under the reader's own store, so a
        foreign page that reached it would throw away somebody's afternoon."""
        session = session_for(tree, "ada")
        app = browser(session)
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        foreign = app.post(
            "/api/discard",
            json={"case": CASE},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        untokened = TestClient(
            create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN
        ).post("/api/discard", json={"case": CASE})
        assert foreign.status_code == 403
        assert untokened.status_code == 403
        assert draft_file(tree, CASE).is_file()


class TestADraftTheAppCannotRead:
    """Fail closed, name the file, change nothing, and cost one case.

    Two alternatives are rejected. To treat it as absent throws the reader's
    own list away and re-arms the gate, so they retype a list they already
    wrote and never learn the first one existed. To repair it writes a guess
    into the one file the reader owns.
    """

    def spoil(self, tree, case=CASE, text="{not json at all"):
        path = draft_file(tree, case)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def rows(self, app):
        return {row["case"]: row for row in app.get("/api/rail").json()["cases"]}

    def test_the_row_names_the_file_and_says_it_will_not_read(self, tree):
        path = self.spoil(tree)
        row = self.rows(browser(session_for(tree, "ada")))[CASE]
        assert str(path) in row["status"]
        assert row["state"] == "error"
        assert row["pressable"] is False

    def test_every_other_case_still_walks(self, tree):
        """One bad file never costs the reader the other twelve cases."""
        self.spoil(tree)
        app = browser(session_for(tree, "ada"))
        assert self.rows(app)[OTHER]["pressable"] is True
        assert app.get(f"/api/part-one?case={OTHER}").status_code == 200
        opened = app.post("/api/own-list", json={"case": OTHER, "items": OWN_LIST})
        assert opened.status_code == 200
        assert app.get(f"/api/part-two?case={OTHER}").status_code == 200

    def test_it_changes_nothing_on_disk(self, tree):
        path = self.spoil(tree)
        before = path.read_bytes()
        app = browser(session_for(tree, "ada"))
        self.rows(app)
        app.get(f"/api/part-one?case={OTHER}")
        assert path.read_bytes() == before

    def test_a_draft_that_is_json_but_not_a_draft_reads_the_same_way(self, tree):
        """A hand-edited file fails the shape rather than the parse, and the
        reader gets the same refusal with the same file named."""
        path = self.spoil(tree, text=json.dumps({"case": CASE, "state": "halfway"}))
        row = self.rows(browser(session_for(tree, "ada")))[CASE]
        assert str(path) in row["status"]
        assert row["state"] == "error"


class TestTwoLoginsOnOneMachine:
    """The login is in the path, so two readers never collide."""

    def test_neither_reader_sees_the_other_s_draft(self, tree):
        ada = browser(session_for(tree, "ada"))
        ada.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})

        sam = browser(session_for(tree, "sam"))
        assert sam.get(f"/api/part-one?case={CASE}").json()["own_list"] is None
        assert sam.get(f"/api/part-two?case={CASE}").status_code == 409

        sam.post("/api/own-list", json={"case": CASE, "items": ["a flat battery"]})
        assert draft_file(tree, CASE, "ada").is_file()
        assert draft_file(tree, CASE, "sam").is_file()
        assert ada.get(f"/api/part-one?case={CASE}").json()["own_list"] == [
            "a spoofed device"
        ]


class TestTheDraftStore:
    """The rules under the surface: where a draft is filed, and what reads."""

    def store(self, tmp_path):
        return tmp_path / "sittings"

    def test_the_login_and_the_case_are_both_path_segments(self, tmp_path):
        """The case id arrives in a request, so a value carrying a separator
        is refused here rather than trusted to have been resolved."""
        for login, case in (("ada", "../../etc"), ("../root", CASE), ("ada", "..")):
            with pytest.raises(sittings.DraftError):
                sittings.draft_path(self.store(tmp_path), login, case)

    def test_a_saved_draft_round_trips(self, tmp_path):
        root = self.store(tmp_path)
        written = sittings.Draft(case=CASE, clone=str(tmp_path), own_list=OWN_LIST)
        path = sittings.save_draft(root, "ada", written)
        assert path == root / "ada" / f"{CASE}.json"
        assert sittings.load_draft(root, "ada", CASE) == written

    def test_a_draft_nobody_wrote_is_none(self, tmp_path):
        assert sittings.load_draft(self.store(tmp_path), "ada", CASE) is None

    def test_the_store_is_the_reader_s_alone(self, tmp_path):
        """It holds an unsigned own list, which is nobody else's to read."""
        root = self.store(tmp_path)
        path = sittings.save_draft(
            root, "ada", sittings.Draft(case=CASE, clone=str(tmp_path))
        )
        assert path.stat().st_mode & 0o777 == 0o600
        assert (root / "ada").stat().st_mode & 0o777 == 0o700

    def test_a_field_the_shape_does_not_name_is_refused(self, tmp_path):
        root = self.store(tmp_path)
        path = sittings.draft_path(root, "ada", CASE)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"case": CASE, "clone": "/x", "opened_at": "today"}),
            encoding="utf-8",
        )
        with pytest.raises(sittings.DraftError, match=str(path)):
            sittings.load_draft(root, "ada", CASE)

    def test_a_mark_outside_the_closed_set_is_refused(self, tmp_path):
        root = self.store(tmp_path)
        path = sittings.draft_path(root, "ada", CASE)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"case": CASE, "clone": "/x", "marks": {"abc": "maybe"}}),
            encoding="utf-8",
        )
        with pytest.raises(sittings.DraftError):
            sittings.load_draft(root, "ada", CASE)

    def test_a_draft_filed_under_another_case_is_refused(self, tmp_path):
        """The file name and the field must agree, or a mark meant for one
        case would be read against another."""
        root = self.store(tmp_path)
        path = sittings.draft_path(root, "ada", CASE)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"case": OTHER, "clone": "/x"}), encoding="utf-8")
        with pytest.raises(sittings.DraftError, match=OTHER):
            sittings.load_draft(root, "ada", CASE)

    def test_a_survey_reports_the_unreadable_one_and_keeps_going(self, tmp_path):
        root = self.store(tmp_path)
        sittings.save_draft(root, "ada", sittings.Draft(case=CASE, clone="/x"))
        sittings.draft_path(root, "ada", OTHER).write_text("{", encoding="utf-8")
        held = sittings.draft_states(root, "ada")
        assert held[CASE].state == "open"
        assert held[OTHER].state == sittings.UNREADABLE

    def test_a_survey_of_a_reader_with_no_store_is_empty(self, tmp_path):
        assert sittings.draft_states(self.store(tmp_path), "ada") == {}

    def test_the_default_root_is_outside_any_repository(self, tmp_path):
        """Under the reader's own state directory, which is where a store
        that must never merge belongs."""
        root = sittings.draft_root()
        assert root.parts[-4:] == (".local", "state", "work-agent", "sittings")
        assert root.is_absolute()

    def test_a_session_takes_the_root_its_caller_names(self, tree, tmp_path):
        """The whole reason the root is a field: a test points it at a
        temporary directory, and no test writes into a real home directory."""
        named = tmp_path / "elsewhere"
        assert build_session(tree, "ada", drafts=named).drafts == named
        assert build_session(tree, "ada").drafts == sittings.draft_root()


class TestThePinnedRailFooter:
    """``Submit — N cases ready``: the count and the way to press, in one.

    It counts the finished drafts, which is what one press carries, so the
    footer and the submit stage can never disagree about the size of the job.
    It is off at a count of zero, because a press with nothing behind it is
    not an offer.
    """

    def ready(self, app):
        return app.get("/api/rail").json()["ready"]

    def test_it_counts_the_finished_drafts(self, tree):
        app = browser(session_for(tree, "ada"))
        assert self.ready(app) == 0
        for count, case in enumerate(CASES, start=1):
            read_and_record(app, case)
            assert self.ready(app) == count

    def test_a_draft_in_progress_is_not_ready(self, tree):
        """A read the reader started carries no record, so no press carries
        it — the footer counts what is finished and never what is open."""
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": ["a stolen key"]})
        assert self.ready(app) == 0

    def test_a_dropped_case_leaves_the_count(self, tree):
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        app.post("/api/drop", json={"case": CASE})
        assert self.ready(app) == 0

    def test_the_footer_is_in_the_rail_and_off_at_zero(self, tree):
        """The rail never leaves, so the footer is on screen on every stage.

        The page is asserted rather than driven, as every other page property
        here is: the button ships hidden, and the rail's own load is what
        turns it on.
        """
        nav = _PAGE.split("<nav>")[1].split("</nav>")[0]
        assert '<button id="toSubmit" class="hidden">' in nav
        assert '"Submit — " + count + " cases ready"' in _PAGE


class TestTheSubmitStage:
    """What one press carries, and what stays behind.

    The list is read from the tree on every ask, so it is the submission the
    press builds rather than a description of one. The stage also names what
    stays unfinished, which is the failure a read that survives the process
    invites: believing you submitted five cases when you submitted four.
    """

    def stage(self, app):
        return app.get("/api/stage").json()

    def test_it_lists_every_finished_draft(self, tree):
        app = browser(session_for(tree, "ada"))
        assert self.stage(app)["ready"] == []
        for case in CASES:
            read_and_record(app, case)
        listed = self.stage(app)["ready"]
        assert [row["case"] for row in listed] == sorted(CASES)
        for row in listed:
            assert row["number"] == row["case"].split("-")[0]
            assert row["title"] and row["title"] != row["case"]

    def test_a_row_carries_no_claim_count(self, tree):
        """The same rule the rail row reads under. A number here would tell
        the reader how much a case they have not read holds."""
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        for row in self.stage(app)["ready"]:
            assert set(row) == {"case", "number", "title"}
        assert "STRIDE claims" not in json.dumps(self.stage(app))

    def test_it_says_how_many_cases_stay_unfinished(self, tree):
        app = browser(session_for(tree, "ada"))
        assert self.stage(app)["unfinished"] == len(CASES)
        read_and_record(app, CASE)
        assert self.stage(app)["unfinished"] == len(CASES) - 1

    def test_a_signed_case_is_not_unfinished(self, tree):
        """A case somebody cleared is done rather than left, whoever signed
        it. What is left is what nobody finished."""
        sign(tree, OTHER, "sam")
        app = browser(session_for(tree, "ada"))
        assert self.stage(app)["unfinished"] == len(CASES) - 1

    def test_the_written_paths_name_every_case_it_carries(self, tree):
        app = browser(session_for(tree, "ada"))
        for case in CASES:
            read_and_record(app, case)
        written = self.stage(app)["written"]
        for case in CASES:
            assert f"evals/corpus/{case}/REVIEW-ada.md" in written
            assert f"evals/corpus/{case}/case.json" in written
        assert written.count(sittings.UNREVIEWED_FILE) == 1, "one list, once"

    def test_the_paste_text_names_every_case_it_carries(self, tree):
        """In the shape the pull request itself takes: a title that counts
        the cases and a body that lists them."""
        app = browser(session_for(tree, "ada"))
        for case in CASES:
            read_and_record(app, case)
        paste = self.stage(app)["paste"]
        assert paste.startswith(f"Sitting: ada, {len(CASES)} cases")
        for case in CASES:
            assert f"- {case}" in paste

    def test_one_command_carries_the_whole_session(self, tree):
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        assert self.stage(app)["command"] == (
            "python -m evals.harness.run submit sitting"
        )

    def test_with_nothing_recorded_the_ways_out_are_off(self, tree):
        """A reader who walks to the end having recorded nothing is offered
        no way out, because there is nothing for one to carry."""
        app = browser(session_for(tree, "ada"))
        stage = self.stage(app)
        assert stage["ready"] == []
        assert stage["written"] == []
        assert (
            '$("waysOut").classList.toggle("hidden", !d.ready.length)'
            in client_script("sitting.js")
        )

    def test_with_no_gh_login_the_ways_out_stay_and_the_button_never_appears(
        self, tree
    ):
        """The path still ends somewhere. The command and the paste text are
        the same either way; only the press is missing."""
        session = session_for(tree, "ada", can_submit=False)
        app = browser(session)
        read_and_record(app, CASE)
        stage = self.stage(app)
        assert stage["command"] and stage["paste"] and stage["written"]
        assert "const CAN_SUBMIT = false;" in app.get("/").text
        refused = app.post("/api/submit")
        assert refused.status_code == 409
        assert "nothing to" in refused.json()["detail"]


class TestDroppingACase:
    """A drop holds one recorded case back, and a put back returns it.

    The press takes no argument and the submission builds itself from the
    working tree, so a drop has to take the record out of the tree. That is
    what makes the list on the page exactly what the press carries — and it
    is why the reader's own words survive it untouched.
    """

    def case_dir(self, tree, case=CASE):
        return tree / "evals" / "corpus" / case

    def snapshot(self, tree, case=CASE):
        """Every file under one case, as it stands. The drop is measured
        against it, because a stray byte in this directory puts the case in
        the pull request."""
        root = self.case_dir(tree, case)
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_a_dropped_case_returns_to_draft_in_progress(self, tree):
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        assert app.post("/api/drop", json={"case": CASE}).json() == {
            "case": CASE,
            "state": "open",
        }
        rows = {row["case"]: row for row in app.get("/api/rail").json()["cases"]}
        assert rows[CASE]["state"] == "draft"
        assert rows[CASE]["status"] == "draft in progress"
        assert rows[CASE]["pressable"] is True

    def test_it_leaves_every_other_case_alone(self, tree):
        app = browser(session_for(tree, "ada"))
        for case in CASES:
            read_and_record(app, case)
        kept = self.snapshot(tree, OTHER)
        app.post("/api/drop", json={"case": CASE})
        assert self.snapshot(tree, OTHER) == kept
        assert [row["case"] for row in app.get("/api/stage").json()["ready"]] == [OTHER]

    def test_the_stage_shows_it_held_back_and_reversible(self, tree):
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        app.post("/api/drop", json={"case": CASE})
        stage = app.get("/api/stage").json()
        assert stage["ready"] == []
        assert [row["case"] for row in stage["held_back"]] == [CASE]
        assert stage["held_back"][0]["title"]

    def test_putting_it_back_writes_the_record_again(self, tree):
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE, notes="21 agree", missing=["no fleet key"])
        before = self.snapshot(tree)
        app.post("/api/drop", json={"case": CASE})
        assert app.post("/api/put-back", json={"case": CASE}).json() == {
            "case": CASE,
            "state": "finished",
        }
        assert self.snapshot(tree) == before, "the same record, from the draft"
        stage = app.get("/api/stage").json()
        assert [row["case"] for row in stage["ready"]] == [CASE]
        assert stage["held_back"] == []

    def test_putting_it_back_appends_no_second_entry(self, tree):
        """One reader and one case never write two entries into one pull
        request, whichever route wrote the second one."""
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        app.post("/api/drop", json={"case": CASE})
        app.post("/api/put-back", json={"case": CASE})
        path = self.case_dir(tree) / "case.json"
        reviews = json.loads(path.read_text("utf-8"))["reviews"]
        assert [entry["submitted_by"] for entry in reviews] == ["ada"]

    def test_a_recorded_sitting_that_merged_is_untouchable(self, tree):
        """The drop removes the entry this reader appended and nothing else."""
        sign(tree, CASE, "sam")
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        app.post("/api/drop", json={"case": CASE})
        path = self.case_dir(tree) / "case.json"
        reviews = json.loads(path.read_text("utf-8"))["reviews"]
        assert [entry["submitted_by"] for entry in reviews] == ["sam"]

    def test_a_doctored_draft_cannot_write_python_into_the_list(self, tree):
        """The drop puts the case back on the unreviewed list from the draft,
        and the draft is a file the reader owns. The list is a module
        `pytest` imports, so the entry is checked before it is written."""
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        listing = tree / "tests" / "test_case_review.py"
        before = listing.read_text("utf-8")
        path = draft_file(tree, CASE)
        held = json.loads(path.read_text("utf-8"))
        held["unreviewed_entry"] = (
            f'    "{CASE}": "unread",\n}}\nimport pathlib\n'
            'pathlib.Path("/tmp/never").write_text("ran")\n'
            "JUNK: dict[str, str] = {\n"
        )
        path.write_text(json.dumps(held), encoding="utf-8")
        refused = app.post("/api/drop", json={"case": CASE})
        assert refused.status_code == 409
        assert listing.read_text("utf-8") == before, "a refusal changed the file"
        assert (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").is_file()

    def test_a_case_nobody_sat_keeps_no_empty_review_list(self, tree):
        """The key `record` wrote comes off with the entry that made it.

        A case left carrying `"reviews": []` stays in the diff, and the pull
        request then carries a case the reader dropped.
        """
        app = browser(session_for(tree, "ada"))
        read_and_record(app, CASE)
        app.post("/api/drop", json={"case": CASE})
        meta = json.loads((self.case_dir(tree) / "case.json").read_text("utf-8"))
        assert "reviews" not in meta

    def test_a_case_that_is_not_recorded_cannot_be_dropped(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        refused = app.post("/api/drop", json={"case": CASE})
        assert refused.status_code == 409
        assert "does not carry it" in refused.json()["detail"]

    def test_a_case_nobody_dropped_cannot_be_put_back(self, tree):
        app = browser(session_for(tree, "ada"))
        app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
        refused = app.post("/api/put-back", json={"case": CASE})
        assert refused.status_code == 409
        assert "not held back" in refused.json()["detail"]

    def test_both_carry_both_controls(self, tree):
        """They name a case, they write under the reader's own store, and
        they decide what the next press publishes."""
        session = session_for(tree, "ada")
        app = browser(session)
        read_and_record(app, CASE)
        untokened = TestClient(
            create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN
        )
        for path in ("/api/drop", "/api/put-back"):
            foreign = app.post(
                path, json={"case": CASE}, headers={"Sec-Fetch-Site": "cross-site"}
            )
            assert foreign.status_code == 403, path
            assert untokened.post(path, json={"case": CASE}).status_code == 403, path
        assert self.snapshot(tree) != {}
        assert (self.case_dir(tree) / "REVIEW-ada.md").is_file(), "nothing moved"


class TestWhatASuccessfulSubmitLeaves:
    """A successful submit deletes every draft it carried, and no other.

    That work is in a pull request by then, and a store that only grows is a
    store nobody trusts. Nothing here reaches GitHub: the spine is stubbed,
    which is the same seam the button's other tests use.
    """

    def spine(self, monkeypatch, ok=True):
        from evals.harness import submit as spine

        outcome = (
            spine.Outcome(author="ada", url="https://example.test/pr/9", closing="ok")
            if ok
            else spine.Outcome(
                author="ada",
                checks=(spine.Check(name="the digests hold", problems=("x",)),),
            )
        )
        monkeypatch.setattr(spine, "submission", lambda root, kind, **kw: outcome)

    def test_it_deletes_every_draft_it_carried(self, tree, monkeypatch):
        self.spine(monkeypatch)
        app = browser(session_for(tree, "ada", can_submit=True))
        for case in CASES:
            read_and_record(app, case)
        answer = app.post("/api/submit")
        assert answer.status_code == 200
        assert answer.json()["carried"] == sorted(CASES)
        assert answer.json()["kept"] == []
        for case in CASES:
            assert not draft_file(tree, case).exists(), case

    def test_a_case_the_reader_dropped_keeps_its_draft(self, tree, monkeypatch):
        self.spine(monkeypatch)
        app = browser(session_for(tree, "ada", can_submit=True))
        for case in CASES:
            read_and_record(app, case)
        app.post("/api/drop", json={"case": CASE})
        assert app.post("/api/submit").json()["carried"] == [OTHER]
        assert draft_file(tree, CASE).is_file(), "the held-back read survives"
        assert not draft_file(tree, OTHER).exists()

    def test_a_failed_submission_keeps_every_draft(self, tree, monkeypatch):
        """Nothing merged, so nothing is finished. The reader repairs the
        failures and presses again."""
        self.spine(monkeypatch, ok=False)
        app = browser(session_for(tree, "ada", can_submit=True))
        read_and_record(app, CASE)
        assert app.post("/api/submit").status_code == 409
        assert draft_file(tree, CASE).is_file()

    def test_submitting_with_every_case_dropped_is_refused(self, tree):
        app = browser(session_for(tree, "ada", can_submit=True))
        read_and_record(app, CASE)
        app.post("/api/drop", json={"case": CASE})
        refused = app.post("/api/submit")
        assert refused.status_code == 409
        assert "record the sitting" in refused.json()["detail"]


class TestTheTerminalReadsNoDraft:
    """Decision 38, as a property of the command line rather than a promise.

    A terminal reader of the draft file would be a second surface over the
    rules the app enforces, so the command line never opens one.
    """

    def test_no_command_line_path_reads_the_store(self):
        import inspect

        import webapp.sitting as app_module

        source = inspect.getsource(app_module.main)
        for name in ("load_draft", "draft_states", "draft_path", "discard_draft"):
            assert name not in source, f"the command line reads a draft through {name}"

    def test_listing_the_cases_left_starts_no_server_and_opens_no_draft(
        self, tree, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "evals.harness.sitting.unreviewed_cases", lambda root: list(CASES)
        )
        assert app_main(["--list"]) == 0
        assert capsys.readouterr().out.splitlines() == list(CASES)


class TestACorpusThatDoesNotLoad:
    """A ``case.json`` the loader refuses stops the session with one line.

    The entry that stops it is often the reader's own. A sitting recorded
    under a field set the loader has since replaced stays in the working tree,
    and the reader meets it at the next launch rather than at a commit. So the
    command line prints the case and the field it stopped on, and the reader
    is left with something to act on rather than a pydantic frame.
    """

    def stale(self, tree):
        """Give one case a sitting entry the loader does not accept."""
        path = tree / "evals" / "corpus" / OTHER / "case.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["reviews"] = [
            {
                "reviewer": "sam",
                "date": "2026-09-01",
                "read": [{"file": "source.md", "sha256": "0" * 64}],
                "document": "REVIEW-sam.md",
                "notes": "",
            }
        ]
        path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    def test_the_loader_names_the_case_and_the_field(self, tree):
        self.stale(tree)
        with pytest.raises(CorpusError) as raised:
            session_for(tree, "sam")
        assert OTHER in str(raised.value)
        assert "submitted_by" in str(raised.value)

    def test_the_command_line_prints_one_line_and_starts_no_server(
        self, tree, monkeypatch, capsys
    ):
        self.stale(tree)
        monkeypatch.setattr("webapp.sitting.REPO_ROOT", tree)
        monkeypatch.setattr("webapp.sitting.submit_spine.gh_login", lambda root: "sam")
        monkeypatch.setattr(
            "webapp.sitting.sittings.draft_root", lambda: drafts_root(tree)
        )
        assert app_main(["--submitted-by", "sam"]) == 1
        printed = capsys.readouterr()
        assert printed.err.startswith("cannot read the corpus:")
        assert OTHER in printed.err
        assert "Traceback" not in printed.err
        assert printed.out == ""


class TestThePageParses:
    """A JavaScript string literal never spans a line, so the block parses.

    A string literal opened on one line and closed on the next is a syntax
    error that stops the whole ``<script>`` block — so nothing on the page
    works and no request is ever made. Nothing else here would notice, because
    every test drives the endpoints rather than the page.

    Read through :func:`~webapp.page.client_script`, so this reads the bytes
    the app serves.
    """

    def script(self) -> str:
        return client_script("sitting.js")

    def test_no_string_literal_spans_a_line(self):
        spanning = [line for line in self.script().split("\n") if line.count('"') % 2]
        assert spanning == []

    def test_the_page_writes_newline_escapes_and_not_newlines(self):
        """The escape the reader's lists are split and joined on."""
        assert '.split("\\n")' in self.script()
        assert '.join("\\n")' in self.script()


class TestTheLayoutCoversTheCase:
    """The page lays the case out, and neither half may outgrow the other.

    The flat text the page used to print could not lose a part of a case: a
    block the layout did not know about still arrived as its own words. Markup
    can, so both halves are checked against what the corpus actually holds.
    """

    def kinds(self) -> set[str]:
        """The block kinds the page's own table carries a builder for."""
        table = client_script("sitting.js").split("const BLOCKS = {")[1]
        table = table.split("}")[0]
        return {entry.split(":")[0].strip() for entry in table.split(",")}

    @pytest.mark.parametrize(
        "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
    )
    def test_every_block_the_corpus_holds_has_a_builder(self, case_dir):
        """A kind the page cannot build drops that part of the case in silence."""
        prepared = sittings.prepare(case_dir)
        served = {block["kind"] for block in prepared.part_one_blocks}
        assert served <= self.kinds(), (
            f"{case_dir.name} serves {sorted(served - self.kinds())}, which the"
            " page's BLOCKS table has no builder for"
        )

    @pytest.mark.parametrize(
        "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
    )
    def test_every_recorded_record_reaches_the_mark_that_answers_it(self, case_dir):
        """The claim sentence is the anchor, so it has to be one on both sides.

        The page pairs a record card with its mark target by the claim
        sentence, because a target carries no position. A record whose sentence
        names no target would print with no mark on it, and a sentence written
        twice in one case would put the second card's mark on the first.
        """
        prepared = sittings.prepare(case_dir)
        anchored = {
            claim for target in prepared.mark_targets for claim in target.claims
        }
        titles = [
            record["title"]
            for part in prepared.part_two_blocks.values()
            for group in part["groups"]
            for record in group["records"]
        ]
        assert set(titles) <= anchored, "a recorded record reaches no mark target"
        assert len(titles) == len(set(titles)), "one sentence names two records"
