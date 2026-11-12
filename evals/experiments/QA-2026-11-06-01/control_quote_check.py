"""Send three control rejections through the critic's review seam and the
report's quote reader, with the same Control and three reasons.

The reasons quote a line the evidence holds, a sentence the evidence does not
hold, and nothing. Uses the production CriticRound.first() and the reader
controls_section calls for each ruling's quoted lines.
"""

from analysis_service.critic_round import Accepted
from analysis_service.job_controls import _quoted
from tests.test_critic_controls import DB_CONTROL, _db, _round, _row, _ruling

REASONS = {
    "a true quote": "rds.tf states `storage_encrypted = true` for the orders database.",
    "an invented quote": (
        "rds.tf states “Every column is encrypted with a customer-managed KMS key.”"
    ),
    "no quote": "The owner's Control covers it.",
}


def main() -> None:
    control = _db(_row("V11"))
    for label, reason in REASONS.items():
        ruling = _ruling(
            rejected_because="control",
            control_row={"control": DB_CONTROL, "unit": "V11"},
            reason=reason,
        )
        outcome = _round(control).first([ruling])
        accepted = isinstance(outcome, Accepted)
        lines = [line.text for line in _quoted(reason, control.evidence)]
        print(f"{label:18} seam accepts: {accepted!s:5}  report quotes: {lines}")


if __name__ == "__main__":
    main()
