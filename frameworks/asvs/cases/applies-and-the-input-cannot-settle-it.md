# The Requirement Applies and the Prose Cannot Settle It

## Pattern

A model shows a browser talking to an API across a boundary. The flow's `authentication` reads `password`, because the description says that customers sign in with a password. The description never says what rules a password must meet. The scope line rules the level 1 password requirements in.

## Considered

That the application fails the password-length requirement, because nothing in the input shows a minimum being enforced.

## Ruling

Accepted, as **needs-info** — and never as a pass or a fail.

## Why

This is the ordinary ASVS outcome here, and getting it right is most of what this framework does in this service.

The requirement *applies*: the input states a password login, so the service rules 6.2.1 in, and saying so is a real answer. What cannot be reached is verification. ASVS verification needs the source, the configuration and the people who built the system; a job here carries prose. So `confirmed` would assert a failure the material does not support, and dropping the ruling would hide an applicable requirement behind silence.

The conditional form carries both facts: the requirement applies to this system, and the input does not show it satisfied. That is a finding a reader can act on — it names exactly what to go and look at.

Note what is *not* claimed. Nothing here says the application has a short password minimum. It says the requirement is in scope and unanswered.

## What decided it

The stated password login is what made the requirement applicable, through the applicability rule the service reads; a level never does that alone. The ruling quotes the sentence that states the login. No source states the password rules, and that silence is what left the ruling unsettled — a fact about the material rather than about the system.
