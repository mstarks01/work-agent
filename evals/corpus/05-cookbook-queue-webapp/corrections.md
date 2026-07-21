# Bootstrap → blessed corrections: 05-cookbook-queue-webapp

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:web-application-to-browser:serve-response` | present | removed | The source diagram draws request and response as two arrows. One interaction is one flow, direction = who initiates; the response rides implicitly. The bootstrap transcribed the picture rather than applying the rule. |
| 2 | `flow:background-worker-to-message-queue:consume-job` | source `store:message-queue`, destination `process:background-worker` | reversed | Same direction error as case 02's correction 5, and from the same cause: the diagram's arrow points the way the data travels, not the way the interaction is initiated. Two independent occurrences make this a pattern worth a metric. |
| 3 | `store:message-queue` | typed as `Process` | `DataStore` | A queue is data at rest between two processes; the glossary lists queues-at-rest explicitly. The bootstrap typed it by the fact that it moves things. |
| 4 | `store:database.description` | "application records" | includes the log records | The source states the database also holds the application's log records. Dropping it deletes the model's strongest repudiation finding, since the whole threat is that the audited actor writes the audit store. |
| 5 | `process:web-application.technology` | `web server` | `unknown` | The source names no technology. A plausible generic value is still an invented one. |
| 6 | `store:web-application-config.assets`, `store:worker-config.assets` | `[]` | `["credentials", "secrets"]` | Both are stated to hold the credentials their process uses. The bootstrap tagged neither, because neither is named as a credential store. |

## Signal

Corrections 1–3 are all **transcribing the diagram instead of applying the
model's rules** — a failure mode specific to converted-diagram inputs, and one
to watch as the corpus takes on more cookbook material. Correction 4 repeats the
now-familiar shape from cases 02 and 03: a fact stated in the source survives
into `source_excerpt` but not into any attribute or description an analyst
reads. Across five cases that is the single most repeated extraction failure.
