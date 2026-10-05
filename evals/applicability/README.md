# Applicability fixtures

Each file is one labelled situation for the applicability rule (ADR 0051). It
states what the sources say about some capabilities, what a submitter answered,
and which requirements must then come out `applicable`, `not-applicable` or
`unknown`. A file with `truth` also drives the question simulation: the paused
job's capability questions are answered from it in order.

Run the benchmark with:

```sh
python -m evals.harness.run rule-applicability
```

It calls no model. It reports signed and unsigned fixtures apart, and the
false-exclusion count is the safety figure.

`drafted_by` names who wrote the labels and `signed_by` who reviewed them. An
agent drafted every file here, and the maintainer signed each one. A new file
carries `signed_by: null` until the maintainer reviews it. `tests/test_evals_rule_applicability.py` refuses a file
signed by its own drafter.
