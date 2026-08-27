"""The Case Sitting app, and the domain logic under it.

The property that carries this module is the method's one rule: **the recorded
sets are not reachable until the reader's own list is in.** It is enforced by
the server, not by asking, so it is tested the way the review app's
configuration-blindness is — by checking the payload cannot carry it.

The rest is what a sitting writes: the filled document, the append-only entry
with a digest per file read, and the debt line. Everything a person doing this
by hand would write, so ``submit sitting`` cannot tell the two paths apart.

Deterministic, offline and credential-free, like the act itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evals.harness import sitting as sittings
from webapp.sitting import build_session, create_app

LOOPBACK = "http://127.0.0.1:8020"
CASE = "02-iot-fleet-telemetry"

#: What a browser puts on every request this page makes. Every writing endpoint
#: refuses without it, so the clients carry it and the tests that care about
#: its absence set their own.
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def tree(tmp_path):
    """A throwaway repo holding one real corpus case and a debt list."""
    root = Path(__file__).resolve().parents[1]
    case_dir = tmp_path / "evals" / "corpus" / CASE
    case_dir.parent.mkdir(parents=True)
    source = root / "evals" / "corpus" / CASE
    for item in source.rglob("*"):
        target = case_dir / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.is_file():
            target.write_bytes(item.read_bytes())
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_case_review.py").write_text(
        "UNREVIEWED: dict[str, str] = {\n"
        f'    "{CASE}": (\n'
        '        "18 STRIDE claims and 8 ASVS records, unread."\n'
        "    ),\n"
        '    "03-batch-data-pipeline": "17 STRIDE claims, unread.",\n'
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def client(tree):
    session = build_session(CASE, "ada", tree)
    app = TestClient(create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN)
    return app, session, tree


class TestTheOwnListRuleIsEnforced:
    def test_part_two_is_refused_before_the_own_list(self, client):
        """The method's only rule, and the reason it is server-side."""
        app, _, _ = client
        refused = app.get("/api/part-two")
        assert refused.status_code == 409
        assert "own list first" in refused.json()["detail"]

    def test_part_one_carries_no_reference_set(self, client):
        """A curious reader in devtools must not find the answers."""
        app, _, _ = client
        body = app.get("/api/part-one").json()
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
        app.post("/api/own-list", json={"items": ["someone spoofs a device"]})
        sets = app.get("/api/part-two").json()["frameworks"]
        assert "stride" in sets
        assert sets["stride"].strip()

    def test_finishing_without_a_list_is_refused(self, client):
        app, _, _ = client
        assert app.post("/api/finish", json={}).status_code == 409

    def test_an_empty_list_is_an_answer(self, client):
        """ "I saw nothing" is a real answer; it just has to be given."""
        app, _, _ = client
        app.post("/api/own-list", json={"items": []})
        assert app.get("/api/part-two").status_code == 200


class TestWhatASittingWrites:
    def finish(self, app):
        app.post("/api/own-list", json={"items": ["a spoofed device"]})
        app.get("/api/part-two")
        return app.post(
            "/api/finish",
            json={
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
        assert entry["reviewer"] == "ada"
        assert entry["document"] == "REVIEW-ada.md"
        read = {item["file"]: item["sha256"] for item in entry["read"]}
        assert set(read) == set(sittings.required_files(case_dir))
        for name, digest in read.items():
            actual = hashlib.sha256((case_dir / name).read_bytes()).hexdigest()
            assert digest == actual, f"{name}'s digest does not match the bytes"

    def test_the_required_files_derive_from_the_declaration(self, client):
        """A case that gains a package requires its set read, with no table edit."""
        _, _, tree = client
        case_dir = tree / "evals" / "corpus" / CASE
        files = sittings.required_files(case_dir)
        declared = json.loads((case_dir / "case.json").read_text("utf-8"))["frameworks"]
        for framework in declared:
            assert f"claims/{framework['name']}.json" in files

    def test_the_debt_line_is_cleared_and_the_others_are_not(self, client):
        app, _, tree = client
        self.finish(app)
        debt = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        assert CASE not in debt
        assert "03-batch-data-pipeline" in debt, "only this case comes off the list"

    def test_the_reviews_list_is_append_only(self, client):
        app, _, tree = client
        case_dir = tree / "evals" / "corpus" / CASE
        meta = json.loads((case_dir / "case.json").read_text("utf-8"))
        meta["reviews"] = [
            {
                "reviewer": "sam",
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
        assert after[0]["reviewer"] == "sam", "the recorded sitting was rewritten"

    def test_it_answers_with_the_command_and_the_paste(self, client):
        app, _, _ = client
        payload = self.finish(app).json()
        assert payload["command"] == "python -m evals.harness.run submit sitting"
        assert "Sitting:" in payload["paste"]
        assert f"evals/corpus/{CASE}/case.json" in payload["written"]


class TestThePosture:
    def test_a_rebound_host_is_refused(self, tree):
        """This app writes to the corpus, so it gets the same Host check."""
        session = build_session(CASE, "ada", tree)
        app = TestClient(create_app(session), base_url="http://attacker.example")
        assert app.get("/").status_code == 400
        assert app.post("/api/own-list", json={"items": []}).status_code == 400

    def test_the_reviewer_cannot_be_set_from_a_request(self, client):
        """#320's binding: a browser field naming the reviewer would break it."""
        app, _, _ = client
        refused = app.post("/api/own-list", json={"items": [], "reviewer": "sam"})
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
            "/api/own-list": {"items": ["a spoofed device"]},
            "/api/finish": {"marks": {}, "missing": [], "notes": "not mine"},
        }
        for path, body in writes.items():
            for site in ("cross-site", "same-site", "none"):
                refused = app.post(path, json=body, headers={"Sec-Fetch-Site": site})
                assert refused.status_code == 403, f"{path} accepted a {site} request"

        assert session.own_list is None, "a refused request set the own list"
        assert session.recorded is False, "a refused request recorded the sitting"
        assert not (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").exists()

    def test_a_bare_post_is_refused_too(self, tree):
        """No header at all is not the same as ``same-origin``.

        The check admits exactly one value rather than rejecting a list, so
        anything that sends nothing is refused too. This builds its own client
        because the shared one carries the header on every request.
        """
        session = build_session(CASE, "ada", tree)
        app = TestClient(create_app(session), base_url=LOOPBACK)
        assert app.post("/api/own-list", json={"items": []}).status_code == 403
        assert session.own_list is None

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
        assert app.post("/api/own-list", json={"items": [long_line]}).status_code == 422

        app.post("/api/own-list", json={"items": ["a spoofed device"]})
        for body in (
            {"marks": {}, "missing": [long_line], "notes": ""},
            {"marks": {"stride:1": long_line}, "missing": [], "notes": ""},
            {"marks": {long_line: "ok"}, "missing": [], "notes": ""},
            {"marks": {str(n): "ok" for n in range(201)}, "missing": [], "notes": ""},
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
        session = build_session(CASE, "ada", tree, can_submit=can_submit)
        app = TestClient(create_app(session), base_url=LOOPBACK, headers=SAME_ORIGIN)
        app.post("/api/own-list", json={"items": ["a stolen key"]})
        app.get("/api/part-two")
        app.post("/api/finish", json={"marks": {}, "missing": [], "notes": ""})
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
        """A page that never read this one cannot have the token."""
        app, _ = self.sat(tree)
        assert (
            app.post(
                "/api/submit", headers={"Sec-Fetch-Site": "same-origin"}
            ).status_code
            == 403
        )

    def test_a_wrong_token_is_refused(self, tree):
        app, _ = self.sat(tree)
        refused = app.post(
            "/api/submit",
            headers={"Sec-Fetch-Site": "same-origin", "X-Sitting-Token": "guessed"},
        )
        assert refused.status_code == 403

    def test_a_rebound_host_is_refused_here_too(self, tree):
        session = build_session(CASE, "ada", tree, can_submit=True)
        app = TestClient(create_app(session), base_url="http://attacker.example")
        assert app.post("/api/submit", headers=self.headers(session)).status_code == 400

    def test_submitting_before_recording_is_refused(self, tree):
        session = build_session(CASE, "ada", tree, can_submit=True)
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


class TestTheDebtHelper:
    """The debt list is a Python literal, so removing a line is worth testing."""

    def test_both_entry_shapes_are_removed(self, tree):
        assert sittings.clear_debt(tree, CASE) is True
        assert sittings.clear_debt(tree, "03-batch-data-pipeline") is True
        text = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        assert text.count('": ') == 0
        assert text.endswith("}\n"), "the dict is still a dict"

    def test_a_case_not_listed_writes_nothing(self, tree):
        before = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        assert sittings.clear_debt(tree, "99-not-a-case") is False
        assert (tree / "tests" / "test_case_review.py").read_text("utf-8") == before

    def test_the_cases_in_debt_are_listed_in_file_order(self, tree):
        assert sittings.cases_in_debt(tree) == [CASE, "03-batch-data-pipeline"]
