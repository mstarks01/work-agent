# What a job needs to continue after a restart

Ticket #1526, map #1522. Probed on 2026-10-06 against repo `289efea3`,
`google-adk==2.5.0` and the run archive under `evals/runs/`. The probe is
`probe_job_restart_state.py` beside this file. It calls no model.

## Answer in short

- Every part of a `JobRecord` goes to JSON and back without loss. This
  includes the checkpoint, the resumption, the report and the certification.
- A **paused** job (`awaiting-answers`) needs nothing from ADK. Its checkpoint
  carries the model and the catalog, and the run's ADK session is gone when the
  pause starts.
- A **running** job cannot continue from where it stopped. The partial work is
  only in the ADK session, which lives in process memory. The record holds
  enough to start the run again from its entry, at the cost of a second run.
- Seven places rely on a copy from the store, and three methods rely on a body
  with no `await`.
- A typical record is about 86 KB. The report is 95% of it.

## 1. Does each field go to JSON and back without loss?

Yes. The probe took all 202 archived `*.report.json` files. Today's `Report`
schema accepts all of them. For each report it built two records:

- a completed job, with sources from the corpus case, one event per node, the
  report and a `CertifyResult` with an uncertified node and an unexercised tier;
- a resumed job, with a `Resumption`, a `Checkpoint` (the report's
  `SystemModel` and `AssertionRecord`), a `shown_early` key and a
  `skipped_early` list that mixes a key and a string.

Each record went through `model_dump_json` and `model_validate_json`. All 404
records came back equal, and the second dump gave the same bytes as the first.

The parts, and why each one holds:

| Part | Type | Note |
| --- | --- | --- |
| `JobRecord`, `JobEvent` | pydantic (`jobs.py:113`, `jobs.py:174`) | `datetime` fields are in UTC and dump as ISO 8601. |
| `Checkpoint`, `Resumption` | frozen pydantic (`jobs.py:137`, `jobs.py:152`) | They hold only pydantic types. |
| `SystemModel` | pydantic | It is inside the report and the checkpoint. |
| `AssertionRecord` | pydantic (`assertions.py:883`) | The dataclasses in `assertions.py` are not fields of the record. |
| `CertifyResult` | pydantic (`certification.py:125`) | Its `tuple` fields dump as arrays and validate back to tuples. |
| `Report` | pydantic (`report.py:869`) | `analyses` uses `SerializeAsAny` and a dispatch validator, so a framework block comes back as its own type. `NodeRun.served_trust` is a computed field that the validator drops on input and computes again (`report.py:416-440`). |
| `UnknownKey`, `SkipKey` | `tuple[str, ...]`, or a tuple or a string | A JSON array validates back to a tuple. |

No field of the record is a dataclass. The dataclasses in `jobs.py`
(`Admission`, `PipelineCompleted`, `PipelineRejected`, `PipelineAwaiting`) are
return values, and the store never holds them.

A durable store must keep the JSON as text, or as a JSON type that keeps key
order. The test of the round trip is byte-equal output, and an attestation
signs exact bytes (#1523, point 6).

## 2. What ADK `InMemorySessionService` holds, and whether a resumed job needs it

**What it holds.** `GraphExecutor` builds the session service and the ADK
`Runner` (`execution.py:304-308`). The `App` has no `resumability_config`
(`execution.py:306`). One run makes one session (`execution.py:359`), seeded
with:

- the rendered sources (`STATE_INPUT_TEXT`) and the sources by label
  (`STATE_SOURCE_TEXTS`), at `execution.py:341-357`;
- the framework options and the answers, at `pipeline.py:157-163`;
- for a resumed job, the parent's model (`STATE_VALID_MODEL`) and catalog
  (`STATE_ASSERTION_CATALOG`), at `pipeline.py:258-271`.

During the run, the service keeps every non-partial event in
`storage_session.events` and merges each `state_delta` into the session state
(`in_memory_session_service.py:322-371`). So the state grows to hold each
node's output: the extracted model, the valid model, the catalog, the lane
drafts and the analysis. The graph writes no `app:` or `user:` key, so the
service-wide `app_state` and `user_state` maps stay empty
(`graph.py:758-852`).

**After the run.** The executor reads the final state, then deletes the
session in a `finally` block (`execution.py:399-415`). This happens on success,
on failure and on cancellation. So after a pause the session no longer exists.

**Paused job.** The pause reads the final state into a `Checkpoint`
(`pipeline.py:274-297`), and `execute_job` writes it to the record
(`jobs.py:1033-1038`). The answers route copies it into a `Resumption` on a new
job (`api.py:1103-1119`). The resumed run seeds its state from that
resumption alone (`pipeline.py:258-271`). **The checkpoint carries everything.
A resumed job needs nothing from the session.**

**Running job.** A job in `running` holds no checkpoint. Its progress is in the
session state, and the session is in process memory. A restart loses it. No
code reads the session back, and ADK resumability is off. So a running job
cannot continue from its last node.

The record does hold every input to start the run again: `sources`,
`frameworks`, `links`, `facts`, `resumption` and `ask_questions`. `entry_of`
picks the same graph from these fields (`pipeline.py:224-233`). A restart can
therefore run the job again from its entry. That run pays again for every node.

Two facts limit a run again today:

- Nothing starts one. `_admit_and_start` hands the job to FastAPI
  `BackgroundTasks` (`api.py:481-486`). There is no startup scan for jobs in
  `queued` or `running`, and `execute_job` only moves a job from `queued`
  (`jobs.py:71-78`, `jobs.py:992`).
- The token charge. A job's `reserved_tokens` stays until a terminal status
  settles it (`jobs.py:311-348`). A second run spends again under the same
  reservation. The node runs of the first attempt are lost with the process, so
  the measured figure covers only the second attempt.

## 3. Where the code relies on a copy from the store, or on no `await`

**A copy on read.** The caller changes the object it gets, and the change must
not reach the store until `save`:

1. `execute_job` takes the record from `get` (`jobs.py:987`), then calls
   `transition` and `record_node` on it in place (`jobs.py:992-1046`). The same
   object goes to the runner as `job` (`jobs.py:1002`).
2. `_answerable` assigns `record.resumption` on the envelope from `owned`
   (`api.py:542`). With no copy, this writes the resumption into the stored
   record outside any save.
3. `owned` blanks `report`, `checkpoint` and `resumption` on its result
   (`jobs.py:648-651`). This is safe only because the result is a copy.

**A copy on write.** The caller keeps the object after it hands it over:

4. `reserve` stores a copy (`jobs.py:584`). The API keeps `record` and reads
   `record.status` for the response (`api.py:487-488`).
5. `save` stores a copy (`jobs.py:731`). `execute_job` keeps changing `record`
   after each `save`, from `on_node` (`jobs.py:995-997`) to the terminal save.

**A copy that the store chose not to make.** These return fresh structures, so
the caller may keep them:

6. `report_json` returns `model_dump(mode="json")` (`jobs.py:668`).
7. `report`, `checkpoint`, `resumption` and `events_after` each return a deep
   copy of one part (`jobs.py:682`, `jobs.py:696`, `jobs.py:710`,
   `jobs.py:724-726`).

A store that serialises to JSON gives a fresh object on each read. It meets 1
to 7 with no extra work.

**No `await` between the check and the write.** asyncio switches tasks only at
an `await`, so each of these is atomic in one process:

- `reserve` (`jobs.py:506-590`). It checks the duplicate ID, the
  `resumed_already` rule through `_resumed_by`, the ceiling, the rate and both
  token budgets, then inserts. Its docstring states the reliance
  (`jobs.py:511-516`).
- `save_round` (`jobs.py:733-769`). It checks the owner, the status, the round
  revision and `_resumed_by`, then increments `round_revision` and writes the
  answers in place on the stored record.
- `save_corrections` (`jobs.py:771-787`). It checks the owner, the status and
  the follow-up flag, then writes in place.

A durable backend needs each of these three as one transaction or one
conditional write. `save_round` and `reserve` also read other records through
`_resumed_by`, so `save_round` and the admission of a resumed job must
serialise on the parent ID.

## 4. Size of each part of a typical record

UTF-8 bytes of the JSON, over the 202 archived reports. The record is the
completed job that the probe built.

| Part | Median | Min | Max |
| --- | ---: | ---: | ---: |
| Sources | 1,486 | 867 | 3,457 |
| Report | 81,917 | 50,079 | 256,543 |
| Checkpoint | 9,768 | 6,520 | 23,646 |
| Events | 1,507 | 1,411 | 2,964 |
| Whole record | 85,752 | 55,891 | 262,133 |

- **Events.** A job has a median of 15 events (minimum 14, maximum 28), at
  about 100 bytes each.
- **Checkpoint.** It holds the model and the catalog. Only 6 of the 202
  reports carry a catalog. With a catalog the median is 21,868 bytes (17,115 to
  23,646). Without one it is 9,755 bytes. A paused job carries the checkpoint
  once on its own record and once in the resumed job's `resumption`.
- **Sources.** The corpus sources are short. The deployment limit is
  `max_source_bytes = 102400` (`config/resilience.toml:133`), so a real job's
  sources can be up to 100 KiB. A resumed job adds an answers source.
- **Report.** It is most of the record. A report in the archive comes from an
  eval sweep. It has the same schema as a report from the service.
