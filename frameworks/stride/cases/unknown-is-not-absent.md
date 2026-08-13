# An Unknown Control Is Not a Missing One

## Pattern

A model shows a flow from a browser into an API across a boundary. The flow's `authentication` is the `unknown` sentinel: the submitted description simply never says how the caller is identified. No other element mentions a session, a token or a login.

## Considered

That any internet caller can act as any user, because the API authenticates nobody.

## Ruling

Accepted, as **needs-info** — not as a confirmed finding.

## Why

Two systems produce this same model: one that genuinely accepts anonymous requests, and one with a perfectly good login the writer did not think to mention. Nothing in the material distinguishes them, so a confirmed finding asserts something the evidence does not support, and dropping the threat hides a real risk in the case where the control is absent.

The conditional form is what carries both: name the attacker action, name the control whose state is unknown, and let the verdict record the gap. The same move applies to `encryption_at_rest` on a store and to transport protection on a link — anywhere the sentinel appears rather than a stated absence.

Compare a flow whose `authentication` reads `none; the queue is only reachable inside the cluster`. That is a *stated* absence with a stated reason, and a finding against it is confirmed rather than conditional, because the submitter has told you the control is not there.

## What decided it

The `unknown:` evidence reference for the attribute itself — the fact that the input never stated it, which is a fact about the material rather than about the system.
