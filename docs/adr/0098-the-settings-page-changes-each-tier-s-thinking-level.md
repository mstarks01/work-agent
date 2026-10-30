# 98. The Settings page changes each tier's thinking level

- **Status**: accepted
- **Date**: 2026-10-20
- **Related**: map #1760, whose ruling this changes for one field;
  [ADR 0091](0091-the-settings-page-reads-the-loaders-own-rules.md)
- **Evidence**: `tests/test_settings_page.py`
  (`test_each_tier_thinking_level_changes_with_a_note`),
  `tests/test_settings_save.py`
  (`test_a_thinking_level_writes_its_variable_and_a_reset_removes_it`),
  and the measurements in issue #1863

## Context

Map #1760 ruled that the Settings page shows the sampling settings and does
not change them, because a change makes each run uncertified until a sweep
measures it. So the person who starts the app could set a tier's thinking level only with
`ANALYSIS_SAMPLING_<TIER>_THINKING` in the command that starts the app.

The thinking level decides whether a job finishes and what it finds. On
2026-10-20, Sonnet 5 at its default level wrote STRIDE lanes of up to 341 s
and a critic of 430 s, so the 300 s request timeout failed the job. In a lane
replay of case 01, Sonnet 5 found fewer must-finds at `medium` than at `high`
in each of three passes. The reason for the ruling also protects nothing on a
deployment whose model has no blessed fingerprint, because each of its runs is
already uncertified.

## Decision

The page changes `sampling.<tier>.thinking` for each tier. The control offers
the values of `sampling.ReasoningEffort`, and a reset removes the line, so the
tier returns to its model's own preset. The row states that a change makes each
run uncertified until a sweep measures it, in the new `Setting.note` field.

The other sampling fields and review independence stay shown only.

## Consequences

The person who starts the app can tune the level in the first-run app, and the save checks it with
the real loaders as it checks each other setting. A run that the change makes
uncertified has the **Certification** `uncertified`; nothing in the first-run app refuses
an uncertified run.
