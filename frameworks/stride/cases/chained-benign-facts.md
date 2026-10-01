# A Finding No Single Fact Supports

## Pattern

Three unremarkable facts in one model. An internet-facing worker holds a long-lived credential for a message queue. The queue feeds a processor that reads job definitions from a configuration store. The configuration store is written by the same processor.

## Considered

Nothing, on any fact taken alone. Each is ordinary and each has a plausible reason to be as it is.

## Ruling

Accepted as a conditional finding: a chain from the exposed worker to attacker-controlled job definitions, open on the one hop the model does not state.

## Why

Whoever compromises the exposed worker inherits its queue credential, publishes a message the processor treats as work, and — because the processor writes the configuration it later reads — reaches a store whose contents are effectively instructions. No individual element is misconfigured; the composition is what carries the risk.

This is the kind of finding deterministic rules cannot surface. A rule fires on a condition, and here no single condition is remarkable — the chain only exists when the flows are read together and the attacker's position is carried forward from one hop to the next. Candidates may have fired on two of the three hops for unrelated reasons, and following each in isolation would have produced two thin findings and missed this one.

Write the chain explicitly: the entry point, what is inherited at each hop, and the terminal capability. A chain asserted without its steps reads as speculation, and the critic will treat it as one.

One hop here is not a fact in the model. The model says the processor takes work from the queue and writes the configuration store. It does not say that what a message carries reaches what the processor writes. Name that hop as the open fact the chain rests on, so the finding is conditional on it.

## What decided it

The flows themselves, plus the crossing on the worker's inbound edge. Each step but one is a fact in the model, and the one that is not is named as open; the sequence is the analysis.
