# Bootstrap → blessed corrections: 13-dispatch-control-plane

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `boundary:production-control-plane.kind` | `network` | `privilege` | The bootstrap read "the production control plane" as a second network segment and typed it the way it types every other segment. The source answers the prompt's second question outright — same company, same staff, and an account there can move a crew where a corporate laptop holds no such right — so the zone separates authority rather than location. This is the correction the case exists for: it is what makes the crossings into the zone a privilege transition instead of a network hop. |
| 2 | `flow:dispatch-console-to-dispatch-api:dispatch-requests.protocol`, `.encryption_in_transit` | `HTTPS`, `encrypted (TLS)` | `unknown`, `unknown` | A cross-origin browser call reads as HTTPS to anyone who has seen one, and the source never says so. The same sentence states the origins differ and states that the CORS configuration was not recorded; neither statement is about transport. An invented `encrypted` here would suppress the tampering and information-disclosure findings on the one flow that carries crew moves. |
| 3 | `flow:dispatch-console-to-dispatch-api:live-job-status.protocol` | `WebSocket over wss` | `WebSocket` | The bootstrap supplied the scheme the sentence never carries. The source states the socket exists and states that nobody wrote down whether it runs over TLS, so the protocol is the bare channel and `encryption_in_transit` stays `unknown`. Writing `wss` would have answered the ASVS V4.4.1 record with a pass, which is the one verdict this service never reports. |
| 4 | `flow:schedule-importer-to-scheduling-partner:pull-schedule-feed` | `entity:scheduling-partner` → `process:schedule-importer` | `process:schedule-importer` → `entity:scheduling-partner` | The bootstrap pointed the flow the way the data travels, because the partner "publishes" the work. The importer pulls every hour, so the importer initiates and the document rides the response. |
| 5 | `process:schedule-importer.technology` | `unknown` | `parses the partner's SOAP feed as an XML document; no product named` | The bootstrap dropped a stated fact because no product name sat beside it. The source states what the importer parses and in what form, and that is the whole of what the parser rules read. A stated technology with no vendor is still a stated technology. |
| 6 | `process:dispatch-console.exposure` | `internet-facing` | `unknown` | The bootstrap inferred exposure from "browser". Engineers reach the console from the corporate network and the source places the console nowhere else, so nothing states whether it is reachable from outside. |
| 7 | `process:dispatch-api.exposure` and `assumptions[0]` | `internal`, no assumption | `internal`, assumption recorded | The value is right and the record was missing. The source states the control plane is not reachable from the internet and states nothing about the API's own exposure, so the value is inferred from the zone and belongs in `assumptions` with that basis. |
| 8 | `store:dispatch-database.encryption_at_rest` | `unknown`, no note | `unknown`, note added | The value was already right, and the *kind* of unknown was lost. The source states the at-rest protection is not written down anywhere, which is a gap somebody probed rather than a topic nobody raised, and `notes` is the only field that keeps the difference. |
| 9 | `store:audit-log` and `flow:dispatch-api-to-audit-log:write-job-history` | present | removed | The bootstrap turned "every job order carries the token or the session that created it" into an audit store and a flow to it. The sentence describes a column on the job order, not a second element. An invented element is citable, so a repudiation finding would have been closed against a store this system does not have. |

## Signal

Two failures, and they pull in opposite directions on the same source. The
first is **the familiar zone made from a network habit** — correction 1 — where
the bootstrap had every fact it needed and reached for the value it writes most
often. The second is **the scheme supplied from the shape of the thing**:
corrections 2, 3 and 6 all add a security property nobody stated, each one
plausible from a single word in the text (`cross-origin`, `WebSocket`,
`browser`). Correction 3 is the sharpest, because `wss` would have converted an
unknown into a pass on the ASVS record that reads it.

Against the rest of the corpus this case moves the trust hand-off inward. Cases
11 and 12 hand data to a party we do not run; here every party is ours and the
authority still changes, which is the reading `kind` was given four values to
carry. Correction 4 repeats case 07's reversed pull and correction 9 repeats
case 12's invented audit store — both landed on the elements a reader is most
inclined to fill in for the author.
