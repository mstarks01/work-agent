# 131. The live-eval federation trusts the repository by its ID

- **Status**: accepted
- **Date**: 2026-11-21
- **Relates to**: [ADR 0021](0021-no-credential-bearing-job-runs-pr-head-code.md),
  whose attribute condition also pins the ref.

## Context

The Workload Identity Federation for the live Vertex lane trusts a GitHub OIDC
token only from one repository. `setup-workload-identity.sh` names that
repository in the attribute condition and in the service account binding.

A full name such as `<OWNER>/<REPO>` is not a stable identity. A rename or a
transfer changes it, and the trust then refuses every token from the
repository. A name can also come back: after a rename, the owner of the old
namespace can create a new repository with the old name, and a condition on
the name would trust that repository.

GitHub's OIDC token carries `repository_id`, the numeric ID of the repository.
A rename or a transfer does not change it.

## Decision

**The attribute condition and the service account binding name the
repository by `repository_id`.**

- The script reads the ID with `gh api repos/<OWNER>/<REPO> --jq .id` and stops
  when the result is not a number, so it never writes a condition with an
  empty ID.
- The attribute mapping holds `google.subject` and `attribute.repository_id`
  only, and the binding is the principal set
  `.../attribute.repository_id/<REPO_ID>`. No binding reads another attribute,
  so a binding on the name matches no principal.
- A re-run updates the mapping as well as the condition, so a provider that an
  earlier run created gets the new attribute.
- The condition keeps the `ref` and `job_workflow_ref` checks of ADR 0021.
- The eval service account is `katra-evals`.

## Considered options

- **Keep the name, and re-run the script after each rename.** Rejected: the
  lane fails until someone re-runs it, and a name that comes back is trusted
  in the meantime.
- **Name both the ID and the name.** Rejected: a rename then breaks the trust,
  and the ID alone already identifies one repository.

## Consequences

A rename or a transfer of the repository keeps the live lane working with no
change in Google Cloud.

A deployment that ran the script before this decision has the service account
`analysis-evals`, with `roles/aiplatform.user` and a binding on
`attribute.repository/<OWNER>/<REPO>`. A re-run creates `katra-evals`, binds it
to the ID, points `GCP_EVAL_SERVICE_ACCOUNT` at it, and replaces the mapping,
so the binding on the name matches no principal. Delete the account that
nothing uses with
`gcloud iam service-accounts delete analysis-evals@<PROJECT_ID>.iam.gserviceaccount.com`.
