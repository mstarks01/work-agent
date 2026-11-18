# Regression matrix: pipeline failures and the tests that hold them

This matrix lists each pipeline failure that a test holds. For each one it gives the
failure, where the evidence comes from, the contract, the production seam, the
expected outcome and the test. Read it before you add a regression test. Add
a row when you add one.

Every test here runs without credentials and calls no model. A fixture test
runs in CI. An archive replay reads `evals/runs/`, which is not in git, so it
runs only as an offline command.

## The rows

| Failure | Provenance | Contract | Production seam | Expected outcome | Test |
|---|---|---|---|---|---|
| A proposal entry fails its schema | Scripted fixture | The batch drops the entry with the schema error; the valid entry beside it stays | `ProposalBatch`, `evidence.invalid_proposal_marks`, `fan_in.fan_in` | One `dropped_claims` mark that starts "fails the proposal schema"; the neighbour is ruled on | `test_regression_matrix.py::test_an_invalid_proposal_costs_only_itself` (each package); `test_evals_fan_in_accounting.py::test_an_invalid_proposal_is_dropped_and_its_neighbour_kept` |
| A draft names an element the model lacks | Scripted fixture | An element the model lacks costs only that reference; a draft left with none is dropped with the reason | `fan_in.join_drafts` | A `dropped_claims` mark that names the element | `test_fan_in.py::TestJoinDrafts`; `test_pipeline.py::test_a_hallucinated_element_reference_costs_the_claim_not_the_job` |
| A quote names a source the job lacks | Scripted fixture | A lone quote on an unknown label drops its draft; a quote beside a good ground is marked, not fatal | `fan_in._verify_quotes` | A drop whose reason names the label; the neighbour stays | `test_fan_in.py::TestQuoteVerification`; `test_evals_fan_in_accounting.py::test_a_lone_quote_on_a_source_the_job_lacks_drops_its_draft_only` |
| Two drafts share one ID | Scripted fixture | The fan-in keeps the first draft under each ID and drops the rest with the reason | `fan_in._drop_duplicate_ids` | One kept, one dropped, nothing unexplained | `test_fan_in.py::TestJoinDrafts::test_a_duplicate_id_keeps_the_first_draft_and_drops_the_rest`; `test_evals_fan_in_accounting.py::test_a_repeated_id_is_one_draft_and_one_drop` |
| The critic's first pass leaves a draft unruled | Scripted fixture | The bounded re-ask runs once, and the report records that the first pass did not reconcile | `critic_round.CriticRound`, the graph's review router | Completed run; the claim is ruled; `unreconciled_rulings` is not empty | `test_regression_matrix.py::test_a_first_pass_that_drops_the_draft_is_repaired_by_the_re_ask` (each package); `test_pipeline.py::test_a_malformed_critic_output_is_re_asked_once_and_then_assembled` |
| The re-ask changes a ruling no problem named | Scripted fixture | The first-pass ruling stands, and an `unbriefed-change` mark records the change | `critic_round.CriticRound` second look | The first ruling is kept; one `unbriefed-change` mark | `test_regression_matrix.py::test_a_re_ask_keeps_the_first_pass_ruling_it_was_not_asked_to_change` (each package); `test_critic_round.py::TestTheSecondLook` |
| The re-ask still fails | Scripted fixture | The second failure ends the job and names the draft | The graph's `critic_failed` node | The run raises, and the message names the draft | `test_regression_matrix.py::test_a_re_ask_that_still_drops_the_draft_fails_the_job` (each package); `test_pipeline.py::test_a_critic_that_will_not_reconcile_after_the_re_ask_fails_the_job_loudly` |
| A claim sits in two lists of its block, or is returned twice | Reproduced on a production-built block | One claim ID sits in one list, once | `FrameworkAnalysis._identity_issues` | The report fails to load | `test_report.py::TestReportInvariants::test_a_claim_both_kept_and_returned_is_rejected`; `test_report.py::TestReportInvariants::test_a_claim_returned_twice_is_rejected` |
| A ruled draft is missing from the report | Scripted archive | Every draft that reached assembly is in one of the block's three lists | `evals/harness/assembly.py` replay of `CriticRound.outcome` and `returned_to_deferred` | The draft is named as omitted | `test_evals_assembly.py::test_a_ruled_draft_the_report_lacks_is_an_omission`; `test_evals_assembly.py::test_a_changed_claim_id_cannot_hide_its_draft` |
| Assembly files a claim in the wrong list, at equal counts | Scripted archive | Each claim is filed where its ruling puts it | The assembly replay | Both swapped claims are named | `test_evals_assembly.py::test_an_equal_count_swap_is_two_claims_filed_differently` |
| A claim returned to the deferred set | Built by the production `assemble_report` | The replay accounts for it, and the critic replay reads it as `returned` | `returned_to_deferred`; `critic_yield.fate_by_claim` | Accounted, not omitted | `test_evals_assembly.py::test_a_returned_claim_is_accounted_and_its_neighbour_kept`; `test_evals_critic_replay.py::test_a_claim_assembly_returned_reads_as_returned` |
| A reported severity is not its draft's | Scripted archive | No ruling replaces a severity, for a run whose code descends from `CRITIC_RATES_NO_SEVERITY` | The assembly replay's severity oracle | The claim is named; a run that does not descend is incompatible; a shallow clone gives no oracle | `test_evals_assembly.py::test_a_reported_severity_its_draft_does_not_hold_is_named`; `test_evals_assembly.py::test_a_run_with_no_severity_oracle_compares_none`; `test_evals_assembly.py::test_a_shallow_clone_cannot_place_a_run` |
| A summary miscounts its block | Scripted archive | The block's own check owns the summary | `FrameworkAnalysis._summary_issues` | The report fails to load and is named as unreadable | `test_evals_assembly.py::test_a_summary_that_miscounts_its_block_fails_the_load` |
| A finished case lacks a capture | Scripted archive | The case is named, and the other cases replay | `bundle.finished_file` | A capture fault; the command exits 1 | `test_evals_assembly.py::test_a_finished_case_missing_a_capture_is_named_and_the_rest_replay`; `test_evals_fan_in_accounting.py::test_a_missing_capture_is_named_and_fails_the_command` |
| The fan-in output loses a proposal | Scripted fixture | Each proposal has one fate: kept, dropped, unknown identity or deferred | `fan_in.fan_in`, read by `fan_in_accounting.account` | The proposal is named as unexplained | `test_evals_fan_in_accounting.py::test_a_proposal_the_output_loses_is_unexplained`; `test_evals_fan_in_accounting.py::test_a_second_copy_of_a_kept_id_is_extra_and_its_proposal_accounted`; `test_regression_matrix.py::test_a_proposal_the_fan_in_drops_silently_is_unexplained` |

## Questions with no oracle

These are semantic questions. No label or deterministic rule answers them, so
each one stays unverified. A passing test here does not answer them.

- **Unverified: whether a critic's verdict is right.** The tests check that a
  ruling reaches its list, not that the ruling is correct. #2270 audits the
  critic.
- **Unverified: whether a drop reason is the right reason.** The tests check
  that every drop has a recorded reason, not that the reason is true.
- **Unverified: whether a swap that moves its verdict is wrong.** The report is the only
  record of the critic's rulings. A claim whose verdict moved with it reads as
  the critic's ruling.
- **Unverified: whether a kept claim on a unit a Control row kept should have been
  returned.** The report holds only the units that assembly sent back.

## How to run it

- CI runs `tests/test_regression_matrix.py` and the other tests in the table
  in the offline suite.
- The archive replays are offline commands. Set `ANALYSIS_OFFLINE=1`; neither
  calls a model:
  - `uv run python -m evals.harness.run assembly <artifact>`
  - `uv run python -m evals.harness.run fan-in <artifact>`
