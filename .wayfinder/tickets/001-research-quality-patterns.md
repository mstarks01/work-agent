---
id: 001
title: "Research: multi-agent quality patterns for threat modeling"
label: wayfinder:research
status: closed
assignee: claude (research subagent)
blocked-by: []
---

## Question

What does current (2024–2026) research and practitioner evidence say about which multi-agent quality pattern most robustly improves LLM analysis quality for tasks like STRIDE threat modeling — single strong model with self-critique, reviewer/reviser (generator–critic), multi-agent debate, consensus/ensemble voting (e.g., per-STRIDE-category specialists merged), or mixture-of-agents? Specifically: which patterns improve recall of real threats vs inflate false positives, what the token-cost/quality trade-offs are, and where diminishing returns set in. Include any published evaluations of LLM-based threat modeling specifically.

## Context pointer

Findings land on branch `research/quality-patterns`.

## Resolution

Resolved 2026-07-18 by research subagent. Full findings: `docs/research/quality-patterns.md` on branch `research/quality-patterns` (commit 1d31c9b).

- Intrinsic self-critique (same context) *reduces* accuracy (arXiv:2310.01798 + 2026 follow-ups); critique needs a separate context with external anchors.
- Generator–critic has the strongest evidence (CriticGPT arXiv:2407.00215; Semgrep triage kills ~20% FPs at 92-96% reviewer agreement); ~2-3x cost, tunable precision/recall.
- Multi-agent debate: worst tokens-per-quality (ICML 2024 "Should we be going MAD?"); often loses to self-consistency; skip.
- Ensemble voting: non-monotonic, optimum k≈3-5; for threat lists use union-then-critic-filter, not voting.
- Self-MoA beats mixed MoA (arXiv:2502.00674) — sample the best model, put the strong tier on the critic.
- Threat-modeling evals (arXiv:2411.17058, arXiv:2505.04101): off-the-shelf recall <0.30; structure + few-shot exemplars drive gains (P/R 0.73 achievable), not topology; unfiltered findings ~86% FP.
- Recommendation: STRIDE-per-element fan-out + one grounded critic pass (max 1-2 iterations, strongest tier), optional k=2-3 Self-MoA on high-risk elements; no debate, no mixed MoA. ~2-4x token budget.
