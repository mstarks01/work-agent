# The Requirement Applies and the Prose Cannot Settle It

## Pattern

A model shows a browser talking to an API across a boundary. The flow's `authentication` is the `unknown` sentinel. The description mentions user accounts but never says how a password is handled. The level 1 password requirements are in the selected set.

## Considered

That the application fails the password-length requirement, because nothing in the input shows a minimum being enforced.

## Ruling

Accepted, as **needs-info** — and never as a pass or a fail.

## Why

This is the ordinary ASVS outcome here, and getting it right is most of what this framework does in this service.

The requirement *applies*: the system authenticates people with passwords, so 6.2.1 is in scope and saying so is a real answer. What cannot be reached is verification. ASVS verification needs the source, the configuration and the people who built the system; a job here carries prose. So `confirmed` would assert a failure the material does not support, and dropping the ruling would hide an applicable requirement behind silence.

The conditional form carries both facts: the requirement applies to this system, and the input does not show it satisfied. That is a finding a reader can act on — it names exactly what to go and look at.

Note what is *not* claimed. Nothing here says the application has a short password minimum. It says the requirement is in scope and unanswered.

## What decided it

The flow's `unknown` authentication value, cited as the `unknown:` reference for that attribute — a fact about the material rather than about the system. The requirement's presence in the level's set is what made it applicable; the sentinel is what left it unsettled.
