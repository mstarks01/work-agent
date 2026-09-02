"""The standalone report verifier: ``python -m analysis_service.verify_report``.

It reads JSON and never a model. The verifier loads the report as a plain
mapping and canonicalizes it as bytes, so verification never depends on this
project's pydantic schema. That is most of what "operates independently of the
producing service" means. A report from a build whose schema has since moved
still verifies, and somebody can write a verifier in another language from
:mod:`analysis_service.attestation`'s docstring alone.

The exit code is the verdict, and there are six of them. Zero means the origin
is established. Every other state gets its own non-zero code, so a script can
tell an unsigned report from a revoked one without parsing prose. Collapsing
them into 0 and 1 would make "we do not sign" and "this key was compromised" the
same event to anything automated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analysis_service.attestation import (
    Attestation,
    DuplicateKeyError,
    KeyringError,
    Verdict,
    Verification,
    load_keyring,
    load_report,
    verify,
)

#: Verdict -> process exit code. A table because a script keys on it, and
#: because a missing entry raises here rather than silently returning success.
EXIT_CODES: dict[Verdict, int] = {
    "verified": 0,
    "unsigned": 10,
    "unknown-key": 11,
    "unsupported": 12,
    "revoked": 13,
    "invalid": 14,
}


def render(result: Verification) -> str:
    """One line a person reads, and the caveat that must ride with a pass.

    The caveat is on the **passing** path specifically. A failure is already
    read as a failure; a pass is the one a reader is tempted to over-read, and
    a verifier that printed a bare "OK" would be inviting exactly that.
    """
    head = f"{result.verdict}: {result.detail}"
    if result.verdict != "verified":
        return head
    return (
        f"{head}\n"
        "This is authenticity, not correctness. It does not say the findings are"
        " right, and it does not say the run was certified."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analysis_service.verify_report", description=__doc__
    )
    parser.add_argument("report", type=Path, help="the report JSON")
    parser.add_argument("--keyring", type=Path, required=True)
    parser.add_argument(
        "--attestation",
        type=Path,
        help="the detached signature. Omit it to be told the report is unsigned"
        " -- which is a distinct verdict, never a pass.",
    )
    args = parser.parse_args(argv)

    try:
        keyring = load_keyring(args.keyring)
    except KeyringError as error:
        print(f"keyring: {error}", file=sys.stderr)
        return 2

    try:
        report = load_report(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, DuplicateKeyError) as error:
        print(f"report: {error}", file=sys.stderr)
        return 2

    attestation = None
    if args.attestation is not None:
        try:
            attestation = Attestation.model_validate_json(
                args.attestation.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            print(f"attestation: {error}", file=sys.stderr)
            return 2

    result = verify(report, attestation, keyring)
    print(render(result))
    return EXIT_CODES[result.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
