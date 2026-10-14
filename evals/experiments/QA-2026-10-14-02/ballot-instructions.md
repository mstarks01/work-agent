# Capability reader gate ballot: instructions

This is the promotion gate that ADR 0086 declared before the run. The
capability reader ran once, as it ships, on each of the two holdout cases. It
had never seen them, and this run used their last read. These 64 rows are
every statement it kept.

**The gate:** the reader's default turns on only if at least 95% of the rows
you rule `right` or `wrong` are `right`.

The columns, the verdicts and the reasons are the same as in ballot 3:

- `verdict`: `right`, `wrong` or `unsure`.
- `reason`, for a `wrong` row only: `not-stated`, `third-party`,
  `too-specific`, `injected` or `other` (say what in `note`).
- `note`: optional.

Do not change the text in the other columns. `descriptions.md` has the full
text of both holdout descriptions. Send the CSV back in this conversation.
