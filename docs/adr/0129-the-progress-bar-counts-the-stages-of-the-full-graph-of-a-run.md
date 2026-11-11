# 129. The progress bar counts the stages of the full graph of a run

- **Status**: accepted
- **Date**: 2026-11-11
- **Relates to**: [#2243](https://github.com/mstarks01/work-agent/issues/2243);
  [ADR 0125](0125-a-control-fills-only-an-open-fact.md), whose continue at the
  pause splits one run into two jobs

## Context

The progress table gave each step a stage number for each mode by hand. The
numbers repeated the shape of the graph: where a run starts, whether the
graph builds the assertion pass, and whether it ends at a catalog or at a
pause. One number fell out of step with the graph (#2241). A deployment with
the assertion pass off showed a bar that went from its second stage to its
last, because the stage count was a constant. The page scripts also chose
the first words of a run, so the service and the page were two writers of one
line.

## Decision

**Each step names the Progress Stage it completes.** The stages, in order, are
`map`, `details`, `checks`, `questions`, `findings` and `report`. A refused
description, the preparation of the lanes and each lane complete no stage.
The number of a stage is its place among the stages of the run, and the bar
shows the highest number that a completed step reached.

**One stage order serves both modes.** A run counts only the stages that its
graph holds. A run that asks has `map`, `details`, `checks` and `questions`.
A run to the report has `map`, `details`, `checks`, `findings` and `report`.
Each run without the assertion pass has no `checks` stage.

**The full graph of the mode sets the stages, not the graph that one job
runs.** A run that asks counts the graph of the `head-only` entry, and each
other run counts the graph of the `extract` entry. A Resumed Job counts the
stages of a run to the report, and its bar starts at the stage where its
paused parent stopped. A pause for the Controls alone completes no stage of a
run to the report, so the owner sees one analysis across the two jobs.

**The job stores whether its analysis runs the assertion pass.** A job that
extracts its model takes the deployment's setting at admission. A Resumed Job
copies it from its parent, though the graph of a Resumed Job holds no pass. A
change on the Settings page does not change the count of a job in flight.

**The service writes every line.** Each line carries the count of complete
stages, the count of stages of the run and the words of the work that
follows. The page shows no words until the first line arrives.

## Consequences

- A run to the report with the assertion pass on has five stages, not four.
- A new step, or an entry that a deployment turns off, changes the bar with no
  edit to a table of numbers. A test holds the steps that each entry builds to
  each built graph.
- The status line is empty from the submit until the stream connects.
