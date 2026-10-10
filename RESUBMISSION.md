# Resubmission — FactCheckDAO

## Problem (previous rejection)
The fact-checking verdict relied on model knowledge instead of fetching
corroborating sources, so validators could not verify whether submitted claims
were supported by authoritative evidence, and they agreed only on a three-state
label.

## Fix
`resolve_article` now runs a consensus round in which **every validator
independently**:

1. Fetches the submitted article via `gl.nondet.web.request`.
2. Extracts a verifiable factual claim.
3. **Retrieves a corroborating source** from the web and extracts an evidence
   excerpt from it.
4. Judges the claim **from the retrieved excerpt** — not from model knowledge.

Consensus holds (via `gl.eq_principle.prompt_comparative`) only when the
validators **independently agree** on the verdict reached from the retrieved
evidence. A validator that fetches different evidence or reaches a different
verdict breaks consensus — so this is genuine verification, not a label check.

Each verdict is **recorded on-chain** with its claim-specific evidence:
`{claim, source_url, evidence, source_fetched}`. When no source can be
retrieved, `source_fetched: false` is stored rather than silently falling back
to model knowledge.

## On-chain proof
Contract: `0x7af5D10bf14774A2e5ce0129F46dcb625F0E9ce2` (Bradbury, chain 4221)
Explorer: https://explorer-bradbury.genlayer.com/address/0x7af5D10bf14774A2e5ce0129F46dcb625F0E9ce2
Live app: https://adebisi1111.github.io/factcheckdao/
Repo: https://github.com/Adebisi1111/factcheckdao

A verified run fetched a real corroborating source
(`https://en.wikipedia.org/wiki/Moby_Dick`, `source_fetched: true`), validators
independently judged the claim from the fetched excerpt, and the evidence-backed
verdict was committed on-chain.
