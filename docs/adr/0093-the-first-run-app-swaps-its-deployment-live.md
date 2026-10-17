# 93. The first-run app swaps its Deployment live

- **Status**: accepted
- **Date**: 2026-10-16
- **Relates to**: map [#1760](https://github.com/mstarks01/work-agent/issues/1760),
  ticket [#1765](https://github.com/mstarks01/work-agent/issues/1765),
  [ADR 0092](0092-the-settings-directory-and-its-strict-env-file.md)

## Context

A save on the Settings page of the first-run app applies without a
restart. The owner chose this over a restart. Before this decision, each
process built one **Deployment** at startup and kept it until it stopped. The
routes of the first-run app read that Deployment through two objects, and one
request reads them more than one time, across `await` points. So a change
that replaces them field by field can let one request check a selection under
one Deployment and admit the job under another.

## Decision

**One cell.** The first-run app holds one cell. The cell holds one frozen
object: a `JobAnswers` on the running store, the job deadline, and the view
that the pages show (the carried frameworks and the tiers). The `JobAnswers`
holds the Deployment's runner, bounds and certification gate; the cell holds
no `Deployment` field. A save builds the new object completely, then replaces the
cell in one assignment. Each route reads the cell one time, at its start, and
uses only that value. The `Workspace` keeps the store, the tasks and the job
cap, and it is not in the cell.

**A job in flight.** A job keeps the runner and the deadline that it got at
admission, so it runs to its end on the Deployment that admitted it. The old
Deployment stays in memory until no task holds its runner.

**The check is the build.** The test Deployment that the check before a save
builds, and the runner that the check builds from it, become the new cell.
The app does not build them a second time.

**A Resumed Job.** `Deployment.selection` raises a typed `ConfigError`
subclass when a selection names a framework that the Deployment does not
carry. The subclass carries the text of the `SelectionError`. `JobAnswers.admit`
passes it through, and the first-run app and `/v1` map it to a 409 that names
the framework. `resolve_names` stays the one reader of the rule. A save of
answers, a draft and a correction start no job, so they stay allowed. The
page gives no warning when a save removes a framework that a paused job
selected, because a later save can add the framework again.

**Two saves.** An `asyncio.Lock` holds one save from validation to the swap.
The page loads with a revision, the SHA-256 of the bytes of `settings.env`,
and a save sends it. When the revision does not match the file, the server
refuses the save with a 409 and tells the user to reload the page. This also
refuses a save after a hand edit of `settings.env`. The app does not watch
the file, so a hand edit applies at the next restart.

**Job storage.** The app always builds the store and the `Workspace` at
startup from the merged mapping, before the Deployment and without it.
`store_lifespan` always runs, and it reads the retention setting from the
merged mapping. A store setting that is not usable stops the startup. When
the Deployment does not build, only the cell is empty, and the first save
that passes fills it without a restart. A swap keeps the running store. The
check before a save still runs `build_store` on the new mapping. When the
store keys of the running mapping and the saved mapping differ, the page
names those keys and tells the user to restart the app.

**The gate.** The report route judges each report with the gate of the
current cell. A save that changes `require_certified` or a tier therefore
changes whether an older report is served, as a restart does.

**The `/v1` service.** It keeps one Deployment for each process, and the page
does not change it.

## Considered options

- **A restart for each save.** The owner ruled for a live swap.
- **Mutable fields on the present objects.** One request could then read two
  Deployments.
- **A second check of the selection in `JobAnswers`.** It would be a second
  reader of the name rule.
- **The last save wins.** It loses a change from another tab without a word.
- **A store that the first save builds late.** It needs a second path for the
  store startup.
- **A gate for each job.** It needs a gate stored with each job, and a
  restart does not keep one today.
