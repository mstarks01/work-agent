# The review ledger

`votes.jsonl` is the append-only record of what a **person** decided about a
finding. It is the only human judgement this repository holds, and it is
checked in on purpose: it is the evidence behind every quality number the tool
publishes, so it belongs in version control where each change is a reviewed
diff with an author and a date.

One JSON object per line. Never edit a line, and never delete one — a reviewer
who changes their mind appends a new event, and the latest event for a
`(fingerprint, voter)` pair is the live verdict. That is what makes any past
state of the ledger reconstructible, and it is what lets a number computed last
month be recomputed to the same digit today.

Write to it through `webapp/review.py`, which validates every field against a
closed set before the line is written. `evals/harness/ledger.py` is the reader.

The file does not exist until the first sitting. That is a starting state and
not a fault; the loader returns an empty ledger.
