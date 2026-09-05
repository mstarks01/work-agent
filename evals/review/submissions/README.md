# Review submissions

Each JSON file in this directory is one contributed human review. It is the canonical contribution artifact for the review UI and the standalone review page.

A submission contains the reviewer's independent list, marks on recorded findings, missed issues, notes, and SHA-256 digests of the case material they reviewed. Contribution CI binds `submitted_by` to the GitHub account that opened the pull request and validates the recorded case IDs, finding keys, and digests before merge.

A later edit to a corpus case does not rewrite an old review. The digest mismatch simply means that review no longer clears the changed case; a new review records the new bytes. Corrections likewise arrive as a new JSON file rather than editing historical evidence.

New review pull requests should add **one JSON file and nothing else**. The review UI's **Show files** action displays the exact path and contents before contribution.
