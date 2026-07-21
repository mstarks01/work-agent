# Bootstrap → blessed corrections: 03-batch-data-pipeline

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `entity:insurance-partner` | three separate entities (`entity:partner-a`, `-b`, `-c`) | one entity | The text names three partners but describes no distinguishing fact about any of them. Inventing names the source never used breaks the rule that nothing invented gets a type; the extract prompt's own guidance is to prefer one element and note the ambiguity. |
| 2 | `store:landing-bucket.encryption_at_rest` | `Google-managed keys` | `unknown` | The source says "I don't know how the landing bucket is encrypted … Both are on defaults as far as I know." A hedged guess by the author is not a stated control. |
| 3 | `store:airflow-metadata-db` | (absent) | present, with its flow | The bootstrap treated Airflow's metadata database as infrastructure rather than a data store, dropping the element that concentrates every credential in the system. |
| 4 | `flow:ingest-scheduler-to-landing-bucket:list-and-read-files.description` | "scheduler reads landed files" | includes the unchecked-depositor fact | The single most load-bearing sentence in the source — "It does not check that a file came from the partner whose folder it landed in" — survived only as a `source_excerpt`, not in any attribute an analyst reads. |
| 5 | `flow:data-analyst-to-claims-warehouse:run-queries.authentication` | `company SSO` | `company SSO; dataset-wide grant with no column-level restriction` | Authentication was recorded, authorization scope dropped. The stated weakness is the grant, not the sign-in. |
| 6 | `store:claims-warehouse.assets` | `["pii"]` | `["pii", "health", "business-critical-data"]` | Insurance claim records are health data; the bootstrap used the generic tag and stopped. |

## Signal

Two new failure shapes on top of the pattern from cases 01–02. **Invented
cardinality** (1): the bootstrap manufactured three named entities from a count.
And **infrastructure blindness** (3): components that exist to run the pipeline
rather than to carry its payload get dropped, even when they hold the
credentials. Correction 4 is the same "detail lost between excerpt and
attribute" failure as case 02's correction 2, now on a stated *absence* rather
than a stated control — worth watching as a distinct extraction metric, since a
dropped absence reads to an analyst as an unremarkable element.
