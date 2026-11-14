# Bootstrap → blessed corrections: 17-permit-portal-limits

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
The agent wrote `model.json` by hand against `source.md`, with no extraction
draft, because a draft is a paid model call. Applying these in reverse to
`model.json` reconstructs the agent's hand-written model.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:entity:inspector>process:permit-service>review-and-decide.authentication` | `one inspector login shared by all inspectors in a district` | `unknown`, the shared login kept in `description` and `notes` | The source states "All inspectors in a district use one shared inspector login." and names no mechanism behind the login. The field and the `authentication-mechanism` fact name a mechanism, so both read unknown. The `credential-sharing` fact holds the shared login. Ruled by the maintainer on 2026-11-14. |

## Signal

None yet. The first extraction run over this case gives the draft that a
reader can compare against `model.json`.
