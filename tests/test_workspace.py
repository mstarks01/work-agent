"""Session workspace boundaries, using only the offline engine fixture."""

import pytest

from tests import test_webapp_questions as web
from tests.test_webapp import BREAKOUT, SAME_ORIGIN, posted

tiers = web.tiers


@pytest.mark.parametrize("name", ["", " " * 3, "x" * 121, None, [], 42])
def test_invalid_names_do_not_claim_a_run(tiers, name):
    with web.client_for(tiers, web.PausingRunner()) as client:
        response = client.post(
            "/analyze", json=posted("System") | {"name": name}, headers=SAME_ORIGIN
        )
        assert response.status_code == 400
        assert client.get("/workspace/runs").json() == []


def test_reopen_keeps_questions_revision_and_related_report(tiers):
    with web.client_for(tiers, web.PausingRunner()) as client:
        response = client.post(
            "/analyze",
            json=posted("Private system description")
            | {"name": BREAKOUT, "questions": True},
            headers=SAME_ORIGIN,
        )
        run = response.json()["run"]
        paused = web.event(client.get(f"/events/{run}").text, "questions")
        listing = client.get("/workspace/runs")
        assert "no-store" in listing.headers["cache-control"]
        assert "Private system description" not in listing.text
        assert listing.json()[0]["name"] == BREAKOUT
        detail = client.get(f"/workspace/runs/{run}").json()
        assert detail["questions"] == paused
        assert detail["description"] == "Private system description"
        saved = client.post(
            f"/answer/{run}",
            json={"save": True, "links": [web.LINK], "revision": 0},
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 200
        assert client.get(f"/workspace/runs/{run}").json()["questions"]["revision"] == 1
        resumed = client.post(
            f"/answer/{run}", json={"links": [], "revision": 1}, headers=SAME_ORIGIN
        )
        child = resumed.json()["run"]
        client.get(f"/events/{child}")
        rows = client.get("/workspace/runs").json()
        assert len(rows) == 1
        assert rows[0]["name"] == BREAKOUT
        assert rows[0]["reports"] == [{"run": child, "url": f"/report/{child}"}]
        assert client.get(f"/workspace/runs/{run}").json()["run"] == child


def test_workspace_missing_run_and_cross_origin_write(tiers):
    with web.client_for(tiers, web.PausingRunner()) as client:
        assert client.get("/workspace/runs/missing").status_code == 404
        assert (
            client.post(
                "/analyze", json=posted("System") | {"name": "Name"}
            ).status_code
            == 403
        )
        assert client.get("/workspace/runs").json() == []
