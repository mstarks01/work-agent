# Bootstrap → blessed corrections: 12-overclaiming-supplier-portal

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `store:document-store.encryption_at_rest` | `encrypted` | `unknown` | Taken from the datasheet's "enterprise-grade encryption throughout". That is a marketing claim about the platform, not a stated property of this store. A control an analyst will treat as present must be one the source actually asserted about the thing it is attached to. |
| 2 | `flow:supplier-to-supplier-portal:upload-documents.encryption_in_transit`, `.protocol` | `encrypted (TLS)`, `HTTPS` | `unknown`, `unknown` | Same claim, applied to a flow, and it dragged an invented protocol along with it. The source names no protocol anywhere. |
| 3 | `flow:portal-vendor-to-landing-bucket:push-nightly-extract.encryption_in_transit` | `encrypted end to end` | `unknown` | The candidate took the sentence it read first and never registered that the next sentence contradicts it. Neither statement is privileged, nothing in the source resolves them, so the value is `unknown` and the conflict is recorded in the flow's `notes`. Resolving a contradiction silently is worse than either value, because the reader cannot see that there was one. |
| 4 | `flow:category-manager-to-supplier-portal:review-documents.authentication` | `fully authenticated and audited` | `unknown` | The datasheet phrase copied verbatim into an attribute an analyst reads as a control. The source states how *suppliers* sign in and never states how category managers do; the presence of one real stated control nearby is what makes this error easy to make. |
| 5 | `store:audit-log` and `flow:supplier-portal-to-audit-log:write-audit-records` | present | removed | The candidate invented an element and a flow out of the word "audited". This is the most damaging shape of over-claim: a fabricated element gives every analyst something to cite, so a repudiation finding gets closed against a store that does not exist. |
| 6 | `store:supplier-database.data_classification`, `store:landing-bucket.data_classification` | `confidential` | `unknown` | Derived from "fully compliant". A compliance adjective is not a classification, and the source classifies nothing. |
| 7 | `entity:portal-vendor` | absent | present | The vendor was modelled only as a boundary, with the nightly push sourced from `process:supplier-portal`. The source states the vendor pushes, and the vendor is an actor outside our control that initiates into our cloud account — dropping it removes the case's sharpest external entity. |
| 8 | `boundary:vendor-platform.kind` | `network` | `tenant` | "The vendor hosts it and we do not run any part of it" states a controlling party, and the zone's whole point is that the party differs rather than the network. This is the one boundary in the case that suppliers and category managers both cross into, so its kind is what tells the elevation-of-privilege lane that the crossing leaves our control. |

## Signal

Every correction here is the same failure in a different place: **assurance
language read as stated fact**. Corrections 1, 2, 4 and 6 turn adjectives into
attribute values, and correction 5 turns an adjective into an element — the
worst of the set, because an invented element is citable and therefore
propagates into findings rather than merely weakening one. Correction 3 is the
distinct one and the reason this case exists: given two statements that cannot
both be true, the candidate silently picked one, which is the failure a reader
cannot detect afterwards.

Set against case 11, the pair separates two opposite pressures on the same
mechanism. On a sparse input the candidate invents values to fill gaps; on a
dense one it adopts values the text supplies but never establishes. Both produce
a confident non-`unknown` attribute the source does not support, so both are
graded by the same `unknown`/assumption distinction — the corpus now has a case
pulling on each side of it. Correction 7 is the familiar dropped-actor error
from cases 02 and 04, and it landed on the actor the reader is least inclined to
model, the one on the far side of an organizational boundary.
