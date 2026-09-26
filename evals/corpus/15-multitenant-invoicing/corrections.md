# Reading decisions: 15-multitenant-invoicing

No extraction draft exists for this case. An agent wrote `model.json` by hand,
sentence by sentence against `source.md`, so there is no bootstrap value to
reverse. The table records the decisions a correction pass would otherwise
have made, so that a reader can disagree with each one specifically.

| # | Path | Value | Why (source text) |
|---|---|---|---|
| 1 | `trust_boundaries` | no tenant boundary | Every company shares "one billing app, one database and one PDF bucket". The tenants are rows, not zones, so a tenant boundary would invent a separation the system does not have. |
| 2 | `process:billing-app.notes` | two checks unstated | "Nobody wrote down whether it also checks that the invoice belongs to the user's company" and "whether the billing app checks the user's role again". Neither is written as present or absent. |
| 3 | `flow:…>open-payment-page.authentication` | none; counting invoice number | "asks the payer to sign in to nothing" and "Invoice numbers count up from 1000 across all companies." Both qualifiers sit in the attribute a lane reads. |
| 4 | `flow:…>open-payment-page.protocol` | `HTTPS`, noted as inferred | The source states HTTPS for users' browsers and does not repeat it for the payer's link. |
| 5 | `flow:…>manage-invoices.authentication` | no second factor | "There is no second factor, for admins or viewers." |
| 6 | `process:pdf-renderer.description` | trusts the job's company ID | "It trusts the company ID the job carries." |
| 7 | `process:admin-console.exposure` | `internal` | "reachable only from there", the support office network. Where it runs is an assumption. |
| 8 | store flows, `*.encryption_at_rest` | `unknown`, with notes | "Nobody recorded how the billing app and the renderer reach the database, the queue or the bucket, or how the database and the bucket are protected at rest." |

## Signal

The two worst findings, enumeration on the payment page and a cross-tenant
invoice load, cross no zone this model can show. A lane that reads only
boundary crossings finds neither.
