"""Lifting an older artifact into a copy the loader reads.

The loader takes one version and refuses every other. That is right, and it
left the repeat archive under ``evals/runs/`` unreadable — which is the data
the band reading is calibrated on (#916). These drive the lift, and the
refusals that keep it from becoming a compatibility shim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness.artifact import ARTIFACT_VERSION, load_artifact
from evals.harness.migrate import (
    OLDEST,
    STEPS,
    migrate,
    migrate_file,
    missing_steps,
)
from evals.harness.provenance import ProvenanceError
from tests.test_evals_provenance import provenance, sampling  # noqa: F401
from tests.test_evals_stability import score, write_run


def older(tmp_path, name, record, **overrides):
    """One artifact shaped as version 6 shapes it: four coverage metrics under
    the names that version spells them with."""
    path = write_run(tmp_path, name, record, [score("01", 4, [0, 1])], **overrides)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["artifact_version"] = 6
    metrics = raw["scores"][0]["metrics"]
    metrics["recall"] = metrics.pop("reference_coverage")
    metrics["must_find_recall"] = 0.5
    metrics["element_accuracy"] = 1.0
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path


class TestTheTableIsCheckedAgainstItsRegistry:
    """A chain composed by lookup, and a lookup that says when it has a hole."""

    def test_every_version_between_the_oldest_and_now_has_a_step(self):
        """The gap this finds is a version bump that forgot a migration: the
        module would claim to lift an artifact and refuse it at the operator."""
        assert missing_steps() == []

    def test_the_steps_stop_at_the_current_version(self):
        """A step keyed at or past the current version lifts to nothing."""
        assert max(STEPS) < ARTIFACT_VERSION
        assert min(STEPS) >= OLDEST


class TestOneArtifactLifted:
    def test_the_four_coverage_metrics_are_renamed_and_no_value_moves(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        path = older(tmp_path, "old.json", provenance(sampling))
        before = json.loads(path.read_text(encoding="utf-8"))["scores"][0]["metrics"]
        out = tmp_path / "new.json"

        assert migrate_file(path, out) == 6

        after = json.loads(out.read_text(encoding="utf-8"))["scores"][0]["metrics"]
        assert after["reference_coverage"] == before["recall"]
        assert after["must_find_coverage"] == before["must_find_recall"]
        assert after["element_agreement"] == before["element_accuracy"]
        assert not {"recall", "must_find_recall", "element_accuracy"} & set(after)

    def test_the_lifted_copy_is_what_the_loader_reads(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """The whole point, and the only assertion that proves it."""
        path = older(tmp_path, "old.json", provenance(sampling))
        with pytest.raises(ProvenanceError, match="unsupported artifact_version 6"):
            load_artifact(path)

        migrate_file(path, tmp_path / "new.json")

        assert load_artifact(tmp_path / "new.json").mode == "analysis"

    def test_the_source_is_left_alone(self, tmp_path, sampling):  # noqa: F811
        """One way, into a copy. A rewrite in place is what #855 deleted."""
        path = older(tmp_path, "old.json", provenance(sampling))
        was = path.read_bytes()

        migrate_file(path, tmp_path / "new.json")

        assert path.read_bytes() == was

    def test_lifting_twice_is_not_an_error(self, tmp_path, sampling):  # noqa: F811
        """So an operator may point this at a directory again without pruning."""
        path = older(tmp_path, "old.json", provenance(sampling))
        migrate_file(path, tmp_path / "one.json")
        migrate_file(tmp_path / "one.json", tmp_path / "two.json")

        assert json.loads((tmp_path / "two.json").read_text()) == json.loads(
            (tmp_path / "one.json").read_text()
        )

    def test_a_half_migrated_row_keeps_the_value_it_has(self):
        """A file carrying both spellings keeps the value under the name this
        version reads, rather than having it overwritten by the other."""
        raw = migrate(
            {
                "artifact_version": 6,
                "scores": [{"metrics": {"recall": 0.1, "reference_coverage": 0.9}}],
            }
        )

        assert raw["scores"][0]["metrics"]["reference_coverage"] == 0.9
        assert "recall" in raw["scores"][0]["metrics"]


class TestWhatItRefuses:
    """Refusing is the half that keeps this from becoming a shim."""

    @pytest.mark.parametrize("version", [None, "6", 6.0, True])
    def test_a_version_that_is_not_a_number_is_refused(self, version):
        with pytest.raises(ProvenanceError, match="does not say which"):
            migrate({"artifact_version": version})

    def test_a_version_newer_than_this_build_is_refused(self):
        with pytest.raises(ProvenanceError, match="newer than this build"):
            migrate({"artifact_version": ARTIFACT_VERSION + 1})

    def test_a_version_older_than_the_oldest_step_is_refused(self):
        """Those predate recorded served identities, so what answered cannot be
        recovered and a lift would be a guess at it."""
        with pytest.raises(ProvenanceError, match="older than"):
            migrate({"artifact_version": OLDEST - 1})

    def test_a_current_artifact_passes_through_unchanged(self):
        raw = {"artifact_version": ARTIFACT_VERSION, "scores": []}

        assert migrate(dict(raw)) == raw

    def test_a_chain_with_a_hole_names_the_step_it_lacks(self, monkeypatch):
        """The failure ``missing_steps`` exists to find before an operator does."""
        monkeypatch.setattr("evals.harness.migrate.STEPS", {})
        monkeypatch.setattr("evals.harness.migrate.ARTIFACT_VERSION", OLDEST + 1)

        with pytest.raises(
            ProvenanceError, match=f"no step lifts artifact_version {OLDEST}"
        ):
            migrate({"artifact_version": OLDEST})

    def test_a_file_that_is_not_json_is_refused_by_name(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ProvenanceError, match="invalid JSON"):
            migrate_file(path, tmp_path / "out.json")

    def test_a_json_document_that_is_not_an_object_is_refused(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")

        with pytest.raises(ProvenanceError, match="not an eval artifact"):
            migrate_file(path, tmp_path / "out.json")


class TestTheCommandRefusesToWriteIntoASealedBaseline:
    def test_an_out_path_inside_the_baselines_directory_is_refused(
        self,
        tmp_path,
        sampling,  # noqa: F811
        capsys,
    ):
        """A Baseline's files are digest-sealed; a rewrite there fails the
        repo-wide verify and stales the published table."""
        from argparse import Namespace

        from evals.harness import baseline, run

        path = older(tmp_path, "old.json", provenance(sampling))
        target = baseline.BASELINES_DIR / "somewhere" / "old.json"

        code = run.command_migrate(Namespace(artifact=[str(path)], out=str(target)))

        assert code == 1
        assert "digest-sealed" in capsys.readouterr().err
        assert not target.exists()

    def test_a_lift_that_still_refuses_is_reported_at_the_lift(
        self,
        tmp_path,
        sampling,  # noqa: F811
        capsys,
    ):
        """The copy goes back through the loader, so a lift that produced
        something unreadable says so here rather than at the next instrument."""
        from argparse import Namespace

        from evals.harness import run

        path = older(tmp_path, "old.json", provenance(sampling))
        raw = json.loads(path.read_text(encoding="utf-8"))
        del raw["provenance"]
        path.write_text(json.dumps(raw), encoding="utf-8")

        code = run.command_migrate(
            Namespace(artifact=[str(path)], out=str(tmp_path / "out.json"))
        )

        assert code == 1
        assert "no provenance block" in capsys.readouterr().err
        # The half-lifted copy is not left behind to be read as a good one.
        assert not (tmp_path / "out.json").exists()


def test_one_file_a_lift_cannot_help_does_not_stop_the_others(
    tmp_path,
    sampling,  # noqa: F811
    capsys,
):
    """Found by pointing the command at the real archive.

    ``evals/runs/`` holds a sweep from before execution identities were
    recorded, which the loader refuses for a reason no rename repairs. Stopping
    at the first would leave every file behind it unlifted for the sake of one.
    """
    from argparse import Namespace

    from evals.harness import run

    good = older(tmp_path, "good.json", provenance(sampling))
    bad = older(tmp_path, "bad.json", provenance(sampling))
    raw = json.loads(bad.read_text(encoding="utf-8"))
    del raw["provenance"]["identity_version"]
    bad.write_text(json.dumps(raw), encoding="utf-8")
    out = tmp_path / "lifted"

    code = run.command_migrate(Namespace(artifact=[str(bad), str(good)], out=str(out)))

    assert code == 1  # something refused, and the exit code says so
    assert (out / "good.json").is_file()  # and the rest still landed
    assert not (out / "bad.json").exists()
    assert "1 artifact(s) lifted" in capsys.readouterr().out


def test_the_repeat_archive_is_what_this_was_written_for(tmp_path):
    """A real file, if this machine has one. The archive is gitignored, so this
    skips elsewhere rather than pinning a path nobody else has."""
    archive = Path("evals/runs")
    # The archive also holds files that are not artifacts, such as a batch's
    # request list, so a JSON value that is not an object is not a candidate.
    documents = (
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(archive.rglob("*.json"))
    )
    older_files = [
        path
        for path, data in documents
        if isinstance(data, dict) and data.get("artifact_version") == 6
    ][:1]
    if not older_files:
        pytest.skip("no version 6 artifact on this machine")
    out = tmp_path / "lifted.json"

    assert migrate_file(older_files[0], out) == 6
    assert json.loads(out.read_text())["artifact_version"] == ARTIFACT_VERSION
