"""The adaptive question page saves as the submitter answers (ADR 0078, #1632).

These tests drive the shipped page script under ``node`` with the stand-in
DOM of :mod:`tests.test_webapp_questions`, and mock each response the service
would give. The service's own rules are tested with the service; here the
subject is what the page sends, when, and what it keeps on screen.
"""

from __future__ import annotations

import json

from tests.test_webapp_questions import _run_form_script, _text_row

YES_NO = [{"id": "yes", "name": ""}, {"id": "no", "name": ""}]


def capability(name, label, **more):
    return _text_row(["", "", "", "", "", name], label) | {
        "kind": "capability",
        "form": "choice",
        "choices": YES_NO,
        "group": "capabilities",
        "group_heading": "About the application",
        **more,
    }


def answer(q: dict, value: str) -> dict:
    """``q`` as the service lists an answered question: with its answer."""
    return q | {"answer": {"key": q["key"], "value": value}}


def payload(**over):
    """A questions payload with every list empty, then ``over``."""
    base = {
        "run": "r1",
        "revision": 0,
        "policy_version": "adaptive-1",
        "link_questions": [],
        "early_questions": [],
        "early_blocked": [],
        "early_unnecessary": [],
        "early_held_back": [],
        "early_below_floor": [],
        "early_stop": None,
        "early_summary": None,
        "framework_gates": {},
        "skipped_early": [],
        "skipped_links": [],
        "answered_early": [],
        "answered_links": [],
    }
    return base | over


AUTH = capability("authentication", "Sign-in?")
OAUTH = capability("oauth", "OAuth?")
PART = capability("oauth-client", "An OAuth client?", parent=OAUTH["key"])
WHO = _text_row(["", "", "", "who runs it?", "", ""], "Who runs it?") | {
    "group": "operations",
    "group_heading": "Operations",
}

PRELUDE = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
streams[0].listeners.questions({ data: JSON.stringify(FIRST) });
const walk = (n, out = []) => { for (const c of n.children || [])
  if (typeof c === "object") { out.push(c); walk(c, out); } return out; };
const text = (n) => typeof n === "string" ? n
  : [n.textContent || "", ...(n.children || []).map(text)].join("");
const selects = () => walk(ids.list).filter((n) => n.tag === "select");
const record = (url, init) => calls.push({ url, body: init && init.body ? JSON.parse(init.body) : null });
const answer = (q, value) => ({ ...q, answer: { key: q.key, value } });
"""


def run(first: dict, steps: str) -> dict:
    return _run_form_script(PRELUDE.replace("FIRST", json.dumps(first)) + steps)


def test_a_chosen_option_is_saved_at_once_and_the_row_stays_in_place_compact():
    first = payload(early_questions=[AUTH, OAUTH])
    after = payload(
        revision=1, early_questions=[OAUTH], answered_early=[answer(AUTH, "yes")]
    )
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  return {{ ok: true, json: async () => ({json.dumps(after)}) }}; }};
const [auth] = selects();
auth.value = "yes"; auth.listeners.change();
calls.push({{ status: ids["save-status"].textContent }});
await settle(); await settle();
const box = ids.list.children[0];
calls.push({{ status: ids["save-status"].textContent, saveHidden: ids.save.hidden,
  rows: box.children.filter((c) => c.tag === "p").map(text),
  boxes: ids.list.children.length, earlierHidden: ids.earlier.hidden }});
"""
    seen = run(first, steps)["calls"]
    sent = next(c for c in seen if c.get("url") == "/answer/r1")
    assert sent["body"] == {
        "links": [],
        "facts": [{"key": AUTH["key"], "value": "yes"}],
        "save": True,
        "skip": [],
        "presented": [AUTH["key"], OAUTH["key"]],
        "revision": 0,
    }
    assert seen[1] == {"status": "Not saved yet"}, "before the save is sent"
    end = seen[-1]
    assert end["status"] == "" and end["saveHidden"]
    # The answered row is first, compact, with "Change"; the open row follows.
    assert end["rows"][0].startswith("Sign-in? — yes Change")
    assert end["rows"][1].startswith("OAuth?")
    assert end["boxes"] == 1 and end["earlierHidden"], "nothing moved to Your answers"


def test_a_typed_answer_waits_before_it_is_saved():
    first = payload(early_questions=[WHO])
    steps = """
globalThis.fetch = async (url, init) => { record(url, init);
  return { ok: true, json: async () => ({ run: "r1" }) }; };
const [input] = walk(ids.list).filter((n) => n.tag === "input");
input.value = "the platform team"; input.listeners.input({ type: "input" });
await settle(); await settle();
calls.push({ sentYet: calls.some((c) => c.url === "/answer/r1"), status: ids["save-status"].textContent });
await new Promise((r) => setTimeout(r, 450));
"""
    seen = run(first, steps)["calls"]
    marker = next(c for c in seen if "sentYet" in c)
    assert marker == {"sentYet": False, "status": "Not saved yet"}
    sent = [c for c in seen if c.get("url") == "/answer/r1"]
    assert len(sent) == 1
    assert sent[0]["body"]["facts"] == [
        {"key": WHO["key"], "value": "the platform team"}
    ]


def test_an_edit_made_while_a_save_is_in_flight_is_sent_next():
    first = payload(early_questions=[AUTH, OAUTH])
    steps = """
let release = null;
globalThis.fetch = (url, init) => { record(url, init);
  return new Promise((r) => { release = () => r({ ok: true, json: async () => ({ run: "r1" }) }); }); };
const [auth, oauth] = selects();
auth.value = "yes"; auth.listeners.change(); await settle();
oauth.value = "no"; oauth.listeners.change(); await settle();
calls.push({ inFlight: calls.filter((c) => c.url === "/answer/r1").length, status: ids["save-status"].textContent });
release(); await settle(); await settle(); await settle();
release(); await settle(); await settle();
calls.push({ status: ids["save-status"].textContent });
"""
    seen = run(first, steps)["calls"]
    posts = [c["body"]["facts"] for c in seen if c.get("url") == "/answer/r1"]
    assert next(c for c in seen if "inFlight" in c) == {
        "inFlight": 1,
        "status": "",
    }
    assert posts == [
        [{"key": AUTH["key"], "value": "yes"}],
        [{"key": OAUTH["key"], "value": "no"}],
    ]
    assert seen[-1] == {"status": ""}


def test_a_stale_save_reads_the_answers_again_and_keeps_only_what_does_not_contradict_them():
    """Another tab saved first: the page resends the edit the other tab did
    not touch, and shows the one it did for the submitter to settle."""
    first = payload(early_questions=[AUTH, OAUTH])
    theirs = payload(
        revision=2, early_questions=[OAUTH], answered_early=[answer(AUTH, "no")]
    )
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  if (url.startsWith("/workspace/runs/")) return {{ ok: true, json: async () => ({{ run: "r1", questions: {json.dumps(theirs)} }}) }};
  const stale = calls.filter((c) => c.url === "/answer/r1").length === 1;
  return stale ? {{ ok: false, status: 409, json: async () => ({{ message: "another tab" }}) }}
    : {{ ok: true, json: async () => ({{ run: "r1" }}) }}; }};
const [auth, oauth] = selects();
auth.value = "yes"; auth.listeners.change();
oauth.value = "no"; oauth.listeners.change();
await settle(); await settle(); await settle(); await settle();
const box = ids.list.children[0];
const conflict = walk(box).find((n) => n.className === "conflict");
calls.push({{ status: ids["save-status"].textContent, conflict: conflict ? text(conflict) : null,
  buttons: conflict ? walk(conflict).filter((n) => n.tag === "button").map((b) => b.textContent) : [] }});
walk(conflict).find((n) => n.textContent === "Take theirs").listeners.click();
await settle(); await settle();
calls.push({{ status: ids["save-status"].textContent, value: auth.value,
  gone: !walk(box).some((n) => n.className === "conflict") }});
"""
    seen = run(first, steps)["calls"]
    urls = [c["url"] for c in seen if "url" in c][1:]
    assert urls == ["/answer/r1", "/workspace/runs/r1", "/answer/r1"]
    resent = [c for c in seen if c.get("url") == "/answer/r1"][1]["body"]
    assert resent["facts"] == [{"key": OAUTH["key"], "value": "no"}]
    assert resent["revision"] == 2
    shown = next(c for c in seen if "conflict" in c)
    assert shown["status"].startswith("Not saved: an answer changed elsewhere")
    assert "Changed elsewhere to “no”" in shown["conflict"]
    assert shown["buttons"] == ["Keep mine", "Take theirs"]
    assert seen[-1] == {"status": "", "value": "no", "gone": True}


def test_an_edit_over_an_answer_removed_elsewhere_waits_for_the_submitter():
    """Another tab removed the saved answer this edit changes. The edit is
    shown as a conflict and is not sent until the submitter settles it."""
    first = payload(early_questions=[OAUTH], answered_early=[answer(AUTH, "yes")])
    theirs = payload(revision=2, early_questions=[AUTH, OAUTH])
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  if (url.startsWith("/workspace/runs/")) return {{ ok: true, json: async () => ({{ run: "r1", questions: {json.dumps(theirs)} }}) }};
  return {{ ok: false, status: 409, json: async () => ({{ message: "another tab" }}) }}; }};
walk(ids.earlier).find((n) => n.textContent === "Change").listeners.click();
const select = walk(ids.earlier).find((n) => n.tag === "select");
select.value = "no"; select.listeners.change();
await settle(); await settle(); await settle(); await settle();
const conflict = walk(ids.earlier).find((n) => n.className === "conflict");
calls.push({{ state: ids["save-status"].dataset.state, conflict: conflict ? text(conflict) : null }});
"""
    seen = run(first, steps)["calls"]
    assert [c["url"] for c in seen if "url" in c][1:] == [
        "/answer/r1",
        "/workspace/runs/r1",
    ]
    end = seen[-1]
    assert end["state"] == "conflict"
    assert end["conflict"].startswith("Removed elsewhere.")


def test_an_answered_row_that_keeps_the_focus_offers_no_skip():
    """The service takes a skip only of a listed question, so a row a save
    answered shows no "Skip for now", even while its editor stays open."""
    first = payload(early_questions=[AUTH, OAUTH])
    after = payload(
        revision=1, early_questions=[OAUTH], answered_early=[answer(AUTH, "yes")]
    )
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  return {{ ok: true, json: async () => ({json.dumps(after)}) }}; }};
const [auth] = selects();
auth.focus(); auth.value = "yes"; auth.listeners.change();
await settle(); await settle();
const skips = walk(ids.list).filter((n) => n.textContent === "Skip for now");
calls.push({{ shown: skips.map((s) => !s.hidden) }});
"""
    seen = run(first, steps)["calls"]
    assert seen[-1] == {"shown": [False, True]}


def test_a_failed_save_keeps_the_answer_and_save_now_retries():
    first = payload(early_questions=[AUTH])
    steps = """
let broken = true;
globalThis.fetch = async (url, init) => { record(url, init);
  if (broken) throw new Error("offline");
  return { ok: true, json: async () => ({ run: "r1" }) }; };
const [auth] = selects();
auth.value = "yes"; auth.listeners.change();
await settle(); await settle(); await settle();
calls.push({ status: ids["save-status"].textContent, value: auth.value, saveShown: !ids.save.hidden });
broken = false;
ids.save.listeners.click(); await settle(); await settle();
calls.push({ status: ids["save-status"].textContent, saveShown: !ids.save.hidden });
"""
    seen = run(first, steps)["calls"]
    failed = next(c for c in seen if "value" in c)
    assert failed["status"].startswith("Not saved")
    assert failed["value"] == "yes" and failed["saveShown"]
    posts = [c for c in seen if c.get("url") == "/answer/r1"]
    assert len(posts) == 2 and posts[1]["body"]["facts"] == [
        {"key": AUTH["key"], "value": "yes"}
    ]
    assert seen[-1] == {"status": "", "saveShown": False}


def test_a_refused_answer_is_not_sent_again_until_it_changes():
    first = payload(early_questions=[AUTH, OAUTH])
    steps = """
globalThis.fetch = async (url, init) => { record(url, init);
  return { ok: false, status: 400, json: async () => ({ message: "refused" }) }; };
const [auth, oauth] = selects();
auth.value = "yes"; auth.listeners.change(); await settle(); await settle();
oauth.value = "no"; oauth.listeners.change(); await settle(); await settle();
calls.push({ problem: ids["answer-problem"].textContent, status: ids["save-status"].textContent });
"""
    seen = run(first, steps)["calls"]
    posts = [c["body"]["facts"] for c in seen if c.get("url") == "/answer/r1"]
    assert posts == [
        [{"key": AUTH["key"], "value": "yes"}],
        [{"key": OAUTH["key"], "value": "no"}],
    ], "the refused answer stays out of the next request"
    assert seen[-1]["problem"] == "refused"
    assert seen[-1]["status"] == "Not saved: refused"


def test_the_start_saves_what_is_pending_and_starts_once():
    first = payload(early_questions=[AUTH])
    steps = """
globalThis.fetch = async (url, init) => { record(url, init);
  await new Promise((r) => setTimeout(r, 5));
  return { ok: true, json: async () => ({ run: "r2" }) }; };
const [auth] = selects();
auth.value = "yes";
const first = ids.continue.listeners.click();
const second = ids.continue.listeners.click();
calls.push({ disabled: ids.continue.disabled });
await first; await second; await settle();
"""
    seen = run(first, steps)["calls"]
    posts = [c["body"] for c in seen if c.get("url") == "/answer/r1"]
    assert posts[0]["save"] is True and posts[0]["facts"] == [
        {"key": AUTH["key"], "value": "yes"}
    ]
    assert posts[1] == {
        "links": [],
        "facts": [],
        "presented": [AUTH["key"]],
        "revision": 0,
    }
    assert len(posts) == 2, "one save, one start"
    assert next(c for c in seen if "disabled" in c) == {"disabled": True}


def test_a_start_during_a_save_waits_for_it_and_starts_once():
    """The submitter chooses an option and starts before its save answers: the
    start waits for that save, sends the answer once and starts once."""
    first = payload(early_questions=[AUTH])
    steps = """
globalThis.fetch = async (url, init) => { record(url, init);
  await new Promise((r) => setTimeout(r, 5));
  return { ok: true, json: async () => ({ run: "r2" }) }; };
const [auth] = selects();
auth.value = "yes"; auth.listeners.change(); await settle();
await ids.continue.listeners.click(); await settle();
calls.push({ problem: ids["answer-problem"].hidden ? null : ids["answer-problem"].textContent });
"""
    seen = run(first, steps)["calls"]
    posts = [c["body"] for c in seen if c.get("url") == "/answer/r1"]
    assert [post.get("save") for post in posts] == [True, None], "one save, one start"
    assert posts[0]["facts"] == [{"key": AUTH["key"], "value": "yes"}]
    assert seen[-1] == {"problem": None}


def test_the_skip_button_stays_disabled_until_its_skip_ends():
    """A click on the skip button reaches the question area too, and the
    area redraws the progress. The button stays disabled until its save
    answers."""
    first = payload(early_questions=[AUTH])
    steps = """
let release = null;
globalThis.fetch = (url, init) => { record(url, init);
  return new Promise((r) => { release = () => r({ ok: true, json: async () => ({ run: "r1" }) }); }); };
const skipped = ids.skip.listeners.click();
ids.asked.listeners.click();
await settle();
calls.push({ during: ids.skip.disabled });
release(); await skipped; await settle();
calls.push({ after: ids.skip.disabled });
"""
    seen = run(first, steps)["calls"]
    assert next(c for c in seen if "during" in c) == {"during": True}
    assert seen[-1] == {"after": False}


def test_a_lost_response_whose_save_landed_is_acknowledged_not_a_conflict():
    """The service serves an answer with every field and its defaults. Read
    back after a lost response, it is the submitter's own answer, saved."""
    first = payload(early_questions=[AUTH])
    held = AUTH | {
        "answer": {"key": AUTH["key"], "value": "yes", "facets": None, "detail": ""}
    }
    theirs = payload(revision=1, answered_early=[held])
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  if (url.startsWith("/workspace/runs/")) return {{ ok: true, json: async () => ({{ run: "r1", questions: {json.dumps(theirs)} }}) }};
  throw new Error("lost"); }};
const [auth] = selects();
auth.value = "yes"; auth.listeners.change();
await settle(); await settle(); await settle(); await settle();
calls.push({{ status: ids["save-status"].textContent,
  conflict: walk(ids.list).some((n) => n.className === "conflict") }});
"""
    seen = run(first, steps)["calls"]
    assert len([c for c in seen if c.get("url") == "/answer/r1"]) == 1
    assert seen[-1] == {"status": "", "conflict": False}


def test_a_save_larger_than_one_request_goes_in_batches():
    """One request carries at most the published limits. The start saves
    the rest in the next request, then starts once."""
    first = payload(early_questions=[AUTH, OAUTH])
    steps = """
ANSWER_LIMITS.facts = 1;
globalThis.fetch = async (url, init) => { record(url, init);
  return { ok: true, json: async () => ({ run: "r2" }) }; };
const [auth, oauth] = selects();
auth.value = "yes"; oauth.value = "no";
await ids.continue.listeners.click(); await settle();
"""
    seen = run(first, steps)["calls"]
    posts = [c["body"] for c in seen if c.get("url") == "/answer/r1"]
    assert [post["facts"] for post in posts] == [
        [{"key": AUTH["key"], "value": "yes"}],
        [{"key": OAUTH["key"], "value": "no"}],
        [],
    ]
    assert [post.get("save") for post in posts] == [True, True, None]


def test_leaving_with_an_unsaved_edit_sends_it_and_asks_to_confirm():
    first = payload(early_questions=[AUTH])
    steps = """
globalThis.fetch = (url, init) => { record(url, init); return new Promise(() => {}); };
const leave = () => { const event = { returnValue: undefined, preventDefault() { this.asked = true; } };
  window.listeners.beforeunload(event); return { asked: Boolean(event.asked), value: event.returnValue }; };
calls.push({ clean: leave() });
const [auth] = selects();
auth.value = "yes"; auth.listeners.change();
calls.push({ dirty: leave() });
"""
    seen = run(first, steps)["calls"]
    assert next(c for c in seen if "clean" in c) == {"clean": {"asked": False}}
    assert next(c for c in seen if "dirty" in c) == {
        "dirty": {"asked": True, "value": ""}
    }
    posts = [c["body"]["facts"] for c in seen if c.get("url") == "/answer/r1"]
    assert posts == [[{"key": AUTH["key"], "value": "yes"}]], "sent at once"


def test_a_contradiction_the_service_names_is_corrected_in_one_save():
    """A "no" to a parent over a saved "yes" to its part is refused, and the
    refusal names both. The page offers to answer the part "no" too, and
    sends the two answers in one save."""
    first = payload(early_questions=[OAUTH], answered_early=[answer(PART, "yes")])
    named = {"part": PART["key"], "ancestor": OAUTH["key"]}
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  const refused = calls.filter((c) => c.url === "/answer/r1").length === 1;
  return refused
    ? {{ ok: false, status: 400, json: async () => ({{ message: "answer both", contradiction: {json.dumps(named)} }}) }}
    : {{ ok: true, json: async () => ({{ run: "r1" }}) }}; }};
const [oauth] = selects();
oauth.value = "no"; oauth.listeners.change();
await settle(); await settle();
const fix = walk(ids["answer-problem"]).find((n) => n.tag === "button");
calls.push({{ offered: fix ? fix.textContent : null }});
fix.listeners.click(); await settle(); await settle();
calls.push({{ status: ids["save-status"].dataset.state }});
"""
    seen = run(first, steps)["calls"]
    offered = next(c for c in seen if "offered" in c)["offered"]
    assert (
        offered == "Answer \u201cAn OAuth client?\u201d \u201cno\u201d too, in one save"
    )
    posts = [c["body"]["facts"] for c in seen if c.get("url") == "/answer/r1"]
    assert posts == [
        [{"key": OAUTH["key"], "value": "no"}],
        [{"key": OAUTH["key"], "value": "no"}, {"key": PART["key"], "value": "no"}],
    ]
    assert seen[-1] == {"status": "saved"}


def test_a_lost_start_response_follows_the_run_that_started():
    first = payload(early_questions=[AUTH])
    steps = """
globalThis.fetch = async (url, init) => { record(url, init);
  if (url.startsWith("/workspace/runs/")) return { ok: true, json: async () => ({ run: "r2", status: "running" }) };
  throw new Error("lost"); };
await ids.continue.listeners.click(); await settle();
"""
    seen = run(first, steps)
    assert seen["streams"] == ["/events/r1", "/events/r2"]
    assert seen["asked"] is False


def test_a_focused_editor_survives_another_answer_s_save():
    """The submitter is typing in one row while another row's save brings the
    list back changed: the typed row keeps its node, its text and the focus,
    and the new row goes where the service lists it."""
    first = payload(early_questions=[AUTH, WHO])
    after = payload(
        revision=1,
        early_questions=[WHO, OAUTH],
        answered_early=[answer(AUTH, "yes")],
    )
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  return {{ ok: true, json: async () => ({json.dumps(after)}) }}; }};
const [who] = walk(ids.list).filter((n) => n.tag === "input" && n.className !== "answer-detail");
who.value = "the platform tea"; who.focus();
const [auth] = selects();
auth.value = "yes"; auth.listeners.change();
await settle(); await settle();
const [whoAgain] = walk(ids.list).filter((n) => n.tag === "input" && n.className !== "answer-detail");
calls.push({{ same: who === whoAgain, value: whoAgain.value, focused: document.activeElement === who,
  order: ids.list.children.map((box) => box.children.filter((c) => c.tag === "p").map((p) => text(p).slice(0, 12))),
  note: ids.changes.textContent }});
"""
    seen = run(first, steps)["calls"]
    end = seen[-1]
    assert end["same"] and end["value"] == "the platform tea" and end["focused"]
    assert end["order"] == [["Sign-in? \u2014 y"], ["Who runs it?"], ["OAuth? (leav"]]
    assert end["note"] == "1 relevant question(s) added."


def test_a_new_row_ranked_above_a_focused_editor_waits_until_the_focus_leaves():
    """The submitter is typing in one row while a save lists a new question
    above it. The new row waits, so the typed row does not move, and it is
    drawn where the service lists it once the focus leaves."""
    first = payload(early_questions=[AUTH, WHO])
    after = payload(
        revision=1,
        early_questions=[OAUTH, WHO],
        answered_early=[answer(AUTH, "yes")],
    )
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  return {{ ok: true, json: async () => ({json.dumps(after)}) }}; }};
const [who] = walk(ids.list).filter((n) => n.tag === "input" && n.className !== "answer-detail");
who.value = "the platform tea"; who.focus();
const [auth] = selects();
auth.value = "yes"; auth.listeners.change();
await settle(); await settle();
const order = () => ids.list.children.map((box) => box.children.filter((c) => c.tag === "p").map((p) => text(p).slice(0, 12)));
calls.push({{ typing: order(), note: ids.changes.textContent }});
document.activeElement = null; ids.list.listeners.focusout(); await settle();
calls.push({{ left: order(), note: ids.changes.textContent }});
"""
    seen = run(first, steps)["calls"]
    typing = next(c for c in seen if "typing" in c)
    assert typing["typing"] == [["Sign-in? \u2014 y"], ["Who runs it?"]]
    assert typing["note"] == (
        "1 relevant question(s) added. 1 of them show when you leave the answer"
        " you are typing."
    )
    left = seen[-1]
    assert left["left"] == [["OAuth? (leav"], ["Sign-in? \u2014 y"], ["Who runs it?"]]
    assert left["note"] == "1 relevant question(s) added."


def test_a_no_lists_the_parts_as_no_longer_needed_and_says_so():
    first = payload(
        early_questions=[OAUTH, AUTH],
        early_blocked=[PART | {"reason": "parent-undecided"}],
    )
    after = payload(
        revision=1,
        early_questions=[AUTH],
        early_unnecessary=[PART | {"reason": "parent-absent"}],
        answered_early=[answer(OAUTH, "no")],
    )
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  return {{ ok: true, json: async () => ({json.dumps(after)}) }}; }};
const [oauth] = selects();
oauth.value = "no"; oauth.listeners.change();
await settle(); await settle();
calls.push({{ note: ids.changes.textContent, drawn: selects().length }});
"""
    seen = run(first, steps)["calls"]
    # The part was never listed, so it is not "no longer needed" on this page.
    assert seen[-1] == {"note": "", "drawn": 1}


def test_an_answered_question_that_leaves_the_list_says_so():
    first = payload(early_questions=[OAUTH, PART | {"parent": None}])
    after = payload(
        revision=1,
        early_questions=[],
        early_unnecessary=[PART | {"reason": "parent-absent"}],
        answered_early=[answer(OAUTH, "no")],
        early_stop="nothing-left",
    )
    steps = f"""
globalThis.fetch = async (url, init) => {{ record(url, init);
  return {{ ok: true, json: async () => ({json.dumps(after)}) }}; }};
const [oauth] = selects();
oauth.value = "no"; oauth.listeners.change();
await settle(); await settle();
calls.push({{ note: ids.changes.textContent, drawn: selects().length }});
"""
    seen = run(first, steps)["calls"]
    assert seen[-1] == {"note": "1 question(s) are no longer needed.", "drawn": 0}


def test_skip_for_now_sends_one_skip_and_hides_the_row():
    first = payload(early_questions=[AUTH, OAUTH])
    steps = """
globalThis.fetch = async (url, init) => { record(url, init);
  return { ok: true, json: async () => ({ run: "r1" }) }; };
const box = ids.list.children[0];
const row = box.children.find((c) => c.tag === "p");
walk(row).find((n) => n.tag === "button" && n.textContent === "Skip for now").listeners.click();
await settle(); await settle();
calls.push({ hidden: row.hidden, status: ids["save-status"].textContent });
"""
    seen = run(first, steps)["calls"]
    sent = next(c for c in seen if c.get("url") == "/answer/r1")["body"]
    assert sent == {
        "links": [],
        "facts": [],
        "save": True,
        "skip": [AUTH["key"]],
        "presented": [AUTH["key"], OAUTH["key"]],
        "revision": 0,
    }
    assert seen[-1] == {"hidden": True, "status": ""}


def test_the_page_names_no_round():
    first = payload(
        early_questions=[AUTH],
        early_summary={
            "recommended": 1,
            "recommended_choices": 1,
            "introduced": 0,
            "choices": 0,
            "settled": 0,
            "partial": 0,
            "unknown": 0,
            "skipped": 0,
            "held_back": 0,
            "below_floor": 0,
            "blocked": 0,
            "unnecessary": 0,
            "applicability": {},
        },
    )
    steps = """
calls.push({ said: text(ids.questions).toLowerCase(), saveHidden: ids.save.hidden });
"""
    seen = run(first, steps)["calls"]
    assert "round" not in seen[-1]["said"]
    assert "next" not in seen[-1]["said"]
    assert seen[-1]["saveHidden"], "no save button shows while nothing is unsaved"
