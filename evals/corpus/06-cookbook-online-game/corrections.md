# Bootstrap → blessed corrections: 06-cookbook-online-game

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `process:game-client` | typed as `ExternalEntity` | `Process` | Defensible either way, and the blessing decided it deliberately: the client is the operator's own code, so it is a Process — but one sitting in a zone the operator does not control, which is exactly what the trust boundary is for. Typing it as an External Entity would have discarded the model's ability to file threats about what the client itself does. |
| 2 | `flow:game-client-to-lobby:matchmaking.authentication`, `flow:game-client-to-game-servers:gameplay-traffic.authentication` | `TCP` | `unknown` | The bootstrap put the transport into the authentication field. A protocol is not a credential, and an analyst reading `TCP` under `authentication` may treat the link as authenticated. |
| 3 | `process:lobby.exposure`, `process:game-servers.exposure` | `unknown` | `internet-facing` + assumption | The source states both must be reachable "from wherever a player is, so they are exposed". Under-reporting here would have removed the boundary crossing that carries half this case's must-find threats. |
| 4 | `boundary:player-local-machine.kind` | `network` | `other` | The boundary is ownership of physical hardware, not a network segment. The bootstrap defaulted every boundary to `network`. |
| 5 | `flow:moderation-website-to-player-database:read-write-players` | (absent) | present | Dropped along with the whole moderation sub-path on the first pass; recovered by walking the source text paragraph by paragraph rather than following the main data path. Same failure as case 02's correction 4. |
| 6 | `store:stats-database.assets` | `[]` | `["business-critical-data"]` | Match statistics are the competitive record the game's integrity rests on; the bootstrap tagged only the store with "player" in its name. |

## Signal

Correction 2 is a new and specific failure: **a transport value written into the
`authentication` field**, which is worse than leaving it `unknown` because it
reads as a control that was verified. Correction 3 is the corpus's clearest case
of under-reporting costing threats outright — without `internet-facing`, no
boundary crossing exists on the client links and the two highest-severity
findings lose their grounding. Correction 5 is the third occurrence of
**elements dropped when they sit off the main narrative path** (cases 02, 06),
which is now the most repeated *structural* extraction failure in the corpus,
alongside the most repeated *attribute* failure (facts stranded in
`source_excerpt`, cases 02, 03, 05).
