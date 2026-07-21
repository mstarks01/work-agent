# Bootstrap → blessed corrections: 04-ml-inference-service

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `store:feature-store.encryption_at_rest` | `none` | `unknown` | The source says Redis has no *password*. The bootstrap generalized one stated absence into a second, different absence it was never told about. An invented absence is as wrong as an invented control, and it is more dangerous: analysts file confident findings on it. |
| 2 | `store:model-registry` | typed as `Process` | `DataStore` | "Registry" read as a service. It is described only as a bucket that artifacts are read from and written to. |
| 3 | `flow:model-server-to-model-registry:load-artifact` | (absent) | present | The bootstrap folded artifact loading into the model server's `description` instead of modelling it as a flow, which would have left the case's headline threat with no flow to attach to. |
| 4 | `store:inference-log.assets` | `[]` | `["pii"]` | Named as a debugging log, so the bootstrap treated it as operational data despite the text stating it holds whatever end users typed. |
| 5 | `entity:ml-engineer.trust_zone` | `boundary:public-internet` | `boundary:model-network` + assumption | Neither zone is stated. The bootstrap picked one silently; blessed picks the one the text better supports and records the inference. |
| 6 | `flow:calling-service-to-inference-gateway:submit-inference-request.authentication` | `API key` | `per-team API key in a header, never expired or rotated` | "We have never expired one" is the fact that raises this from routine to must-find. |

## Signal

Correction 1 is the first **invented absence** in the corpus and the most
worrying single data point: every other case's failures over-report controls,
this one under-reports them, and both directions produce confident analyst
findings on facts the user never gave. Correction 2 shows type assignment
following the *name* rather than the described behaviour, and correction 3 shows
an interaction being demoted into prose — the extraction failure that silently
deletes a threat surface, since analysts can only file against elements that
exist.
