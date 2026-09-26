# Reading decisions: 14-loyalty-oauth-platform

No extraction draft exists for this case. An agent wrote `model.json` by hand,
sentence by sentence against `source.md`, so there is no bootstrap value to
reverse. The table records the decisions a correction pass would otherwise
have made, so that a reader can disagree with each one specifically.

| # | Path | Value | Why (source text) |
|---|---|---|---|
| 1 | `flow:…>token-requests` (mobile) `.authentication` | PKCE stays unstated | "Nobody wrote down whether the app sends a PKCE challenge." An inferred PKCE would answer ASVS V10.4.6 with a pass. |
| 2 | `flow:…>call-rewards-api` (both) `.authentication` | signature checked, audience not | "It does not check which client or which audience a token was issued for." The qualifier sits in the attribute a lane reads, not only in the excerpt. |
| 3 | `process:authorization-server.description` | prefix-matched redirect URIs | "accepts any redirect URI that starts with it." Kept on the process that does the matching, and cited by the redirect flow's `authentication`. |
| 4 | `process:authorization-server.notes` | ten-minute codes; thirty-day cookie, no idle timeout | Both are stated values, and level 3 is what makes the first a gap. |
| 5 | `store:identity-database` | one store | The source keeps accounts and grants "in the identity database, together with" each other, so they are one element. |
| 6 | `process:support-console` | no inbound human flow | The source names no user of the console. Its trust in the shared signing key is the fact the case turns on. |
| 7 | `*.encryption_at_rest`, store flows' `protocol` and `authentication` | `unknown`, with notes | "Nobody recorded how the rewards API connects to the ledger, how the support console connects to the identity database, or how any of the three stores is protected at rest." A gap somebody registered, which `notes` keeps. |
| 8 | `assumptions` | three | The member's zone, and the internet exposure of the authorization server and the rewards API, are inferred and not stated. |

## Signal

The case is built so that its worst finding needs three stated facts at once:
no audience check, one signing key for two services, and one public hostname.
A lane that reads each flow alone sees a signed token and a checked signature,
and finds nothing.
