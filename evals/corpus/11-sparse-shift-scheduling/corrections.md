# Bootstrap → blessed corrections: 11-sparse-shift-scheduling

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:scheduling-web-app-to-colleague:show-shifts` | present | removed | The candidate drew the colleague's request and the app's response as two flows. One interaction is one flow, direction = who initiates; the response rides implicitly. Third occurrence of this error in the corpus, after cases 05 and 06. |
| 2 | `flow:payroll-system-to-file-share:collect-payroll-export` | source `store:file-share`, destination `entity:payroll-system` | reversed | The source says the payroll system *collects* the export. The candidate pointed the flow the way the bytes move rather than the way the interaction is initiated. Same error as case 02 correction 5 and case 05 correction 2; three occurrences now, and all three are pull interactions. |
| 3 | `process:scheduling-web-app.technology` | `web application` | `unknown` | The source names no technology anywhere. Restating the element's own name as its technology reads as a fact and is not one. |
| 4 | `store:rota-database.data_classification` | `confidential` | `unknown` | The source states *what the database holds* — names, contact details, availability — and never states a classification. The candidate derived a label from the content, which is precisely the conflation this case exists to grade: the content drives the `pii` asset tag, and `data_classification` stays `unknown`. |
| 5 | `store:file-share.assets` | `[]` | `["pii"]` | The export is built from the same colleague data the database is tagged for. The candidate tagged the database, whose contents are described, but not the file derived from it, whose contents are not — asset tags were driven by what the element is *called*. |
| 6 | `assumptions` | one entry (exposure only) | two entries | The candidate placed `process:scheduling-web-app` in `boundary:internal-network` without recording it. The source lists the service, database and share as internal and never places the web app anywhere, so its zone is an inference and belongs in `assumptions` with a basis. An inference in an attribute but not in `assumptions` is a bug. |
| 7 | `boundary:payroll-team-environment.kind` | `network` | `tenant` | The source says the payroll team runs it and says nothing else about it. That one stated fact is a controlling party, not a network location, so the kind is `tenant`. A zone the source characterizes this thinly still takes the kind its description supports; `other` would discard the only thing the source says. |

## Signal

The two new patterns here are both about **provenance of a value the source
never gave**. Correction 4 is a derived label presented as a stated one, and
correction 6 is a derived zone with no record that it was derived — the same
failure at the level of an attribute and at the level of the assumptions list.
On a sparse input these dominate: when almost nothing is stated, the candidate's
errors stop being dropped facts and become invented ones, which is the opposite
failure mode from the attribute-stranded-in-`source_excerpt` pattern that leads
cases 02, 03 and 05. Corrections 1 and 2 are the familiar direction and
one-interaction-one-flow errors, and correction 2 makes pull interactions three
for three — the strongest single pattern the corpus has.

## Rulings of 2026-09-16 (#961 step 3)

The step 3 review on #961 ruled on the drafted `facts.json` and its disputed
values. The review is assistant-authored and the maintainer posted it; it is
not a human signature, and no row in `facts.json` is signed. Each edit below
follows one ruling, and `facts.json` changed with it.

| # | Path | Before | After | Ruling |
|---|---|---|---|---|
| 8 | `flow:scheduling-web-app-to-scheduling-service:rota-requests.operations` | `read-write`, no assumption | `read-write` + assumption | The interaction is described only as "talks to". Managers build rotas through the app and colleagues view shifts through it, and the service reads and writes the rotas, so the app's path carries both. A supported inference, and its basis is now visible. |
| 9 | `process:scheduling-service.exposure` | `internal` | `unknown` | Network placement alone does not establish the absence of internet ingress. The internal-network membership stays stated. |

## Signing of 2026-09-16 (#961 step 3)

The maintainer signed every row of `facts.json` in a session, ruling on each
against `source.md`. Five values changed in `model.json` with the rulings:

| # | Path | Before | After | Ruling |
|---|---|---|---|---|
| 10 | `process:scheduling-web-app.exposure` | `internet-facing` + assumption | `unknown` | Access from personal phones does not establish that the web app is internet-facing. |
| 11 | `process:scheduling-web-app.trust_zone` | inferred assumption | placeholder assumption | The placement of the service, the database and the share does not establish the web app's placement. |
| 12 | `entity:colleague.trust_zone` | no assumption | placeholder assumption | Personal phones and use of the app do not establish internet access; an internal connection or a VPN remains possible. |
| 13 | `entity:store-manager.trust_zone` | no assumption | placeholder assumption | The same. |
| 14 | `entity:payroll-system.trust_zone` | no assumption | placeholder assumption | Operation by another team does not establish a separate network or trust zone. |

"Run by another team" establishes operational responsibility, not a distinct
owner or tenant, so the reference reads ownership unknown and keeps that
statement. The zone's `kind` of `tenant` is a graph-owned modelling
judgement the ruling did not reach.
